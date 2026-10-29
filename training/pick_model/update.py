"""
training/pick_model/update.py
One command to (re)train the models for a set: downloads the 17Lands public
draft and game dumps when they changed, trains the pick model (this folder) and
the deck model (training/deck_model), and installs both into the tool's Models
folder.

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


if __name__ == "__main__":
    main()
