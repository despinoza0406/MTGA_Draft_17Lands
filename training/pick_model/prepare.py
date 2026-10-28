"""
training/pick_model/prepare.py
Converts a 17Lands public draft dump (draft_data_public.<SET>.<EVENT>.csv.gz)
into compact numpy arrays for train.py.

Usage:
    python training/pick_model/prepare.py <draft_data.csv.gz> <out.npz>
"""

import sys
import zlib
import numpy as np
import pandas as pd

CHUNK_ROWS = 200_000


def main(csv_path: str, out_path: str):
    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    pack_cols = [c for c in header if c.startswith("pack_card_")]
    pool_cols = [c for c in header if c.startswith("pool_")]
    card_names = [c[len("pack_card_") :] for c in pack_cols]
    assert card_names == [c[len("pool_") :] for c in pool_cols], "pack/pool columns differ"
    name_to_idx = {n: i for i, n in enumerate(card_names)}

    meta_cols = [
        "draft_id",
        "pack_number",
        "pick_number",
        "pick",
        "user_game_win_rate_bucket",
        "user_n_games_bucket",
    ]
    dtypes = {c: np.int8 for c in pack_cols + pool_cols}
    dtypes.update({"pack_number": np.int8, "pick_number": np.int8})

    packs, pools, picks, pack_numbers, pick_numbers = [], [], [], [], []
    win_rates, n_games, groups = [], [], []
    skipped = 0
    reader = pd.read_csv(
        csv_path,
        usecols=meta_cols + pack_cols + pool_cols,
        dtype=dtypes,
        chunksize=CHUNK_ROWS,
    )
    for chunk_no, chunk in enumerate(reader):
        pick_idx = chunk["pick"].map(name_to_idx)
        valid = pick_idx.notna().to_numpy()
        skipped += int((~valid).sum())
        chunk = chunk[valid]

        packs.append(chunk[pack_cols].to_numpy(np.int8))
        pools.append(chunk[pool_cols].to_numpy(np.int8))
        picks.append(pick_idx[valid].to_numpy(np.int16))
        pack_numbers.append(chunk["pack_number"].to_numpy(np.int8))
        pick_numbers.append(chunk["pick_number"].to_numpy(np.int8))
        win_rates.append(chunk["user_game_win_rate_bucket"].fillna(0.5).to_numpy(np.float32))
        n_games.append(chunk["user_n_games_bucket"].fillna(0).to_numpy(np.int16))
        # Stable per-draft hash so train/validation can be split by draft, not by pick
        groups.append(
            np.fromiter((zlib.crc32(d.encode()) % 100 for d in chunk["draft_id"]), np.int8, len(chunk))
        )
        print(f"chunk {chunk_no}: {sum(len(p) for p in picks):,} picks", flush=True)

    np.savez(
        out_path,
        card_names=np.array(card_names),
        pack=np.concatenate(packs),
        pool=np.concatenate(pools),
        pick=np.concatenate(picks),
        pack_number=np.concatenate(pack_numbers),
        pick_number=np.concatenate(pick_numbers),
        win_rate=np.concatenate(win_rates),
        n_games=np.concatenate(n_games),
        group=np.concatenate(groups),
    )
    print(f"saved {out_path} ({len(card_names)} cards, skipped {skipped} rows with unknown picks)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
