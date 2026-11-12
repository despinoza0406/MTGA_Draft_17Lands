"""
training/pick_model/update.py
One command to (re)train the models for a set: downloads the 17Lands public
draft and game dumps when they changed, trains the pick model (this folder) and
the deck model (training/deck_model), and installs both into the tool's Models
folder. With the Premier Draft event it also builds the set's Sealed deck model:
the Premier deck model fine-tuned on the Sealed and Traditional Sealed decks
(too few of them to learn from scratch).

Usage:
    python training/pick_model/update.py [<SET>] --tool-dir C:/MtgaDraftTool [--event PremierDraft] [--data-dir data]

Without <SET>, the set of your most recent draft (the newest DraftLog_<SET>_*.log
in the tool's Logs folder) is used, so the script can run unattended.
"""

import argparse
import glob
import importlib.util
import json
import os
import shutil
import numpy as np
import requests

import prepare
import train

BASE_URL = "https://17lands-public.s3.amazonaws.com/analysis_data"
DUMP_URL = BASE_URL + "/draft_data/draft_data_public.{set}.{event}.csv.gz"
GAME_DUMP_URL = BASE_URL + "/game_data/game_data_public.{set}.{event}.csv.gz"
DECK_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deck_model")


def _load_deck_module(name):
    spec = importlib.util.spec_from_file_location(f"deck_{name}", os.path.join(DECK_MODEL_DIR, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def download_if_changed(url: str, path: str) -> bool:
    """Downloads url to path unless the local copy already has the remote size."""
    head = requests.head(url, timeout=30)
    if head.status_code == 404:
        raise SystemExit(f"17Lands has no public dump at {url}")
    head.raise_for_status()
    remote_size = int(head.headers.get("Content-Length", -1))
    if os.path.exists(path) and os.path.getsize(path) == remote_size:
        print(f"up to date: {path}")
        return False

    print(f"downloading {url} ({remote_size / 2**20:.0f} MB)")
    tmp = path + ".part"
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(tmp, "wb") as f:
            for block in response.iter_content(1 << 20):
                f.write(block)
    os.replace(tmp, path)
    return True


SEALED_EVENTS = ("Sealed", "TradSealed")


def sealed_deck_model(set_code, args, set_file):
    """Fine-tunes the installed Premier deck model on the set's Sealed decks and
    installs it as <SET>_Sealed_deck_model.npz. Skips quietly when 17Lands has no
    Sealed dump yet or nothing changed."""
    base = os.path.join(args.tool_dir, "Models", f"{set_code}_PremierDraft_deck_model.npz")
    installed = os.path.join(args.tool_dir, "Models", f"{set_code}_Sealed_deck_model.npz")
    if not os.path.exists(base):
        return
    dumps, changed = [], False
    for event in SEALED_EVENTS:
        url = GAME_DUMP_URL.format(set=set_code, event=event)
        if requests.head(url, timeout=30).status_code != 200:
            continue
        dump = os.path.join(args.data_dir, f"game_data_public.{set_code}.{event}.csv.gz")
        changed |= download_if_changed(url, dump)
        dumps.append(dump)
    if not dumps:
        print(f"no Sealed dump for {set_code} yet")
        return
    if not changed and not args.force and os.path.exists(installed)             and os.path.getmtime(installed) >= os.path.getmtime(base):
        print(f"sealed deck model already current: {installed}")
        return

    prep, trainer = _load_deck_module("prepare"), _load_deck_module("train")
    parts = []
    for dump in dumps:
        arrays = dump.replace(".csv.gz", "_decks.npz")
        prep.main(dump, arrays)
        parts.append(dict(np.load(arrays)))
        os.remove(arrays)
    names = parts[0]["card_names"]
    parts = [p for p in parts if list(p["card_names"]) == list(names)]
    combined = os.path.join(args.data_dir, f"{set_code}_Sealed_decks.npz")
    np.savez(combined, **{k: names if k == "card_names" else np.concatenate([p[k] for p in parts])
                          for k in parts[0]})
    model = os.path.join(args.data_dir, f"{set_code}_Sealed_deck_model.npz")
    try:
        trainer.main(combined, model, set_file, init=base)
    except AssertionError as e:  # the Premier model's card list differs from the dump's
        print(f"sealed deck model skipped: {e}")
        return
    finally:
        if not args.keep_arrays:
            os.remove(combined)
    shutil.copyfile(model, installed)
    print(f"installed {installed}")


def active_set(tool_dir):
    """Set of the most recent draft log ('DraftLog_FRA_QuickDraft_<id>.log' -> 'FRA'),
    falling back to the dataset selected in the tool's config."""
    logs = glob.glob(os.path.join(tool_dir, "Logs", "DraftLog_*_*.log"))
    if logs:
        return os.path.basename(max(logs, key=os.path.getmtime)).split("_")[1]
    path = os.path.join(os.getenv("APPDATA", ""), "MTGA_Draft_Tool", "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            latest = json.load(f).get("card_data", {}).get("latest_dataset", "")
    except (OSError, ValueError):
        latest = ""
    return latest.split("_")[0] if latest else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("set", nargs="?", help="Set code, e.g. FRA (default: the tool's active set)")
    parser.add_argument("--event", default="PremierDraft")
    parser.add_argument("--tool-dir", required=True, help="Folder with the tool's Sets/ and Models/")
    parser.add_argument("--data-dir", default="data", help="Where the 17Lands dump and arrays are kept")
    parser.add_argument("--force", action="store_true", help="Retrain even if the dump did not change")
    parser.add_argument("--keep-arrays", action="store_true", help="Keep the prepared arrays (~1.5 GB)")
    parser.add_argument("--no-sealed", action="store_true", help="Skip the Sealed deck model")
    args = parser.parse_args()

    set_code = (args.set or active_set(args.tool_dir) or "").upper()
    if not set_code:
        raise SystemExit("No set given and no draft logs or active dataset found")
    print(f"set: {set_code}")
    name = f"{set_code}_{args.event}"
    os.makedirs(args.data_dir, exist_ok=True)
    set_file = os.path.join(args.tool_dir, "Sets", f"{name}_All_Data.json")
    set_file = set_file if os.path.exists(set_file) else None

    jobs = [
        ("pick", DUMP_URL, "draft_data_public", prepare, train, "picks"),
        ("deck", GAME_DUMP_URL, "game_data_public", _load_deck_module("prepare"), _load_deck_module("train"), "decks"),
    ]
    for kind, url, dump_prefix, prep, trainer, arrays_suffix in jobs:
        dump = os.path.join(args.data_dir, f"{dump_prefix}.{set_code}.{args.event}.csv.gz")
        arrays = os.path.join(args.data_dir, f"{name}_{arrays_suffix}.npz")
        model = os.path.join(args.data_dir, f"{name}_{kind}_model.npz")
        installed = os.path.join(args.tool_dir, "Models", f"{name}_{kind}_model.npz")

        changed = download_if_changed(url.format(set=set_code, event=args.event), dump)
        if not changed and not args.force and os.path.exists(installed):
            print(f"{kind} model already current: {installed}")
            continue

        prep.main(dump, arrays)
        trainer.main(arrays, model, set_file)
        if not args.keep_arrays:
            os.remove(arrays)

        os.makedirs(os.path.dirname(installed), exist_ok=True)
        shutil.copyfile(model, installed)
        print(f"installed {installed}")

    # Mulligan / opening hand benchmarks for the post-game review (game dump)
    game_dump = os.path.join(args.data_dir, f"game_data_public.{set_code}.{args.event}.csv.gz")
    bench = os.path.join(args.data_dir, f"{name}_benchmarks.json")
    bench_installed = os.path.join(args.tool_dir, "Models", f"{name}_benchmarks.json")
    stale = not os.path.exists(bench_installed) or os.path.getmtime(bench_installed) < os.path.getmtime(game_dump)
    if stale or args.force:
        _load_deck_module("benchmarks").main(game_dump, bench, set_file)
        shutil.copyfile(bench, bench_installed)
        print(f"installed {bench_installed}")
    else:
        print(f"benchmarks already current: {bench_installed}")

    if args.event == "PremierDraft" and not args.no_sealed:
        sealed_deck_model(set_code, args, set_file)


if __name__ == "__main__":
    main()
