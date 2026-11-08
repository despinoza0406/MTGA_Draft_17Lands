"""
training/prerelease/prerelease.py
Day-1 card ratings for a set with no 17Lands games yet.

MTGAZone (0-5) and Draftsim (0-10) publish a pre-release Limited review for
every set, one rating per card. Past sets show how those ratings turned into
17Lands GIH WR, and where they were wrong (rares rated on their ceiling, cheap
commons and removal underrated). This script

    reviews  <SET> [<SET> ...]   downloads both reviews (cached in --data-dir)
    calibrate                    fits review + card features -> GIH WR on every
                                 past set that has both a review and a dataset,
                                 checks it one held-out set at a time, saves it
    build    <SET>               writes <SET>_<EVENT>_All_Data.json into the
                                 tool's Sets folder from Scryfall's card list,
                                 the reviews and the calibration

Usage:
    python training/prerelease/prerelease.py reviews TRK FRA BLB --data-dir data
    python training/prerelease/prerelease.py calibrate --tool-dir C:/MtgaDraftTool --data-dir data
    python training/prerelease/prerelease.py build TRK --tool-dir C:/MtgaDraftTool --data-dir data

The ratings it writes are predictions: the dataset's meta says so, and every
sample count is 0. The tool's normal sync replaces the file once 17Lands has
data for the set.
"""

import argparse
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from server import config  # noqa: E402  O_TAGS: the ETL's Scryfall tag queries

HEADERS = {"User-Agent": "MTGADraftTool/1.0", "Accept": "application/json"}
REVIEW_PAGES = ["white", "blue", "blue-2", "black", "red", "green", "multicolor", "artifacts-lands",
                "artifacts-and-lands", "colorless-lands", "colorless-land", "multicolor-artifact-lands",
                "multicolor-artifacts-lands", "colorless", "lands"]
EVENTS = ["PremierDraft", "QuickDraft", "TradDraft", "PickTwoDraft", "Sealed", "TradSealed"]
# Typical GIH WR level and spread of a set (last twelve sets: 54.6-56.8 and 3.5-4.4).
GIH_MEAN, GIH_SD = 55.3, 3.9
MIN_CARDS = 50


# ---------------------------------------------------------------- reviews

def _get(url, timeout=60):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


SOURCES = ("mtgazone", "draftsim")


def parse_ratings(html):
    """MTGAZone: {card slug: rating} from one review page. Each rating ("Rating: 3.5/5",
    or "3.5 // 5.0" in older reviews) belongs to the last card link before it."""
    out = {}
    for m in re.finditer(r"Rating:\s*([0-9.]+)\s*/{1,2}\s*5", html):
        slugs = re.findall(r"mtgazone\.com/cards/([a-z0-9-]+)/", html[max(0, m.start() - 6000):m.start()])
        if slugs:
            out[slugs[-1]] = float(m.group(1))
    return out


def parse_draftsim(html):
    """Draftsim: {card name: rating} from its one-page review. Each "Rating: 7/10"
    belongs to the last card heading before it."""
    out = {}
    for m in re.finditer(r"Rating:\s*([0-9.]+)\s*/\s*10", html):
        heads = re.findall(r"<h3[^>]*>(.*?)</h3>", html[max(0, m.start() - 8000):m.start()], re.S)
        if heads:
            name = htmllib.unescape(re.sub(r"<[^>]+>", "", heads[-1])).strip()
            out[name] = float(m.group(1))
    return out


def review_pages(code):
    """The set's MTGAZone review pages, found through its search."""
    pattern = re.compile(r"https://mtgazone\.com/([a-z0-9-]*-%s)-limited-set-review-[a-z0-9-]*/" % code.lower())
    bases = set()
    for query in (f"{code} limited set review", f"{code} limited set review white"):
        _, html = _get("https://mtgazone.com/?s=" + urllib.parse.quote_plus(query))
        bases |= set(pattern.findall(html))
    return [f"https://mtgazone.com/{b}-limited-set-review-{p}/" for b in sorted(bases) for p in REVIEW_PAGES]


def set_name_slug(code):
    try:
        req = urllib.request.Request(f"https://api.scryfall.com/sets/{code.lower()}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as r:
            name = json.load(r).get("name", "")
    except Exception:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def fetch_mtgazone(code):
    found, pages = {}, []
    for url in review_pages(code):
        status, html = _get(url)
        ratings = parse_ratings(html) if status == 200 else {}
        if ratings:
            found.update(ratings)
            pages.append(url)
        time.sleep(0.3)
    return found, pages


def fetch_draftsim(code):
    name = set_name_slug(code)
    for url in [f"https://draftsim.com/mtg-{code.lower()}-limited-set-review/"] + (
            [f"https://draftsim.com/{name}-limited-set-review/", f"https://draftsim.com/mtg-{name}-limited-set-review/"]
            if name else []):
        status, html = _get(url)
        ratings = parse_draftsim(html) if status == 200 else {}
        if len(ratings) >= MIN_CARDS:
            return ratings, [url]
        time.sleep(0.3)
    return {}, []


def fetch_reviews(code, review_dir, sources=SOURCES):
    for source in sources:
        folder = os.path.join(review_dir, source)
        os.makedirs(folder, exist_ok=True)
        found, pages = (fetch_mtgazone if source == "mtgazone" else fetch_draftsim)(code)
        print(f"{code} {source}: {len(pages)} pages, {len(found)} cards")
        if found:
            with open(os.path.join(folder, f"{code}.json"), "w", encoding="utf-8") as f:
                json.dump({"pages": pages, "ratings": found}, f, indent=0)


def load_reviews(code, review_dir):
    """{source: {card key: rating}} for the sources that reviewed the set."""
    out = {}
    for source in SOURCES:
        path = os.path.join(review_dir, source, f"{code}.json")
        if source == "mtgazone" and not os.path.exists(path):
            path = os.path.join(review_dir, f"{code}.json")  # the first layout: MTGAZone only
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out[source] = {key(k): v for k, v in json.load(f)["ratings"].items()}
    return out


def standardize(reviews):
    """Each source's ratings as z-scores within the set: their scales differ."""
    out = {}
    for source, ratings in reviews.items():
        v = np.array(list(ratings.values()), float)
        out[source] = {k: float((x - v.mean()) / (v.std() or 1.0)) for k, x in ratings.items()}
    return out


# ---------------------------------------------------------------- features and fit

def key(name):
    """Name or slug -> one compact key, so "U.S.S. Voyager, Lone Wanderer" meets
    "uss-voyager-lone-wanderer" and the front face names a double-faced card."""
    name = name.split(" // ")[0].lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "", name)


def features(card, zs):
    """zs: {source: z-scored rating} for the sources that reviewed this card."""
    colors = card.get("colors") or []
    tags = card.get("tags") or []
    cmc = float(card.get("cmc") or 0)
    rare = card.get("rarity") in ("rare", "mythic")
    gold = len(colors) >= 2
    consensus = float(np.mean(list(zs.values())))
    return [
        1.0, consensus, consensus ** 2, len(zs) - 1,
        # A missing source reads as the others' consensus.
        *[zs.get(source, consensus) for source in SOURCES],
        card.get("rarity") == "uncommon", card.get("rarity") == "rare", card.get("rarity") == "mythic",
        gold, not colors, "removal" in tags, "card_advantage" in tags,
        "Creature" in card.get("types", []), "Land" in card.get("types", []),
        cmc <= 2, cmc >= 5,
        # Reviewers rate rares on their ceiling: their slope differs.
        consensus * rare, consensus * gold,
    ]


FEATURES = ["const", "consensus", "consensus^2", "extra sources", *[f"z {source}" for source in SOURCES],
            "uncommon", "rare", "mythic", "gold", "colorless", "removal", "card_advantage", "creature", "land",
            "cmc<=2", "cmc>=5", "consensus*rare", "consensus*gold"]


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def fit(rows, lam=3.0):
    X = np.array([r["x"] for r in rows], float)
    y = np.array([r["z"] for r in rows])
    reg = lam * np.eye(X.shape[1])
    reg[0, 0] = 0
    return np.linalg.solve(X.T @ X + reg, X.T @ y)


def past_set_rows(code, review_dir, sets_dir):
    """Cards of a past set with a review and a 17Lands GIH WR, with GIH WR
    standardized within the set."""
    data_path = os.path.join(sets_dir, f"{code}_PremierDraft_All_Data.json")
    zs = standardize(load_reviews(code, review_dir))
    if not zs or not os.path.exists(data_path):
        return []
    with open(data_path, encoding="utf-8") as f:
        dataset = json.load(f)
    if dataset.get("meta", {}).get("predicted"):
        return []  # our own prediction, not 17Lands results
    cards = dataset["card_ratings"]
    rows, seen = [], set()
    for c in cards.values():
        k = key(c.get("name", ""))
        gih = c.get("deck_colors", {}).get("All Decks", {}).get("gihwr")
        src ={s: z[k] for s, z in zs.items() if k in z}
        if src and gih and k not in seen:
            seen.add(k)
            rows.append({"set": code, "name": c["name"], "src": src, "gih": float(gih), "x": features(c, src)})
    if len(rows) < MIN_CARDS:
        return []
    g = np.array([r["gih"] for r in rows])
    for r, z in zip(rows, (g - g.mean()) / g.std()):
        r["z"] = float(z)
    return rows


def calibrate(review_dir, sets_dir, out_path):
    codes = {f[:-5] for f in os.listdir(review_dir) if f.endswith(".json")}
    for source in SOURCES:
        folder = os.path.join(review_dir, source)
        if os.path.isdir(folder):
            codes |= {f[:-5] for f in os.listdir(folder) if f.endswith(".json")}
    data = {c: rows for c in sorted(codes) if (rows := past_set_rows(c, review_dir, sets_dir))}
    if len(data) < 2:
        raise SystemExit("Need reviews and datasets for at least two past sets.")
    columns = [*SOURCES, "consensus", "fit"]
    print(f"{'set':5s} {'cards':>5s} " + " ".join(f"{c:>9s}" for c in columns) + "   (spearman with GIH WR, set held out)")
    scores = {c: [] for c in columns}
    for code, rows in data.items():
        w = fit([r for c, v in data.items() if c != code for r in v])
        gih = [r["gih"] for r in rows]
        line = f"{code:5s} {len(rows):5d}"
        for source in SOURCES:
            sub = [r for r in rows if source in r["src"]]
            if len(sub) >= MIN_CARDS:
                v = spearman([r["src"][source] for r in sub], [r["gih"] for r in sub])
                scores[source].append(v)
                line += f" {v:9.2f}"
            else:
                line += f" {'-':>9s}"
        for column, pred in (("consensus", [np.mean(list(r["src"].values())) for r in rows]),
                             ("fit", [float(np.dot(w, r["x"])) for r in rows])):
            v = spearman(pred, gih)
            scores[column].append(v)
            line += f" {v:9.2f}"
        print(line)
    print(f"{'mean':11s} " + " ".join(f"{np.mean(v):9.2f}" if v else f"{'-':>9s}" for v in scores.values()))
    w = fit([r for v in data.values() for r in v])
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"features": FEATURES, "weights": [float(v) for v in w], "sets": list(data)}, f, indent=1)
    print("saved", out_path)


# ---------------------------------------------------------------- build

def scryfall(query):
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode({"q": query, "unique": "cards"})
    out = []
    while url:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return out
            raise
        out += data.get("data", [])
        url = data.get("next_page")
        time.sleep(0.12)  # Scryfall asks for 50-100 ms between requests
    return out


def card_list(code):
    """{name: card} in the dataset's card format, with the ETL's Scryfall tags."""
    cards = {}
    for c in scryfall(f"e:{code.lower()}"):
        faces = c.get("card_faces") or [c]
        type_line = c.get("type_line") or faces[0].get("type_line", "")
        main_types = type_line.split("—")[0].split("//")[0]
        cards[c["name"]] = {
            "name": c["name"],
            "cmc": c.get("cmc", 0),
            "mana_cost": c.get("mana_cost") or faces[0].get("mana_cost", ""),
            "isprimarycard": 1,
            "linkedfacetype": 0,
            "types": [t for t in ("Creature", "Instant", "Sorcery", "Enchantment", "Artifact", "Planeswalker",
                                  "Land", "Battle", "Legendary", "Basic") if t in main_types],
            "subtypes": type_line.split("—")[1].split("//")[0].split() if "—" in type_line else [],
            "rarity": c.get("rarity", ""),
            "colors": c.get("colors") or faces[0].get("colors", []),
            "color_identity": c.get("color_identity", []),
            "keywords": c.get("keywords", []),
            "oracle_text": "\n".join(f.get("oracle_text", "") for f in faces),
            "image": [(c.get("image_uris") or faces[0].get("image_uris") or {}).get("large", "")],
            "set": code.upper(),
            "tags": [],
            "collector_number": c.get("collector_number", ""),
        }
    for tag, query in config.O_TAGS.items():
        for c in scryfall(f"e:{code.lower()} ({query})"):
            if c["name"] in cards and tag not in cards[c["name"]]["tags"]:
                cards[c["name"]]["tags"].append(tag)
    return cards


def predicted_dataset(code, cards, reviews, calibration, today):
    """The tool's dataset dict. reviews: {source: {card name or slug: rating}}.
    Cards no source reviewed are left out (unrated)."""
    if calibration.get("features") != FEATURES:
        raise SystemExit("The calibration was made with other features: run calibrate again.")
    w = np.array(calibration["weights"])
    zs = standardize({s: {key(k): v for k, v in r.items()} for s, r in reviews.items()})
    raw = {s: {key(k): v for k, v in r.items()} for s, r in reviews.items()}
    ratings, z = {}, {}
    for name, c in cards.items():
        k = key(name)
        src = {s: v[k] for s, v in zs.items() if k in v}
        if "Basic" in c["types"] or not src:
            continue
        card = {f: v for f, v in c.items() if f != "collector_number"}
        card["review_ratings"] = {s: raw[s][k] for s in src}
        card_id = f"{code.upper()}{c.get('collector_number') or len(ratings)}"
        ratings[card_id] = card
        z[card_id] = float(np.dot(w, features(c, src)))
    if not ratings:
        return None
    # Predictions shrink toward the mean; spread them like a real set so the
    # tool's absolute cut-offs (deck estimate, bombs) read them on the usual scale.
    zs = np.array(list(z.values()))
    for card_id, card in ratings.items():
        gih = round(GIH_MEAN + GIH_SD * (z[card_id] - zs.mean()) / (zs.std() or 1.0), 2)
        card["deck_colors"] = {"All Decks": {
            "gihwr": gih, "ohwr": gih, "gpwr": gih, "gnswr": gih, "gdwr": gih, "alsa": 0.0, "ata": 0.0,
            "iwd": 0.0, "samples": 0, "seen_count": 0, "pick_count": 0, "game_count": 0, "play_rate": 0.0}}
    meta = {"collection_date": today, "start_date": today, "end_date": today, "version": 3.0, "game_count": 0,
            "predicted": True,
            "source": "Predicted from " + " and ".join(sorted(reviews)) + " pre-release reviews, calibrated on "
                      + ", ".join(calibration["sets"])}
    return {"meta": meta, "color_ratings": {}, "card_ratings": ratings}


def build(code, review_dir, sets_dir, calibration_path, events):
    missing = [s for s in SOURCES if s not in load_reviews(code, review_dir)]
    if missing:
        fetch_reviews(code, review_dir, missing)
    reviews = load_reviews(code, review_dir)
    if not reviews:
        raise SystemExit(f"No review found for {code}.")
    with open(calibration_path, encoding="utf-8") as f:
        calibration = json.load(f)
    cards = card_list(code)
    dataset = predicted_dataset(code, cards, reviews, calibration, time.strftime("%Y-%m-%d"))
    if dataset is None:
        raise SystemExit(f"No reviewed cards matched Scryfall's {code} list.")
    rated = dataset["card_ratings"].values()
    print(f"{len(rated)} of {len(cards)} cards rated")
    for event in events:
        path = os.path.join(sets_dir, f"{code.upper()}_{event}_All_Data.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                if not json.load(f).get("meta", {}).get("predicted"):
                    print(f"  kept {os.path.basename(path)}: it already has 17Lands data")
                    continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataset, f)
        print("  wrote", path)
    top = sorted(rated, key=lambda c: -c["deck_colors"]["All Decks"]["gihwr"])[:10]
    print("top:", ", ".join(f"{c['name']} {c['deck_colors']['All Decks']['gihwr']:.1f}" for c in top))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["reviews", "calibrate", "build"])
    parser.add_argument("sets", nargs="*")
    parser.add_argument("--tool-dir", default="C:/MtgaDraftTool")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--events", default=",".join(EVENTS))
    args = parser.parse_args(argv)

    review_dir = os.path.join(args.data_dir, "prerelease", "reviews")
    os.makedirs(review_dir, exist_ok=True)
    calibration_path = os.path.join(args.data_dir, "prerelease", "calibration.json")
    sets_dir = os.path.join(args.tool_dir, "Sets")
    if args.command == "reviews":
        for code in args.sets:
            fetch_reviews(code.upper(), review_dir)
    elif args.command == "calibrate":
        calibrate(review_dir, sets_dir, calibration_path)
    else:
        for code in args.sets:
            build(code.upper(), review_dir, sets_dir, calibration_path, args.events.split(","))


if __name__ == "__main__":
    main()
