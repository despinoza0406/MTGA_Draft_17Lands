"""
src/match_history.py
Personal match history. Arena overwrites Player.log on every launch, so finished
matches are read from Player.log / Player-prev.log and accumulated in a JSON
store in the tool folder.
"""

import os
import re
import json
import logging
import datetime
from collections import Counter
from typing import Callable, Dict, List, Optional

from src import constants

logger = logging.getLogger(__name__)

HISTORY_FILE = os.path.join(constants.BASE_DIR, "Match_History.json")
MATCH_COMPLETED = "MatchGameRoomStateType_MatchCompleted"
COLOR_ORDER = "WUBRG"
MIN_CARDS_PER_COLOR = 3


def _find_key(obj, key):
    """Depth-first search for the first dict value stored under `key`."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_key(child, key)
        if found is not None:
            return found
    return None


def _json_after_brace(line: str) -> Optional[dict]:
    start = line.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(line, start)[0]
    except ValueError:
        return None


def event_format(event_name: str):
    """'QuickDraft_FRA_20261027' -> ('QuickDraft', 'FRA'); constructed queues have no set."""
    parts = event_name.split("_")
    if len(parts) >= 2 and re.fullmatch(r"[A-Z0-9]{2,5}", parts[1]):
        return parts[0], parts[1]
    return event_name, ""


def parse_log(text: str) -> Dict:
    """Returns the user's id, finished matches, and the latest deck per event."""
    client_ids = set(re.findall(r'"clientId":\s*"([^"]+)"', text))
    matches, decks = [], {}
    for line in text.splitlines():
        if MATCH_COMPLETED in line:
            data = _json_after_brace(line)
            result = _find_key(data, "finalMatchResult") if data else None
            players = _find_key(data, "reservedPlayers") if data else None
            if not result or not players:
                continue
            mine = next((p for p in players if p.get("userId") in client_ids), None)
            match_scope = next(
                (r for r in result.get("resultList", []) if r.get("scope") == "MatchScope_Match"),
                None,
            )
            if mine and match_scope:
                matches.append(
                    {
                        "match_id": result.get("matchId"),
                        "event": mine.get("eventId", ""),
                        "won": match_scope.get("winningTeamId") == mine.get("teamId"),
                    }
                )
        elif '"Courses"' in line:
            data = _json_after_brace(line)
            for course in (data or {}).get("Courses", []):
                main = (course.get("CourseDeck") or {}).get("MainDeck", [])
                if main:
                    decks[course.get("InternalEventName", "")] = [
                        str(c["cardId"]) for c in main for _ in range(c.get("quantity", 1))
                    ]
    return {"client_ids": client_ids, "matches": matches, "decks": decks}


def deck_colors(card_ids: List[str], lookup: Callable[[List[str]], List[Dict]]) -> str:
    counts = Counter()
    for card in lookup(card_ids) if card_ids else []:
        if "Land" in card.get(constants.DATA_FIELD_TYPES, []):
            continue
        for color in card.get(constants.DATA_FIELD_COLORS, []):
            counts[color] += 1
    return "".join(c for c in COLOR_ORDER if counts[c] >= MIN_CARDS_PER_COLOR)


def load_history(path: str = HISTORY_FILE) -> List[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def update_history(
    log_paths: List[str],
    lookup: Callable[[List[str]], List[Dict]],
    path: str = HISTORY_FILE,
) -> List[Dict]:
    """Merges matches found in the given logs into the store; returns the full history."""
    history = load_history(path)
    known = {m["match_id"] for m in history}
    added = 0
    for log_path in log_paths:
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                parsed = parse_log(f.read())
        except OSError:
            continue
        for match in parsed["matches"]:
            if not match["match_id"] or match["match_id"] in known:
                continue
            fmt, set_code = event_format(match["event"])
            history.append(
                {
                    **match,
                    "format": fmt,
                    "set": set_code,
                    "colors": deck_colors(parsed["decks"].get(match["event"], []), lookup),
                    "recorded": datetime.datetime.now().isoformat(timespec="minutes"),
                }
            )
            known.add(match["match_id"])
            added += 1
    if added:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=1)
        except OSError as e:
            logger.warning(f"Match history save error: {e}")
    return history


def summarize(history: List[Dict], keys=("format", "set", "colors")) -> List[Dict]:
    """Win/loss per group, most played first."""
    groups = {}
    for match in history:
        group = tuple(match.get(k, "") for k in keys)
        wins, total = groups.get(group, (0, 0))
        groups[group] = (wins + bool(match.get("won")), total + 1)
    rows = [
        {**dict(zip(keys, group)), "wins": w, "losses": t - w, "win_rate": w / t}
        for group, (w, t) in groups.items()
    ]
    return sorted(rows, key=lambda r: (-(r["wins"] + r["losses"]), -r["win_rate"]))
