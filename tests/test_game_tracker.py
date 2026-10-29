"""
tests/test_game_tracker.py
Validation for the in-game tracker built from Arena GRE messages.
"""

import json
import time
from src.game_tracker import GameTracker, LogTailer

ME, OPP = 1, 2
BEAR, BOLT, ISLAND, OPP_CARD = 101, 102, 103, 900


def gre(*messages):
    return "[UnityCrossThreadLogger]" + json.dumps({"greToClientEvent": {"greToClientMessages": list(messages)}}) + "\n"


def connect(deck):
    return {"type": "GREMessageType_ConnectResp", "systemSeatIds": [ME], "connectResp": {"deckMessage": {"deckCards": deck}}}


def state(zones=(), objects=(), annotations=(), match="m1", game=1, stage="GameStage_Play", full=False, deleted=()):
    msg = {
        "type": "GameStateType_Full" if full else "GameStateType_Diff",
        "gameInfo": {"matchID": match, "gameNumber": game, "stage": stage},
        "zones": list(zones),
        "gameObjects": list(objects),
        "annotations": list(annotations),
        "diffDeletedInstanceIds": list(deleted),
    }
    return {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [ME], "gameStateMessage": msg}


def zone(zone_id, kind, ids, owner=None):
    z = {"zoneId": zone_id, "type": kind, "objectInstanceIds": list(ids)}
    if owner is not None:
        z["ownerSeatId"] = owner
    return z


def card(instance, grp, zone_id, owner=ME):
    return {"instanceId": instance, "grpId": grp, "zoneId": zone_id, "ownerSeatId": owner, "type": "GameObjectType_Card"}


DECK = [BEAR] * 2 + [BOLT] * 2 + [ISLAND] * 6  # 10-card toy deck


def start_game(tracker, **kw):
    tracker.feed_line(gre(connect(DECK)))
    tracker.feed_line(
        gre(
            state(
                zones=[
                    zone(31, "ZoneType_Hand", [1, 2], ME),
                    zone(32, "ZoneType_Library", range(10, 18), ME),
                    zone(28, "ZoneType_Battlefield", []),
                ],
                objects=[card(1, BEAR, 31), card(2, ISLAND, 31)],
                full=True,
                **kw,
            )
        )
    )


def test_library_odds_exclude_cards_out_of_the_library():
    tracker = GameTracker()
    start_game(tracker)
    snap = tracker.snapshot()

    assert snap["active"] and snap["library_size"] == 8
    assert snap["remaining"] == {BEAR: 1, BOLT: 2, ISLAND: 5}
    assert snap["draw_odds"][BOLT] == 2 / 8


def test_playing_a_card_keeps_counts_consistent():
    tracker = GameTracker()
    start_game(tracker)
    # Bear moves to the battlefield and gets a new instance id
    tracker.feed_line(
        gre(
            state(
                zones=[zone(31, "ZoneType_Hand", [2], ME), zone(28, "ZoneType_Battlefield", [50])],
                objects=[card(50, BEAR, 28)],
                deleted=[1],
            )
        )
    )
    snap = tracker.snapshot()
    assert snap["remaining"][BEAR] == 1
    assert sum(snap["remaining"].values()) == snap["library_size"]


def test_opponent_cards_are_counted_once_across_zone_changes():
    tracker = GameTracker()
    start_game(tracker)
    tracker.feed_line(gre(state(zones=[zone(28, "ZoneType_Battlefield", [70])], objects=[card(70, OPP_CARD, 28, OPP)])))
    # Same card dies: new id 71 in the graveyard, linked by ObjectIdChanged
    changed = {
        "type": ["AnnotationType_ObjectIdChanged"],
        "details": [{"key": "orig_id", "valueInt32": [70]}, {"key": "new_id", "valueInt32": [71]}],
    }
    tracker.feed_line(
        gre(
            state(
                zones=[zone(28, "ZoneType_Battlefield", []), zone(37, "ZoneType_Graveyard", [71], OPP)],
                objects=[card(71, OPP_CARD, 37, OPP)],
                annotations=[changed],
                deleted=[70],
            )
        )
    )
    assert tracker.snapshot()["opponent"] == {OPP_CARD: 1}


def test_opponent_hand_is_not_revealed():
    tracker = GameTracker()
    start_game(tracker)
    tracker.feed_line(gre(state(zones=[zone(41, "ZoneType_Hand", [80], OPP)], objects=[card(80, OPP_CARD, 41, OPP)])))
    assert tracker.snapshot()["opponent"] == {}


def test_new_game_resets_and_game_over_deactivates():
    tracker = GameTracker()
    start_game(tracker)
    tracker.feed_line(gre(state(zones=[zone(28, "ZoneType_Battlefield", [70])], objects=[card(70, OPP_CARD, 28, OPP)])))
    tracker.feed_line(gre(state(stage="GameStage_GameOver")))
    assert not tracker.snapshot()["active"]

    start_game(tracker, game=2)
    snap = tracker.snapshot()
    assert snap["active"] and snap["opponent"] == {} and snap["game"] == ("m1", 2)


def test_ignores_unrelated_and_broken_lines():
    tracker = GameTracker()
    assert not tracker.feed_line("[UnityCrossThreadLogger] something else\n")
    assert not tracker.feed_line('greToClientEvent {"broken": \n')
    assert tracker.snapshot()["active"] is False


def test_tailer_follows_appends_and_truncation(tmp_path):
    log = tmp_path / "Player.log"
    log.write_text(gre(connect(DECK)), encoding="utf-8")
    tracker = GameTracker()
    tailer = LogTailer(str(log), tracker, interval=0.05)
    tailer.start()
    try:
        deadline = time.time() + 2
        while not tracker.deck and time.time() < deadline:
            time.sleep(0.05)
        assert sum(tracker.deck.values()) == 10

        log.write_text(gre(connect([BOLT] * 3)), encoding="utf-8")  # Arena restart
        deadline = time.time() + 2
        while sum(tracker.deck.values()) != 3 and time.time() < deadline:
            time.sleep(0.05)
        assert tracker.deck == {BOLT: 3}
    finally:
        tailer.stop()
        tailer.join(timeout=2)
    assert not tailer.is_alive()
