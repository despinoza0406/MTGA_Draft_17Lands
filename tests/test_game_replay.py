"""
tests/test_game_replay.py
Validation for the replay timeline and its rendering helpers.
"""

import json
from src.game_review import GameLog, record_games
from src.ui.windows.game_replay import replay_steps, event_lines, castable_marks

ME, OPP = 1, 2
CARDS = {
    "1": {"name": "Bear", "types": ["Creature"], "cmc": 2, "mana_cost": "{1}{G}"},
    "2": {"name": "Big One", "types": ["Creature"], "cmc": 4, "mana_cost": "{3}{G}"},
    "3": {"name": "Forest", "types": ["Land", "Basic"], "cmc": 0, "mana_cost": ""},
    "4": {"name": "Aura", "types": ["Enchantment"], "cmc": 2, "mana_cost": "{1}{U}"},
    "9": {"name": "Goblin", "types": ["Creature"], "cmc": 1, "mana_cost": "{R}"},
}


def card(g):
    return CARDS.get(str(g), {})


def name(g):
    return card(g).get("name", f"#{g}")


def gre(*messages):
    return "[UnityCrossThreadLogger]" + json.dumps({"greToClientEvent": {"greToClientMessages": list(messages)}})


def state(turn=None, zones=(), objects=(), annotations=(), persistent=(), players=(), stage="GameStage_Play", results=None):
    info = {"matchID": "m", "gameNumber": 1, "stage": stage}
    if results:
        info["results"] = results
    msg = {"type": "GameStateType_Diff", "gameInfo": info, "zones": list(zones), "gameObjects": list(objects),
           "annotations": list(annotations), "persistentAnnotations": list(persistent), "players": list(players)}
    if turn:
        msg["turnInfo"] = turn
    return {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [ME], "gameStateMessage": msg}


def obj(i, grp, zone, owner=ME, types=("CardType_Creature",), **extra):
    return {"instanceId": i, "grpId": grp, "zoneId": zone, "ownerSeatId": owner, "controllerSeatId": owner,
            "type": "GameObjectType_Card", "cardTypes": list(types), **extra}


def test_timeline_snapshots_events_combat_and_attachments():
    bf = {"zoneId": 28, "type": "ZoneType_Battlefield", "objectInstanceIds": [10, 11, 20, 30]}
    hand = {"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": ME, "objectInstanceIds": [12]}
    lines = [
        gre(state(turn={"turnNumber": 1, "activePlayer": ME, "phase": "Phase_Main1"},
                  zones=[bf, hand],
                  objects=[obj(10, 1, 28, power={"value": 2}, toughness={"value": 2}),
                           obj(11, 3, 28, types=("CardType_Land",), subtypes=["SubType_Forest"]),
                           obj(12, 2, 31, power={"value": 4}, toughness={"value": 4}),
                           obj(20, 9, 28, owner=OPP, power={"value": 1}, toughness={"value": 1}),
                           obj(30, 4, 28, types=("CardType_Enchantment",))],
                  persistent=[{"id": 900, "type": ["AnnotationType_Attachment"], "affectorId": 30, "affectedIds": [20]}],
                  players=[{"systemSeatNumber": ME, "lifeTotal": 20}, {"systemSeatNumber": OPP, "lifeTotal": 18}])),
        gre(state(turn={"turnNumber": 1, "activePlayer": ME, "phase": "Phase_Combat", "step": "Step_DeclareBlock"},
                  objects=[obj(10, 1, 28, power={"value": 2}, toughness={"value": 2}, attackState="AttackState_Attacking", isTapped=True)],
                  annotations=[{"type": ["AnnotationType_DamageDealt"], "affectorId": 10, "affectedIds": [OPP],
                                "details": [{"key": "damage", "valueInt32": [2]}]}],
                  players=[{"systemSeatNumber": OPP, "lifeTotal": 16}])),
        gre(state(stage="GameStage_GameOver", results=[{"scope": "MatchScope_Game", "winningTeamId": ME}])),
    ]
    game = record_games("\n".join(lines))[0]
    entry = game.timeline[0]
    assert entry["active"] == "you"
    start, end = entry["start"], entry["end"]
    assert start["hand"] == [2] and start["life"] == [20, 18]
    assert [c[0] for c in start["my_board"]] == [1, 4] and start["my_lands"][0][0] == 3
    goblin = next(c for c in start["opp_board"] if c[0] == 9)
    aura = next(c for c in start["my_board"] + start["opp_board"] if c[0] == 4)
    assert aura[5] == 9 and goblin[5] is None  # the aura is shown on the goblin
    assert end["life"] == [20, 16] and end["my_board"][0][3] is True

    lines_text = event_lines(entry, name)
    assert "Bear dealt 2 to the opponent" in lines_text
    assert any(l.startswith("⚔ You attacked with Bear") for l in lines_text)

    steps = replay_steps(GameLog.from_dict({**game.__dict__, "turns": []}))
    assert [s["phase"] for s in steps] == ["start", "end"]


def test_castable_marks_now_and_with_land_drop():
    snapshot = {"my_lands": [[3, None, None, False]], "hand": [1, 2, 3]}
    marks = castable_marks(snapshot, card)
    assert marks[1] == "with land drop"  # {1}{G}: 1 Forest + the Forest in hand
    assert marks[2] == ""  # 4 mana
    assert 3 not in marks  # lands aren't marked

    snapshot = {"my_lands": [[3, None, None, False], [3, None, None, False]], "hand": [1]}
    assert castable_marks(snapshot, card)[1] == "now"


def test_turn_summary_and_move_rows():
    from src.game_review import GameLog
    from src.ui.windows.game_replay import move_rows, turn_summary

    names = {1: "Island", 2: "Gallia", 3: "Forest", 4: "Beast"}
    name = lambda g: names.get(g, f"#{g}")
    t1 = {"turn": 1, "active": "you", "events": [("land", "you", 1), ("draw", "you", 9)]}
    t2 = {"turn": 2, "active": "opp", "events": [("land", "opp", 3), ("cast", "opp", 4)]}
    t3 = {"turn": 3, "active": "you", "events": [("cast", "you", 2), ("damage", "you", 2, "opp", 2),
                                                  ("damage", "opp", 4, 2, 3)]}
    assert turn_summary(t1, name) == "Island"
    assert turn_summary(t3, name) == "Gallia · hits for 2"
    assert turn_summary({"turn": 4, "active": "opp", "events": []}, name) == "—"

    game = GameLog(match_id="m", game_number=1, timeline=[t1, t2, t3])
    rows = move_rows(game, name, {3})
    assert [(r["round"], r["you"], r["opp"]) for r in rows] == [
        (1, "Island", "Forest · Beast"),
        (2, "?! Gallia · hits for 2", ""),
    ]
