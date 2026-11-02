"""
src/deck_record.py
Which deck did you actually play? Reads the deck you submit in Arena from the
log, keeps the decks the tool suggested for each draft, and labels every game
by where its deck came from: the trained model's build, another suggestion
(with how many cards you changed), or your own build. History turns that into
a record per source.
"""

import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional

from src import constants

logger = logging.getLogger(__name__)

SUGGESTIONS_FILE = os.path.join(constants.BASE_DIR, "Deck_Suggestions.json")
MAX_SNAPSHOTS = 60
# A played deck within this many card swaps of a suggestion counts as that suggestion.
SAME_DECK_CHANGES = 3
MODEL_PREFIX = "🤖"

SOURCE_NAMES = {"model": "🤖 Model build", "suggested": "Other suggestion", "own": "Your own build"}
SOURCE_SHORT = {"model": "🤖", "suggested": "Sugg.", "own": "Own"}

BASIC_NAMES = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def _is_basic(name: str) -> bool:
    return str(name).removeprefix("Snow-Covered ") in BASIC_NAMES


# --- the deck you submit in Arena ---------------------------------------------------------

def _expand(entries) -> List[str]:
    out = []
    for entry in entries or []:
        out += [str(entry.get("cardId"))] * int(entry.get("quantity", 1) or 1)
    return out


def parse_submitted_decks(log_text: str) -> List[Dict]:
    """Every EventSetDeck request in a log, oldest first: event, main and
    sideboard as lists of Arena ids (one per copy), and Arena's timestamp."""
    decks = []
    for line in log_text.splitlines():
        if "==> EventSetDeck" not in line:
            continue
        try:
            outer = json.loads(line[line.index("{"):])
            request = json.loads(outer.get("request") or "{}")
        except (ValueError, TypeError):
            continue
        deck = request.get("Deck") or {}
        main = _expand(deck.get("MainDeck"))
        if not main:
            continue
        updated = ""
        for attr in (request.get("Summary") or {}).get("Attributes") or []:
            if attr.get("name") == "LastUpdated":
                updated = str(attr.get("value", "")).strip('"')
        decks.append({
            "event": request.get("EventName", ""),
            "main": main,
            "sideboard": _expand(deck.get("Sideboard")),
            "updated": updated,
        })
    return decks


def latest_submitted_deck(log_paths: Iterable[str]) -> Optional[Dict]:
    """The last deck you clicked Done on in Arena, from the newest log that has one."""
    for path in reversed(list(log_paths)):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                decks = parse_submitted_decks(f.read())
        except OSError:
            continue
        if decks:
            return decks[-1]
    return None


# --- decks the tool suggested --------------------------------------------------------------

def deck_label(label: str) -> str:
    """'RG Consistent [Est: 4-3] (Power: 78)' -> 'RG Consistent'. The estimate and power
    change on every rebuild; keyed without them, a rebuilt deck replaces its old entry."""
    return label.split(" [", 1)[0].split(" (Power", 1)[0].strip()


def _names(cards: List[Dict]) -> List[str]:
    out = []
    for c in cards or []:
        name = c.get(constants.DATA_FIELD_NAME, "")
        if name and not _is_basic(name):
            out += [name] * int(c.get("count", 1) or 1)
    return out


def load_snapshots(path: str = SUGGESTIONS_FILE) -> List[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_suggestions(pool: List[Dict], decks: Dict[str, List[Dict]], event: str = "",
                     path: str = SUGGESTIONS_FILE) -> None:
    """Remember the decks suggested for a pool. decks: {label: card dicts with counts}.
    Merges into the snapshot of the same pool, so the model build saved at the end
    of the draft and the full list from the Deck page end up together."""
    pool_names = sorted(_names(pool))
    if not pool_names or not decks:
        return
    # UI tests build decks from fake pools; only an explicit path may be written then.
    if path == SUGGESTIONS_FILE and "pytest" in sys.modules:
        return
    snapshots = load_snapshots(path)
    snap = next((s for s in snapshots if s.get("pool") == pool_names), None)
    if snap is None:
        snap = {"pool": pool_names, "event": event, "decks": {}}
        snapshots.append(snap)
    snap["saved"] = datetime.now().isoformat(timespec="minutes")
    snap["event"] = event or snap.get("event", "")
    snap["decks"] = {deck_label(k): v for k, v in snap.get("decks", {}).items()}
    for label, cards in decks.items():
        names = _names(cards)
        if names:
            snap["decks"][deck_label(label)] = sorted(names)
    # The end-of-draft save names the model build without its colors; the Deck page
    # saves the same cards as "BG 🤖 Model Build (Splash R)". Keep one, the fuller name.
    by_cards: Dict[tuple, str] = {}
    for label in sorted(snap["decks"], key=len, reverse=True):
        by_cards.setdefault(tuple(snap["decks"][label]), label)
    snap["decks"] = {k: v for k, v in snap["decks"].items() if by_cards[tuple(v)] == k}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshots[-MAX_SNAPSHOTS:], f, indent=1)
    except OSError as e:
        logger.warning(f"Could not save deck suggestions: {e}")


# --- where a played deck came from -----------------------------------------------------------

def _changes(played: Counter, suggested: Counter) -> int:
    """Cards in your deck that the suggestion didn't have (= swaps made)."""
    return sum((played - suggested).values())


def classify_deck(played_names: List[str], snapshots: List[Dict]) -> Optional[Dict]:
    """{"source": model|suggested|own, "label", "changes"} for a played deck, using
    the newest snapshot whose pool holds every card in it; None if none does."""
    played = Counter(n for n in played_names if n and not _is_basic(n))
    if not played:
        return None
    for snap in reversed(snapshots):
        pool = Counter(snap.get("pool", []))
        if played - pool:
            continue
        best = None
        for label, names in snap.get("decks", {}).items():
            changes = _changes(played, Counter(names))
            is_model = MODEL_PREFIX in label  # labels read "BG 🤖 Model Build [...]"
            # Prefer the model on a tie: the two agreeing is credit to it.
            key = (changes, not is_model)
            if best is None or key < best[0]:
                best = (key, label, changes, is_model)
        if best is None:
            return None
        _, label, changes, is_model = best
        if changes > SAME_DECK_CHANGES:
            source = "own"
        else:
            source = "model" if is_model else "suggested"
        return {"source": source, "label": label, "changes": changes}
    return None


def deck_names(grp_ids: List, lookup: Callable[[List[str]], List[Dict]]) -> List[str]:
    out = []
    for g in grp_ids or []:
        found = lookup([str(g)])
        if found and found[0].get(constants.DATA_FIELD_NAME):
            out.append(found[0][constants.DATA_FIELD_NAME])
    return out


# Consecutive games whose decks share this many nonbasic cards came from one draft
# (deck tweaks between matches swap a few cards; a new draft swaps nearly all).
SAME_DRAFT_SHARED = 12


def group_runs(entries: List[tuple], min_shared: int = SAME_DRAFT_SHARED) -> List[List[int]]:
    """Split games (newest first) into runs from the same draft or event.

    entries: (event, deck card names) per game. A new run starts when the
    event changes or, for Limited, when the deck shares too few cards with
    the run's first deck. Arena reuses one event name for every draft of a
    format, so the deck is what tells two drafts apart."""
    runs: List[List[int]] = []
    anchor = None
    for i, (event, names) in enumerate(entries):
        deck = Counter(n for n in names if n and not _is_basic(n))
        if runs and anchor is not None and event == anchor[0]:
            limited = any(h in (event or "") for h in ("Draft", "Sealed"))
            shared = sum((deck & anchor[1]).values())
            if not limited or not deck or not anchor[1] or shared >= min_shared:
                runs[-1].append(i)
                continue
        runs.append([i])
        anchor = (event, deck)
    return runs


def source_text(info: Optional[Dict]) -> str:
    if not info:
        return "?"
    short = SOURCE_SHORT[info["source"]]
    if info["source"] != "own" and info["changes"]:
        return f"{short} +{info['changes']}"
    return short


def record_by_source(games: List) -> Optional[str]:
    """One line: game record per deck source, from (won, info) pairs."""
    tally: Dict[str, List[int]] = {}
    for won, info in games:
        if not info or won is None:
            continue
        wins_losses = tally.setdefault(info["source"], [0, 0])
        wins_losses[0 if won else 1] += 1
    if not tally:
        return None
    parts = [f"{SOURCE_NAMES[s]} {w}-{l}" for s, (w, l) in
             sorted(tally.items(), key=lambda kv: list(SOURCE_NAMES).index(kv[0]))]
    return "Deck you played (games): " + " · ".join(parts) + "."
