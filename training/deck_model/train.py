"""
training/deck_model/train.py
Trains a deckbuilding model on arrays produced by prepare.py: given a pool, the
probability that each card makes the maindeck. Decks are weighted by the
drafter's win rate bucket and by how the deck itself performed.

Architecture (same numpy-friendly shape as the pick model, minus pick position):
    h      = relu(pool_counts @ pool_w + pool_b)
    h      = relu(h @ hidden_w + hidden_b)
    p_card = sigmoid(h @ card_emb.T + card_bias)

Usage:
    python training/deck_model/train.py <decks.npz> <out_model.npz> [<17lands_set_file.json>]
"""

import sys
import json
import time
import datetime
import numpy as np
import torch
import torch.nn as nn

HIDDEN = 128
EPOCHS = 12
BATCH = 1024
LR = 2e-3
VALIDATION_GROUPS = 5


class DeckModel(nn.Module):
    def __init__(self, n_cards: int, hidden: int = HIDDEN):
        super().__init__()
        self.pool_emb = nn.Linear(n_cards, hidden)
        self.hidden = nn.Linear(hidden, hidden)
        self.card_emb = nn.Parameter(torch.randn(n_cards, hidden) * 0.05)
        self.card_bias = nn.Parameter(torch.zeros(n_cards))

    def forward(self, pool):
        h = torch.relu(self.pool_emb(pool))
        h = torch.relu(self.hidden(h))
        return h @ self.card_emb.T + self.card_bias


def deck_weights(data) -> np.ndarray:
    drafter = np.clip((data["win_rate"] - 0.40) * 5.0, 0.25, 1.75)
    deck = 0.5 + data["wins"] / np.maximum(1, data["games"])
    return (drafter * deck).astype(np.float32)


def top_k_overlap(scores: np.ndarray, pool: np.ndarray, deck: np.ndarray) -> float:
    """Picks as many copies as the real deck has, best-scored first; returns overlap share."""
    total = hits = 0
    for s, p, d in zip(scores, pool, deck):
        k = int(d.sum())
        copies = np.repeat(np.arange(len(p)), p)
        chosen = copies[np.argsort(-s[copies], kind="stable")[:k]]
        picked = np.bincount(chosen, minlength=len(p))
        hits += np.minimum(picked, d).sum()
        total += k
    return hits / max(1, total)


def load_card_info(set_file, card_names):
    if not set_file:
        return None
    by_name = {}
    for card in json.load(open(set_file, encoding="utf-8"))["card_ratings"].values():
        wr = card.get("deck_colors", {}).get("All Decks", {}).get("gihwr", 0.0) or 0.0
        if wr >= by_name.get(card["name"], (0.0,))[0]:
            by_name[card["name"]] = (wr, set(card.get("colors", [])))
    return [by_name.get(n, (0.0, set())) for n in card_names]


def gihwr_baseline(info, pool, deck):
    """Top GIH WR cards restricted to the real deck's colors (it knows the answer's colors)."""
    wr = np.array([i[0] for i in info], np.float32)
    scores = np.zeros(pool.shape, np.float32)
    for row, d in enumerate(deck):
        colors = set().union(*(info[c][1] for c in np.nonzero(d)[0]))
        castable = np.array([i[1] <= colors for i in info])
        scores[row] = np.where(castable, wr, wr - 100.0)
    return top_k_overlap(scores, pool, deck)


def main(data_path, out_path, set_file=None):
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    data = dict(np.load(data_path))
    card_names = data["card_names"]
    pool_all, deck_all = data["pool"].astype(np.float32), data["deck"].astype(np.float32)
    target_all = np.divide(deck_all, pool_all, out=np.zeros_like(deck_all), where=pool_all > 0)
    weights = deck_weights(data)

    val_mask = data["group"] < VALIDATION_GROUPS
    train_idx, val_idx = np.where(~val_mask)[0], np.where(val_mask)[0]
    print(f"{len(card_names)} cards | train {len(train_idx):,} decks | validation {len(val_idx):,}")

    model = DeckModel(len(card_names))
    optim = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        optim, max_lr=LR, total_steps=EPOCHS * (len(train_idx) // BATCH + 1)
    )
    bce = nn.BCEWithLogitsLoss(reduction="none")

    def scores_for(idx):
        model.eval()
        with torch.no_grad():
            return model(torch.from_numpy(pool_all[idx])).numpy()

    for epoch in range(EPOCHS):
        model.train()
        t0, total = time.time(), 0.0
        order = rng.permutation(train_idx)
        for start in range(0, len(order), BATCH):
            rows = order[start : start + BATCH]
            pool = torch.from_numpy(pool_all[rows])
            target = torch.from_numpy(target_all[rows])
            mask = (pool > 0).float()
            w = torch.from_numpy(weights[rows])[:, None] * mask
            loss = (bce(model(pool), target) * w).sum() / w.sum()
            optim.zero_grad()
            loss.backward()
            optim.step()
            sched.step()
            total += loss.item() * len(rows)
        overlap = top_k_overlap(scores_for(val_idx), data["pool"][val_idx], data["deck"][val_idx])
        print(
            f"epoch {epoch + 1}/{EPOCHS} loss {total / len(train_idx):.4f} | val deck overlap {overlap:.3f} | {time.time() - t0:.0f}s",
            flush=True,
        )

    final = {"deck_overlap": overlap}
    info = load_card_info(set_file, card_names)
    if info:
        final["gihwr_baseline_overlap"] = gihwr_baseline(info, data["pool"][val_idx], data["deck"][val_idx])
    print("final:", {k: round(v, 4) for k, v in final.items()})

    sd = {k: v.detach().numpy() for k, v in model.state_dict().items()}
    np.savez_compressed(
        out_path,
        card_names=card_names,
        pool_w=sd["pool_emb.weight"].T.astype(np.float32),
        pool_b=sd["pool_emb.bias"].astype(np.float32),
        hidden_w=sd["hidden.weight"].T.astype(np.float32),
        hidden_b=sd["hidden.bias"].astype(np.float32),
        card_emb=sd["card_emb"].astype(np.float32),
        card_bias=sd["card_bias"].astype(np.float32),
        metadata=np.array(
            json.dumps(
                {
                    "source": str(data_path),
                    "trained": datetime.date.today().isoformat(),
                    "decks": int(len(train_idx)),
                    **{k: round(float(v), 4) for k, v in final.items()},
                }
            )
        ),
    )
    print(f"saved {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
