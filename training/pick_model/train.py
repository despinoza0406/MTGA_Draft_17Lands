"""
training/pick_model/train.py
Trains a context-aware pick model on arrays produced by prepare.py and exports
numpy weights that src/advisor/pick_model.py can run without PyTorch.

Architecture (kept shallow so inference is a few numpy matmuls):
    h      = relu(pool_counts @ E_pool + E_pos[pick_position] + b1)
    h      = relu(h @ W2 + b2)
    score  = h @ E_card.T + b_card        (masked to the cards in the pack)

Picks are weighted by the drafter's game win rate bucket so the model learns
from winning players more than from average ones.

Training needs torch and pandas, which are not app dependencies:
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install pandas

Usage:
    python training/pick_model/train.py <picks.npz> <out_model.npz> [<17lands_set_file.json>]

Copy the output to <tool folder>/Models/<SET>_<EVENT>_pick_model.npz.
"""

import sys
import json
import time
import datetime
import numpy as np
import torch
import torch.nn as nn

HIDDEN = 128
EPOCHS = 6
BATCH = 4096
LR = 2e-3
VALIDATION_GROUPS = 5  # % of drafts held out
TOP_DRAFTER_WR = 0.60


class PickModel(nn.Module):
    def __init__(self, n_cards: int, n_positions: int, hidden: int = HIDDEN):
        super().__init__()
        self.pool_emb = nn.Linear(n_cards, hidden, bias=True)
        self.pos_emb = nn.Embedding(n_positions, hidden)
        self.hidden = nn.Linear(hidden, hidden)
        self.card_emb = nn.Parameter(torch.randn(n_cards, hidden) * 0.05)
        self.card_bias = nn.Parameter(torch.zeros(n_cards))

    def forward(self, pool, position, pack):
        h = torch.relu(self.pool_emb(pool) + self.pos_emb(position))
        h = torch.relu(self.hidden(h))
        scores = h @ self.card_emb.T + self.card_bias
        return scores.masked_fill(pack <= 0, -1e9)


def pick_weights(win_rate: np.ndarray) -> np.ndarray:
    return np.clip((win_rate - 0.40) * 5.0, 0.25, 1.75).astype(np.float32)


def batches(idx, rng, shuffle):
    order = rng.permutation(idx) if shuffle else idx
    for start in range(0, len(order), BATCH):
        yield np.sort(order[start : start + BATCH])


def to_tensors(data, rows):
    return (
        torch.from_numpy(data["pool"][rows].astype(np.float32)),
        torch.from_numpy(data["position"][rows].astype(np.int64)),
        torch.from_numpy(data["pack"][rows].astype(np.float32)),
        torch.from_numpy(data["pick"][rows].astype(np.int64)),
    )


def evaluate(model, data, idx, gihwr=None):
    model.eval()
    hits = top3 = base_hits = top_hits = top_n = 0
    with torch.no_grad():
        for rows in batches(idx, None, False):
            pool, pos, pack, pick = to_tensors(data, rows)
            scores = model(pool, pos, pack)
            pred = scores.argmax(1)
            hits += (pred == pick).sum().item()
            top3 += (scores.topk(3, 1).indices == pick[:, None]).any(1).sum().item()
            strong = torch.from_numpy(data["win_rate"][rows] >= TOP_DRAFTER_WR)
            top_hits += ((pred == pick) & strong).sum().item()
            top_n += strong.sum().item()
            if gihwr is not None:
                base = torch.from_numpy(gihwr)[None, :].expand_as(pack).masked_fill(pack <= 0, -1e9)
                base_hits += (base.argmax(1) == pick).sum().item()
    n = len(idx)
    out = {"top1": hits / n, "top3": top3 / n, "top1_top_drafters": top_hits / max(1, top_n)}
    if gihwr is not None:
        out["gihwr_baseline_top1"] = base_hits / n
    return out


def load_gihwr(set_file, card_names):
    if not set_file:
        return None
    ratings = json.load(open(set_file, encoding="utf-8"))["card_ratings"]
    by_name = {}
    for card in ratings.values():
        wr = card.get("deck_colors", {}).get("All Decks", {}).get("gihwr", 0.0) or 0.0
        by_name[card["name"]] = max(wr, by_name.get(card["name"], 0.0))
    return np.array([by_name.get(n, 0.0) for n in card_names], dtype=np.float32)


def main(data_path, out_path, set_file=None):
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    data = dict(np.load(data_path))
    card_names = data["card_names"]
    weights = pick_weights(data["win_rate"])
    picks_per_pack = int(data["pick_number"].max()) + 1
    n_packs = int(data["pack_number"].max()) + 1
    data["position"] = (
        data["pack_number"].astype(np.int16) * picks_per_pack + data["pick_number"]
    ).astype(np.int16)

    val_mask = data["group"] < VALIDATION_GROUPS
    train_idx, val_idx = np.where(~val_mask)[0], np.where(val_mask)[0]
    print(f"{len(card_names)} cards | {n_packs}x{picks_per_pack} picks | train {len(train_idx):,} | validation {len(val_idx):,}")

    model = PickModel(len(card_names), n_packs * picks_per_pack)
    optim = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        optim, max_lr=LR, total_steps=EPOCHS * (len(train_idx) // BATCH + 1)
    )
    loss_fn = nn.CrossEntropyLoss(reduction="none")

    for epoch in range(EPOCHS):
        model.train()
        t0, total, seen = time.time(), 0.0, 0
        for rows in batches(train_idx, rng, True):
            pool, pos, pack, pick = to_tensors(data, rows)
            w = torch.from_numpy(weights[rows])
            loss = (loss_fn(model(pool, pos, pack), pick) * w).sum() / w.sum()
            optim.zero_grad()
            loss.backward()
            optim.step()
            sched.step()
            total += loss.item() * len(rows)
            seen += len(rows)
        metrics = evaluate(model, data, val_idx)
        print(
            f"epoch {epoch + 1}/{EPOCHS} loss {total / seen:.4f} | val top1 {metrics['top1']:.3f} "
            f"top3 {metrics['top3']:.3f} top-drafters {metrics['top1_top_drafters']:.3f} | {time.time() - t0:.0f}s",
            flush=True,
        )

    final = evaluate(model, data, val_idx, load_gihwr(set_file, card_names))
    print("final:", {k: round(v, 4) for k, v in final.items()})

    sd = {k: v.detach().numpy() for k, v in model.state_dict().items()}
    np.savez_compressed(
        out_path,
        card_names=card_names,
        pool_w=sd["pool_emb.weight"].T.astype(np.float32),
        pool_b=sd["pool_emb.bias"].astype(np.float32),
        pos_emb=sd["pos_emb.weight"].astype(np.float32),
        hidden_w=sd["hidden.weight"].T.astype(np.float32),
        hidden_b=sd["hidden.bias"].astype(np.float32),
        card_emb=sd["card_emb"].astype(np.float32),
        card_bias=sd["card_bias"].astype(np.float32),
        metadata=np.array(
            json.dumps(
                {
                    "source": str(data_path),
                    "trained": datetime.date.today().isoformat(),
                    "picks": int(len(train_idx)),
                    "picks_per_pack": picks_per_pack,
                    **{k: round(v, 4) for k, v in final.items()},
                }
            )
        ),
    )
    print(f"saved {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
