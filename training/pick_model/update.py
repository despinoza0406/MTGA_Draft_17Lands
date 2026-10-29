"""
training/pick_model/update.py
One command to (re)train the pick model for a set: downloads the 17Lands public
draft dump when it changed, runs prepare.py and train.py, and installs the model
into the tool's Models folder.

Usage:
    python training/pick_model/update.py <SET> --tool-dir C:/MtgaDraftTool [--event PremierDraft] [--data-dir data]
"""

import argparse
import os
import shutil
import requests

import prepare
import train

DUMP_URL = "https://17lands-public.s3.amazonaws.com/analysis_data/draft_data/draft_data_public.{set}.{event}.csv.gz"


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("set", help="Set code, e.g. FRA")
    parser.add_argument("--event", default="PremierDraft")
    parser.add_argument("--tool-dir", required=True, help="Folder with the tool's Sets/ and Models/")
    parser.add_argument("--data-dir", default="data", help="Where the 17Lands dump and arrays are kept")
    parser.add_argument("--force", action="store_true", help="Retrain even if the dump did not change")
    parser.add_argument("--keep-arrays", action="store_true", help="Keep the prepared arrays (~1.5 GB)")
    args = parser.parse_args()

    set_code = args.set.upper()
    name = f"{set_code}_{args.event}"
    os.makedirs(args.data_dir, exist_ok=True)
    dump = os.path.join(args.data_dir, f"draft_data_public.{set_code}.{args.event}.csv.gz")
    arrays = os.path.join(args.data_dir, f"{name}_picks.npz")
    model = os.path.join(args.data_dir, f"{name}_pick_model.npz")
    installed = os.path.join(args.tool_dir, "Models", f"{name}_pick_model.npz")

    changed = download_if_changed(DUMP_URL.format(set=set_code, event=args.event), dump)
    if not changed and not args.force and os.path.exists(installed):
        print(f"model already current: {installed}")
        return

    prepare.main(dump, arrays)
    set_file = os.path.join(args.tool_dir, "Sets", f"{name}_All_Data.json")
    train.main(arrays, model, set_file if os.path.exists(set_file) else None)
    if not args.keep_arrays:
        os.remove(arrays)

    os.makedirs(os.path.dirname(installed), exist_ok=True)
    shutil.copyfile(model, installed)
    print(f"installed {installed}")


if __name__ == "__main__":
    main()
