"""
training/deck_model/benchmarks.py
Benchmarks from a 17Lands public game dump, used by the post-game review:
- mulligans: how often games are won after keeping a 7 with N lands, versus
  after a mulligan, on the play and on the draw
- deck: win rate by number of creatures and of instants in the maindeck

Usage:
    python training/deck_model/benchmarks.py <game_data.csv.gz> <out.json> [<17lands_set_file.json>]
"""

import sys
import json
import datetime
import numpy as np
import pandas as pd

BASIC_LANDS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def land_names(header, set_file):
    names = {c[len("opening_hand_") :] for c in header if c.startswith("opening_hand_")}
    lands = names & BASIC_LANDS
    if set_file:
        for card in json.load(open(set_file, encoding="utf-8"))["card_ratings"].values():
            if "Land" in card.get("types", []) and card["name"] in names:
                lands.add(card["name"])
    return sorted(lands)


def deck_tables(csv_path, header, set_file):
    """Win rate by creatures / instants in the maindeck (needs card types)."""
    if not set_file:
        return {}
    types = {}
    for card in json.load(open(set_file, encoding="utf-8"))["card_ratings"].values():
        types.setdefault(card["name"], set(card.get("types", [])))
    deck_cols = [c for c in header if c.startswith("deck_")]
    names = [c[len("deck_") :] for c in deck_cols]
    creature = np.array(["Creature" in types.get(n, ()) and "Land" not in types.get(n, ()) for n in names])
    instant = np.array(["Instant" in types.get(n, ()) for n in names])
    df = pd.read_csv(csv_path, usecols=deck_cols + ["won"])
    decks = df[deck_cols].to_numpy(np.float32)
    counts = pd.DataFrame(
        {"creatures": decks[:, creature].sum(1), "instants": decks[:, instant].sum(1), "won": df["won"]}
    )
    out = {}
    for col in ("creatures", "instants"):
        g = counts.groupby(col)["won"].agg(["sum", "count"])
        out[col] = {str(int(k)): {"games": int(r["count"]), "wins": int(r["sum"])} for k, r in g.iterrows()}
    return out


def main(csv_path, out_path, set_file=None):
    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    lands = land_names(header, set_file)
    cols = ["on_play", "num_mulligans", "won"] + [f"opening_hand_{n}" for n in lands]
    df = pd.read_csv(csv_path, usecols=cols)
    df["lands"] = df[[f"opening_hand_{n}" for n in lands]].sum(axis=1)
    df["side"] = df["on_play"].map({True: "play", False: "draw"})

    def table(rows, by):
        out = {}
        for (side, key), g in rows.groupby(["side", by]):
            out.setdefault(side, {})[str(int(key))] = {"games": int(len(g)), "wins": int(g["won"].sum())}
        return out

    result = {
        "source": csv_path,
        "built": datetime.date.today().isoformat(),
        "lands_counted": lands,
        # Kept 7: win rate by lands in hand
        "keep7": table(df[df["num_mulligans"] == 0], "lands"),
        # Win rate by number of mulligans taken (any hand)
        "mulligans": table(df, "num_mulligans"),
        "deck": deck_tables(csv_path, header, set_file),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)

    for side in ("play", "draw"):
        keep = result["keep7"].get(side, {})
        mull = result["mulligans"].get(side, {}).get("1", {"games": 0, "wins": 0})
        line = " ".join(f"{k}L:{v['wins'] / v['games']:.1%}({v['games']})" for k, v in sorted(keep.items(), key=lambda kv: int(kv[0])))
        print(f"{side}: keep7 {line} | after 1 mulligan {mull['wins'] / max(1, mull['games']):.1%}")
    print(f"saved {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
