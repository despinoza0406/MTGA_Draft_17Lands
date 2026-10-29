"""
tests/test_match_history.py
Validation for reading finished matches out of Arena logs.
"""

import json
from src.match_history import event_format, parse_log, update_history, summarize

ME, OPP = "ME123", "OPP456"


def login_line():
    return '[UnityCrossThreadLogger]==> Authenticate {"clientId": "%s"}' % ME


def completed_line(match_id, event, my_team, winner):
    players = [
        {"userId": ME, "teamId": my_team, "eventId": event},
        {"userId": OPP, "teamId": 3 - my_team, "eventId": event},
    ]
    msg = {
        "matchGameRoomStateChangedEvent": {
            "gameRoomInfo": {
                "gameRoomConfig": {"reservedPlayers": players, "matchId": match_id},
                "stateType": "MatchGameRoomStateType_MatchCompleted",
                "finalMatchResult": {
                    "matchId": match_id,
                    "resultList": [
                        {"scope": "MatchScope_Game", "winningTeamId": 3 - winner},
                        {"scope": "MatchScope_Match", "winningTeamId": winner},
                    ],
                },
            }
        }
    }
    return "[UnityCrossThreadLogger]" + json.dumps(msg)


def courses_line(event, card_ids):
    deck = {"MainDeck": [{"cardId": int(c), "quantity": 1} for c in card_ids]}
    return json.dumps({"Courses": [{"InternalEventName": event, "CourseDeck": deck}]})


CARDS = {
    "1": {"name": "U1", "types": ["Creature"], "colors": ["U"]},
    "2": {"name": "B1", "types": ["Instant"], "colors": ["B"]},
    "3": {"name": "Island", "types": ["Land", "Basic"], "colors": ["U"]},
}


def lookup(ids):
    return [CARDS[i] for i in ids if i in CARDS]


def write_log(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_event_format():
    assert event_format("QuickDraft_FRA_20261027") == ("QuickDraft", "FRA")
    assert event_format("PremierDraft_FRA_20260929") == ("PremierDraft", "FRA")
    assert event_format("Ladder") == ("Ladder", "")


def test_parse_uses_match_scope_and_my_team():
    text = "\n".join(
        [
            login_line(),
            completed_line("m1", "QuickDraft_FRA_1", my_team=2, winner=2),
            completed_line("m2", "QuickDraft_FRA_1", my_team=1, winner=2),
        ]
    )
    parsed = parse_log(text)
    assert [(m["match_id"], m["won"]) for m in parsed["matches"]] == [("m1", True), ("m2", False)]


def test_history_accumulates_without_duplicates(tmp_path):
    deck = ["1"] * 3 + ["2"] * 3 + ["3"] * 10
    prev = write_log(
        tmp_path, "Player-prev.log",
        [login_line(), courses_line("QuickDraft_FRA_1", deck), completed_line("m1", "QuickDraft_FRA_1", 1, 1)],
    )
    cur = write_log(
        tmp_path, "Player.log",
        [login_line(), courses_line("QuickDraft_FRA_1", deck), completed_line("m1", "QuickDraft_FRA_1", 1, 1),
         completed_line("m2", "QuickDraft_FRA_1", 1, 2)],
    )
    store = str(tmp_path / "history.json")

    history = update_history([prev, cur], lookup, store)
    assert [m["match_id"] for m in history] == ["m1", "m2"]
    assert history[0]["colors"] == "UB" and history[0]["format"] == "QuickDraft"

    again = update_history([cur], lookup, store)
    assert len(again) == 2


def test_missing_logs_are_ignored(tmp_path):
    assert update_history([str(tmp_path / "nope.log")], lookup, str(tmp_path / "h.json")) == []


def test_summarize_groups_and_orders_by_volume():
    history = [
        {"format": "QuickDraft", "set": "FRA", "colors": "UB", "won": True},
        {"format": "QuickDraft", "set": "FRA", "colors": "UB", "won": False},
        {"format": "QuickDraft", "set": "FRA", "colors": "UB", "won": True},
        {"format": "PremierDraft", "set": "FRA", "colors": "WR", "won": True},
    ]
    rows = summarize(history)
    assert (rows[0]["colors"], rows[0]["wins"], rows[0]["losses"]) == ("UB", 2, 1)
    assert rows[1]["win_rate"] == 1.0
