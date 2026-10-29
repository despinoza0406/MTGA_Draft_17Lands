"""
tests/test_game_review.py
Validation for the post-game review: castability, rules of thumb, the GRE
recorder, and persistence.
"""

import json
from src.game_review import (
    GameLog,
    TurnLog,
    can_pay,
    record_games,
    review_game,
    game_summary_text,
    update_reviews,
)

CARDS = {
    "1": {"name": "Bear", "types": ["Creature"], "cmc": 2, "mana_cost": "{1}{G}"},
    "2": {"name": "Shock", "types": ["Instant"], "cmc": 1, "mana_cost": "{R}", "tags": ["removal"]},
    "3": {"name": "Forest", "types": ["Land", "Basic"], "cmc": 0, "mana_cost": ""},
    "4": {"name": "Big Spell", "types": ["Sorcery"], "cmc": 2, "mana_cost": "{U}{U}"},
    "5": {"name": "Counter", "types": ["Instant"], "cmc": 2, "mana_cost": "{U}{U}", "tags": ["removal"], "text": "Counter target spell."},
    "6": {"name": "Slay", "types": ["Sorcery"], "cmc": 2, "mana_cost": "{1}{B}", "tags": ["removal"]},
}
BENCH = {
    "keep7": {"play": {"2": {"games": 1000, "wins": 560}, "1": {"games": 1000, "wins": 300}}},
    "mulligans": {"play": {"1": {"games": 1000, "wins": 410}}},
}


def lookup(ids):
    return [CARDS[i] for i in ids if i in CARDS]


def texts(findings, level="review"):
    return [f.text for f in findings if f.level == level]


def game(turns, **kw):
    defaults = dict(match_id="m", game_number=1, on_play=True, opening_hand_lands=2, won=True)
    return GameLog(turns=turns, **{**defaults, **kw})


def test_can_pay_checks_totals_colors_and_hybrid():
    assert can_pay("{1}{G}", 2, ["G", "U"])
    assert not can_pay("{1}{G}", 2, ["U", "U"])
    assert not can_pay("{U}{U}", 2, ["U", "B"])
    assert can_pay("{U}{U}", 2, ["U", ""])  # unknown land counts as any
    assert can_pay("{1}{G/U}", 2, ["B", "U"])
    assert not can_pay("{3}", 3, ["U", "U"])


def test_missed_land_drop_and_unused_mana():
    turn = TurnLog(3, True, lands_played=0, end_hand=[3, 1], end_hand_lands=1,
                   end_untapped_lands=2, end_untapped_colors=["G", "G"])
    found = texts(review_game(game([turn]), lookup))
    assert any("No land played" in t for t in found)
    assert any("Bear" in t and "untapped" in t for t in found)


def test_unused_mana_respects_colors_and_open_instants():
    wrong_colors = TurnLog(3, True, lands_played=1, end_hand=[4], end_untapped_lands=2, end_untapped_colors=["U", "B"])
    assert texts(review_game(game([wrong_colors]), lookup)) == []

    holding_instant = TurnLog(3, True, lands_played=1, end_hand=[1, 2], end_untapped_lands=2, end_untapped_colors=["G", "R"])
    assert texts(review_game(game([holding_instant]), lookup)) == []


def test_no_attack_into_empty_board():
    turn = TurnLog(5, True, lands_played=1, eligible_attackers=2, opponent_blockers=0, attackers=0)
    assert any("didn't attack" in t for t in texts(review_game(game([turn]), lookup)))
    turn.attackers = 2
    assert texts(review_game(game([turn]), lookup)) == []


def test_held_removal_then_big_hit_but_not_counterspells():
    mine = TurnLog(5, True, lands_played=1, end_hand=[6, 5], end_untapped_lands=2,
                   end_untapped_colors=["B", "U"], opponent_creatures=1, life_end=15)
    theirs = TurnLog(6, False, life_end=9)
    found = texts(review_game(game([mine, theirs]), lookup))
    assert any("Held Slay" in t for t in found)
    assert not any("Counter" in t for t in found)


def test_mulligan_benchmarks():
    keep = review_game(game([], opening_hand_lands=2), lookup, BENCH)
    assert keep[0].level == "ok" and "56%" in keep[0].text

    bad_keep = review_game(game([], opening_hand_lands=1), lookup, BENCH)
    assert bad_keep[0].level == "review"

    mulled_good_hand = review_game(game([], mulligans=1, first_hand_lands=2), lookup, BENCH)
    assert mulled_good_hand[0].level == "review" and "Mulliganed" in mulled_good_hand[0].text


def test_screw_is_info_not_a_decision():
    turns = [TurnLog(n, True, lands_played=1 if n < 3 else 0, lands_on_battlefield=min(n, 2)) for n in range(1, 5)]
    findings = review_game(game(turns), lookup)
    assert any("Mana screw" in f.text and f.level == "info" for f in findings)


# --- recorder over synthetic GRE messages ---

ME, OPP = 1, 2


def gre(*messages):
    return "[UnityCrossThreadLogger]" + json.dumps({"greToClientEvent": {"greToClientMessages": list(messages)}})


def state(turn=None, zones=(), objects=(), annotations=(), players=(), results=None, stage="GameStage_Play"):
    info = {"matchID": "match-1", "gameNumber": 1, "stage": stage}
    if results:
        info["results"] = results
    msg = {"type": "GameStateType_Diff", "gameInfo": info, "zones": list(zones), "gameObjects": list(objects),
           "annotations": list(annotations), "players": list(players)}
    if turn:
        msg["turnInfo"] = turn
    return {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [ME], "gameStateMessage": msg}


def obj(i, grp, zone, owner=ME, types=("CardType_Land",), **extra):
    return {"instanceId": i, "grpId": grp, "zoneId": zone, "ownerSeatId": owner, "controllerSeatId": owner,
            "type": "GameObjectType_Card", "cardTypes": list(types), **extra}


def transfer(i, category):
    return {"type": ["AnnotationType_ZoneTransfer"], "affectedIds": [i],
            "details": [{"key": "category", "valueString": [category]}]}


def test_recorder_builds_turns_from_gre():
    hand = {"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": ME, "objectInstanceIds": [10, 11, 12]}
    lines = [
        gre({"type": "GREMessageType_ConnectResp", "systemSeatIds": [ME],
             "connectResp": {"deckMessage": {"deckCards": [3, 3, 1]}}}),
        gre(state(zones=[hand], objects=[obj(10, 3, 31), obj(11, 3, 31), obj(12, 1, 31, types=("CardType_Creature",))],
                  players=[{"systemSeatNumber": ME, "lifeTotal": 20}, {"systemSeatNumber": OPP, "lifeTotal": 20}])),
        gre({"type": "GREMessageType_MulliganReq", "systemSeatIds": [ME]}),
        gre(state(turn={"turnNumber": 1, "activePlayer": ME, "phase": "Phase_Main1"}, zones=[hand])),
        gre(state(
            turn={"turnNumber": 1, "activePlayer": ME, "phase": "Phase_Main2"},
            zones=[{"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": ME, "objectInstanceIds": [11, 12]},
                   {"zoneId": 28, "type": "ZoneType_Battlefield", "objectInstanceIds": [20]}],
            objects=[obj(20, 3, 28, subtypes=["SubType_Forest"])],
            annotations=[transfer(20, "PlayLand")],
        )),
        gre(state(turn={"turnNumber": 2, "activePlayer": OPP, "phase": "Phase_Main1"},
                  players=[{"systemSeatNumber": ME, "lifeTotal": 17}])),
        gre(state(stage="GameStage_GameOver", results=[{"scope": "MatchScope_Game", "winningTeamId": ME}])),
    ]
    games = record_games("\n".join(lines))

    assert len(games) == 1
    g = games[0]
    assert g.on_play and g.won and g.mulligans == 0
    assert g.first_hand_lands == 2 and g.opening_hand_lands == 2
    first, second = g.turns
    assert first.mine and first.lands_played == 1
    assert first.end_hand == [3, 1] and first.end_hand_lands == 1
    assert first.end_untapped_lands == 1 and first.end_untapped_colors == ["G"]
    assert not second.mine and second.life_end == 17

    text = game_summary_text(g, lookup, review_game(g, lookup))
    assert "WON" in text and "T1 You: lands played 1" in text


def test_update_reviews_persists_finished_games_once(tmp_path):
    finished = "\n".join([
        gre(state(turn={"turnNumber": 1, "activePlayer": ME})),
        gre(state(stage="GameStage_GameOver", results=[{"scope": "MatchScope_Game", "winningTeamId": OPP}])),
    ])
    log = tmp_path / "Player.log"
    log.write_text(finished, encoding="utf-8")
    store = tmp_path / "reviews.json"

    stored = update_reviews([str(log)], {"match-1": "QuickDraft_FRA_1"}, str(store))
    assert list(stored) == ["match-1#1"]
    assert stored["match-1#1"]["event"] == "QuickDraft_FRA_1" and stored["match-1#1"]["won"] is False

    again = update_reviews([str(log)], {}, str(store))
    assert again["match-1#1"]["event"] == "QuickDraft_FRA_1"
    assert GameLog.from_dict(again["match-1#1"]).turns[0].number == 1


# --- board, mana use, deck, patterns ---

from src.game_review import mana_efficiency, first_turn_behind, biggest_hit, deck_findings, patterns, _card_cache


def card_fn():
    return _card_cache(lookup)


def test_mana_efficiency_counts_instants_on_the_opponents_turn():
    turns = []
    for n in range(1, 13):
        mine = n % 2 == 1
        t = TurnLog(n, mine, lands_on_battlefield=(n + 1) // 2 if mine else 0)
        if mine and n in (3, 5):
            t.spells_cast = [1]  # Bear, cmc 2
        if not mine and n == 6:
            t.spells_cast = [2]  # Shock cast in the opponent's turn 6, cmc 1
        turns.append(t)
    g = game(turns)
    # my turns 2-6 = turns 3,5,7,9,11 with 2,3,4,5,6 lands = 20 available; spent 2+2+1 = 5
    assert mana_efficiency(g, card_fn()) == 5 / 20
    assert any(f.kind == "Low mana use" for f in review_game(g, lookup))


def test_behind_on_board_and_biggest_hit():
    turns = [
        TurnLog(3, True, my_creatures=1, my_power=2, opponent_creatures=1, opponent_power=2, life_end=20),
        TurnLog(4, False, my_creatures=1, my_power=2, opponent_creatures=3, opponent_power=7, life_end=18),
        TurnLog(5, True, life_end=18),
        TurnLog(6, False, life_end=10),
    ]
    g = game(turns)
    assert first_turn_behind(g).number == 4
    assert biggest_hit(g) == (6, 8)


def test_deck_findings_only_for_limited_decks():
    bench = {"deck": {"creatures": {str(k): {"games": 5000, "wins": 2500 + 100 * (12 - k)} for k in range(8, 17)},
                      "instants": {}}}
    limited = game([], deck=[1] * 12 + [3] * 17 + [4] * 11)  # 12 creatures, 40 cards
    found = deck_findings(limited, card_fn(), bench)
    assert found and "12 creatures" in found[0].text and "~8" in found[0].text

    constructed = game([], deck=[1] * 24 + [3] * 36)
    assert deck_findings(constructed, card_fn(), bench) == []


def test_patterns_report_variance_sides_and_length():
    def g(won, on_play, n_turns):
        return game([TurnLog(i, i % 2 == 1) for i in range(1, n_turns + 1)], won=won, on_play=on_play)

    reviews = [(g(True, True, 10), []), (g(False, False, 20), [])]
    lines = patterns(reviews, lookup)
    assert lines[0] == "2 games: 1-1."
    assert "by luck alone" in lines[1] and "too few games" in lines[1]
    assert "On the play: 1-0." in lines and "On the draw: 0-1." in lines
    assert "Game length: 10 turns in wins vs 20 in losses." in lines


def test_old_records_are_re_recorded(tmp_path):
    finished = "\n".join([
        gre(state(turn={"turnNumber": 1, "activePlayer": ME})),
        gre(state(stage="GameStage_GameOver", results=[{"scope": "MatchScope_Game", "winningTeamId": ME}])),
    ])
    log = tmp_path / "Player.log"
    log.write_text(finished, encoding="utf-8")
    store = tmp_path / "reviews.json"
    store.write_text(json.dumps({"match-1#1": {"match_id": "match-1", "game_number": 1, "event": "QuickDraft_FRA_1", "won": True, "turns": []}}))

    stored = update_reviews([str(log)], {}, str(store))
    assert stored["match-1#1"]["version"] >= 2
    assert stored["match-1#1"]["event"] == "QuickDraft_FRA_1"  # kept from the old record
    assert stored["match-1#1"]["turns"]


def test_held_castable_creature_even_with_instants_open():
    def mine(n):
        return TurnLog(n, True, lands_played=1, end_hand=[1, 2], end_untapped_lands=2, end_untapped_colors=["G", "R"])
    turns = [mine(3), TurnLog(4, False, spells_cast=[2]), mine(5), TurnLog(6, False, spells_cast=[2])]
    findings = review_game(game(turns), lookup)
    held = [f for f in findings if f.kind == "Held a castable creature"]
    assert len(held) == 1 and held[0].turn == 5 and "Bear" in held[0].text


def test_unused_mana_held_for_instants_is_flagged_only_when_unused():
    mine = TurnLog(3, True, lands_played=1, end_hand=[1, 2], end_untapped_lands=2, end_untapped_colors=["G", "R"])
    used = review_game(game([mine, TurnLog(4, False, spells_cast=[2])]), lookup)
    assert not any(f.kind == "Unused mana held for instants" for f in used)
    unused = review_game(game([mine, TurnLog(4, False)]), lookup)
    assert any(f.kind == "Unused mana held for instants" for f in unused)


def test_risky_attack_into_a_crack_back():
    risky = TurnLog(9, True, lands_played=1, attackers=2, eligible_attackers=2, life_end=11, opponent_power=10)
    assert any(f.kind == "Risky attack" for f in review_game(game([risky]), lookup))
    safe = TurnLog(9, True, lands_played=1, attackers=2, eligible_attackers=2, life_end=20, opponent_power=10)
    assert not any(f.kind == "Risky attack" for f in review_game(game([safe]), lookup))


def test_weaker_card_cast_first_and_flash_is_instant_speed():
    cards = {
        "10": {"name": "Bomb", "types": ["Planeswalker"], "cmc": 5, "mana_cost": "{3}{B}{B}",
               "deck_colors": {"All Decks": {"gihwr": 65.9}}},
        "11": {"name": "Filler", "types": ["Creature"], "cmc": 5, "mana_cost": "{4}{U}",
               "deck_colors": {"All Decks": {"gihwr": 55.0}}},
        "12": {"name": "Flasher", "types": ["Creature"], "cmc": 1, "mana_cost": "{U}", "keywords": ["Flash"],
               "deck_colors": {"All Decks": {"gihwr": 53.0}}},
    }

    def lk(ids):
        return [cards[i] for i in ids if i in cards]

    turn = TurnLog(9, True, lands_played=1, spells_cast=[11], end_hand=[10, 12], lands_on_battlefield=5,
                   land_colors=["B", "B", "U", "U", "U"], end_untapped_lands=1, end_untapped_colors=["U"])
    findings = review_game(game([turn, TurnLog(10, False)]), lk)
    weaker = [f for f in findings if f.kind == "Weaker card cast first"]
    assert len(weaker) == 1 and "Bomb" in weaker[0].text and "Filler" in weaker[0].text
    # Flasher (flash) with mana open is a deliberate hold, not unused mana
    assert not any(f.kind in ("Unused mana", "Unused mana held for instants") and "Flasher" in f.text for f in findings)

    no_colors = TurnLog(9, True, lands_played=1, spells_cast=[11], end_hand=[10], lands_on_battlefield=5,
                        land_colors=["U"] * 5)
    assert not any(f.kind == "Weaker card cast first" for f in review_game(game([no_colors]), lk))


PRIORITY_CARDS = {
    "20": {"name": "Big Flyer", "types": ["Creature"], "cmc": 3, "mana_cost": "{1}{U}{U}",
           "deck_colors": {"All Decks": {"gihwr": 61.0}}},
    "21": {"name": "Wall", "types": ["Creature"], "cmc": 5, "mana_cost": "{4}{U}",
           "deck_colors": {"All Decks": {"gihwr": 55.0}}},
    "22": {"name": "Draw Spell", "types": ["Sorcery"], "cmc": 3, "mana_cost": "{2}{U}",
           "deck_colors": {"All Decks": {"gihwr": 55.0}}},
}


def plk(ids):
    return [PRIORITY_CARDS[i] for i in ids if i in PRIORITY_CARDS]


def priority_turns(opp_power, cast, later_cast=()):
    return [
        TurnLog(9, False, my_power=0, opponent_power=opp_power, life_end=15),
        TurnLog(10, True, lands_played=1, spells_cast=list(cast), end_hand=[20], lands_on_battlefield=5,
                land_colors=["U"] * 5, life_start=15),
        TurnLog(11, False),
        TurnLog(12, True, lands_played=1, spells_cast=list(later_cast), lands_on_battlefield=6, land_colors=["U"] * 6),
    ]


def weaker(findings):
    return [f for f in findings if f.kind == "Weaker card cast first"]


def test_priority_under_pressure_with_a_sturdier_blocker_is_info():
    g = game(priority_turns(opp_power=6, cast=[21]), card_stats={20: [3, 3], 21: [3, 5]})
    found = weaker(review_game(g, plk))
    assert len(found) == 1 and found[0].level == "info" and "you 0 power vs opponent 6" in found[0].text


def test_priority_without_pressure_is_review():
    g = game(priority_turns(opp_power=0, cast=[21]), card_stats={20: [3, 3], 21: [3, 5]})
    found = weaker(review_game(g, plk))
    assert len(found) == 1 and found[0].level == "review"


def test_priority_skips_a_planned_double_spell():
    g = game(priority_turns(opp_power=0, cast=[22, 21], later_cast=[20, 22]), card_stats={20: [3, 3]})
    assert weaker(review_game(g, plk)) == []
