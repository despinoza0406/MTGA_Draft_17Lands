"""
training/deck_model/prepare.py
Converts a 17Lands public game dump (game_data_public.<SET>.<EVENT>.csv.gz) into
one row per submitted deck: the pool (maindeck + sideboard, basics excluded),
the maindeck, and how the deck performed.

Usage:
    python training/deck_model/prepare.py <game_data.csv.gz> <out.npz>
"""

import sys
import zlib
import numpy as np
import pandas as pd

CHUNK_ROWS = 100_000
BASIC_LANDS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def main(csv_path: str, out_path: str):
    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    deck_cols = [c for c in header if c.startswith("deck_")]
    card_names = [c[len("deck_") :] for c in deck_cols]
    side_cols = [f"sideboard_{n}" for n in card_names]
    assert set(side_cols) <= set(header), "deck/sideboard columns differ"
    keep = np.array([n not in BASIC_LANDS for n in card_names])

    meta_cols = ["draft_id", "build_index", "won", "user_game_win_rate_bucket"]
    dtypes = {c: np.int8 for c in deck_cols + side_cols}
    decks = {}  # (draft_id, build_index) -> [deck, pool, wins, games, user_wr]
    for chunk_no, chunk in enumerate(
        pd.read_csv(csv_path, usecols=meta_cols + deck_cols + side_cols, dtype=dtypes, chunksize=CHUNK_ROWS)
    ):
        deck = chunk[deck_cols].to_numpy(np.int8)[:, keep]
        side = chunk[side_cols].to_numpy(np.int8)[:, keep]
        won = chunk["won"].astype(bool).to_numpy()
        user_wr = chunk["user_game_win_rate_bucket"].fillna(0.5).to_numpy(np.float32)
        keys = zip(chunk["draft_id"], chunk["build_index"])
        for i, key in enumerate(keys):
            entry = decks.get(key)
            if entry is None:
                decks[key] = [deck[i], deck[i] + side[i], int(won[i]), 1, user_wr[i]]
            else:
                entry[2] += int(won[i])
                entry[3] += 1
        print(f"chunk {chunk_no}: {len(decks):,} decks", flush=True)

    rows = list(decks.items())
    np.savez(
        out_path,
        card_names=np.array(card_names)[keep],
        deck=np.stack([r[1][0] for r in rows]),
        pool=np.stack([r[1][1] for r in rows]),
        wins=np.array([r[1][2] for r in rows], np.int16),
        games=np.array([r[1][3] for r in rows], np.int16),
        win_rate=np.array([r[1][4] for r in rows], np.float32),
        # Stable per-draft hash so validation never shares a draft with training
        group=np.array([zlib.crc32(r[0][0].encode()) % 100 for r in rows], np.int8),
    )
    print(f"saved {out_path} ({len(rows):,} decks, {int(keep.sum())} cards)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
