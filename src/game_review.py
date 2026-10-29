"""
src/game_review.py
Post-game review. GameRecorder replays Arena's detailed GRE log into a compact
turn-by-turn record of each game you played; review_game() runs pro-player
rules of thumb over that record and flags decisions worth a second look.

Everything here runs after the game, on your own logs: it never suggests a
play while a match is in progress.
"""

import os
import re
import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Callable, Dict, List, Optional

from src import constants
from src.game_tracker import GameTracker

logger = logging.getLogger(__name__)

REVIEWS_FILE = os.path.join(constants.BASE_DIR, "Game_Reviews.json")
MIN_BENCHMARK_GAMES = 300
# Bump when GameLog gains fields, so stored games are re-recorded from the logs.
RECORD_VERSION = 4
LIFE_SWING_FOR_REMOVAL_CHECK = 4


BASIC_LAND_COLORS = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}


def _is_type(obj: Dict, kind: str) -> bool:
    return f"CardType_{kind}" in obj.get("cardTypes", [])


def _land_colors(obj: Dict) -> str:
    """Colors a land taps for, from its basic land types; "" when unknown (treated as any)."""
    return "".join(
        color for sub, color in BASIC_LAND_COLORS.items() if f"SubType_{sub}" in obj.get("subtypes", [])
    )


def can_pay(mana_cost: str, cmc: float, sources: List[str]) -> bool:
    """Rough castability: enough lands in total and, per color, enough lands of that
    color (lands of unknown color count for any). Hybrid pips need one option."""
    if cmc > len(sources):
        return False
    wildcard = sum(1 for s in sources if not s)
    for pip in re.findall(r"\{(.*?)\}", mana_cost or ""):
        options = [c for c in pip.split("/") if c in "WUBRG"]
        if not options:
            continue
        if not any(any(c in s for s in sources) for c in options) and not wildcard:
            return False
    for color in "WUBRG":
        needed = sum(1 for pip in re.findall(r"\{(.*?)\}", mana_cost or "") if pip == color)
        if needed > sum(1 for s in sources if color in s) + wildcard:
            return False
    return True


def _instant_speed(card: Dict) -> bool:
    return "Instant" in card.get("types", []) or "Flash" in (card.get("keywords") or [])


def _gihwr(card: Dict) -> float:
    return float(card.get("deck_colors", {}).get("All Decks", {}).get("gihwr", 0.0) or 0.0)


def _is_counterspell(card: Dict) -> bool:
    text = str(card.get("oracle_text", card.get("text", ""))).lower()
    return "counter target" in text


@dataclass
class TurnLog:
    number: int
    mine: bool
    lands_played: int = 0
    spells_cast: List[int] = field(default_factory=list)
    end_hand: List[int] = field(default_factory=list)
    end_hand_lands: int = 0
    end_untapped_lands: int = 0
    end_untapped_colors: List[str] = field(default_factory=list)
    land_colors: List[str] = field(default_factory=list)  # all your lands at end of turn
    lands_on_battlefield: int = 0
    eligible_attackers: int = 0
    opponent_blockers: int = 0
    opponent_creatures: int = 0
    attackers: int = 0
    # Board at the end of this turn (either player's turn)
    my_creatures: int = 0
    my_power: int = 0
    opponent_power: int = 0
    life_start: Optional[int] = None
    life_end: Optional[int] = None
    opponent_life_end: Optional[int] = None


@dataclass
class GameLog:
    match_id: str
    game_number: int
    event: str = ""
    on_play: Optional[bool] = None
    mulligans: int = 0
    first_hand_lands: Optional[int] = None
    opening_hand: List[int] = field(default_factory=list)
    opening_hand_lands: Optional[int] = None
    won: Optional[bool] = None
    deck: List[int] = field(default_factory=list)
    card_stats: Dict[int, List[int]] = field(default_factory=dict)  # your cards: grpId -> [power, toughness]
    turns: List[TurnLog] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.match_id}#{self.game_number}"

    @staticmethod
    def from_dict(data: Dict) -> "GameLog":
        data = {k: v for k, v in data.items() if k != "version"}
        turns = [TurnLog(**t) for t in data.get("turns", [])]
        stats = {int(k): v for k, v in (data.get("card_stats") or {}).items()}  # JSON keys are strings
        return GameLog(**{**data, "turns": turns, "card_stats": stats})


class GameRecorder(GameTracker):
    """GameTracker that also keeps a per-turn record of every game."""

    def __init__(self):
        super().__init__()
        self.games: List[GameLog] = []
        self.current: Optional[GameLog] = None
        self.turn_number = 0
        self.active_seat = None
        self.lives: Dict[int, int] = {}
        self._mulligan_prompts = 0

    # --- helpers over the tracked state ---
    def _zone_objects(self, kind: str, owner: Optional[int] = None) -> List[Dict]:
        out = []
        for zone in self.zones.values():
            if zone.get("type") != kind or (owner is not None and zone.get("ownerSeatId") != owner):
                continue
            out += [self.objects[i] for i in zone.get("objectInstanceIds", []) if i in self.objects]
        return out

    def _battlefield(self, controller: int) -> List[Dict]:
        return [o for o in self._zone_objects("ZoneType_Battlefield") if o.get("controllerSeatId") == controller]

    def _opponent(self) -> Optional[int]:
        return None if self.seat is None else 3 - self.seat

    def _turn(self) -> Optional[TurnLog]:
        if not self.current or not self.current.turns:
            return None
        return self.current.turns[-1]

    # --- message handling ---
    def _handle(self, message: Dict):
        before = self.game_key
        super()._handle(message)
        if self.game_key and self.game_key != before:
            self.current = GameLog(self.game_key[0], self.game_key[1], deck=list(self.deck.elements()))
            self.games.append(self.current)
            self.turn_number, self._mulligan_prompts = 0, 0
        if not self.current:
            return
        if message.get("type") == "GREMessageType_ConnectResp" and self.deck:
            self.current.deck = list(self.deck.elements())  # sideboarded deck for this game

        if message.get("type") == "GREMessageType_MulliganReq":
            if self._mulligan_prompts == 0:
                hand = self._zone_objects("ZoneType_Hand", self.seat)
                if hand:
                    self.current.first_hand_lands = sum(_is_type(o, "Land") for o in hand)
            self._mulligan_prompts += 1
            self.current.mulligans = self._mulligan_prompts - 1
            return

        state = message.get("gameStateMessage")
        if not state:
            return
        for player in state.get("players", []):
            if "lifeTotal" in player:
                self.lives[player.get("systemSeatNumber")] = player["lifeTotal"]
        for obj in state.get("gameObjects", []):
            if obj.get("ownerSeatId") == self.seat and "toughness" in obj and obj.get("type") == "GameObjectType_Card":
                self.current.card_stats.setdefault(
                    obj["grpId"], [(obj.get("power") or {}).get("value", 0), (obj.get("toughness") or {}).get("value", 0)]
                )
        info = state.get("gameInfo") or {}
        for result in info.get("results", []):
            if result.get("scope") == "MatchScope_Game" and result.get("winningTeamId") is not None:
                self.current.won = result["winningTeamId"] == self.seat

        turn_info = state.get("turnInfo") or {}
        number = turn_info.get("turnNumber")
        if number and number != self.turn_number:
            self.turn_number = number
            self.active_seat = turn_info.get("activePlayer", self.active_seat)
            if number == 1:
                self.current.on_play = self.active_seat == self.seat
                hand = self._zone_objects("ZoneType_Hand", self.seat)
                self.current.opening_hand = [o["grpId"] for o in hand]
                self.current.opening_hand_lands = sum(_is_type(o, "Land") for o in hand)
            self.current.turns.append(TurnLog(number, self.active_seat == self.seat, life_start=self._life(self.seat)))

        turn = self._turn()
        if not turn:
            return
        for annotation in state.get("annotations", []):
            if "AnnotationType_ZoneTransfer" not in annotation.get("type", []):
                continue
            category = next(
                (d.get("valueString", [""])[0] for d in annotation.get("details", []) if d["key"] == "category"),
                "",
            )
            for affected in annotation.get("affectedIds", []):
                obj = self.objects.get(affected, {})
                if obj.get("ownerSeatId") != self.seat:
                    continue
                if category == "PlayLand":
                    turn.lands_played += 1
                elif category == "CastSpell":
                    turn.spells_cast.append(obj.get("grpId"))

        self._snapshot_turn(turn, turn_info)

    def _life(self, seat) -> Optional[int]:
        return self.lives.get(seat)

    def _snapshot_turn(self, turn: TurnLog, turn_info: Dict):
        opp = self._opponent()
        turn.life_end = self._life(self.seat)
        turn.opponent_life_end = self._life(opp)
        step, phase = turn_info.get("step"), turn_info.get("phase")
        mine_bf = self._battlefield(self.seat)
        opp_bf = self._battlefield(opp) if opp else []
        if phase in ("Phase_Main2", "Phase_Ending") or step == "Step_End":
            mine_c = [o for o in mine_bf if _is_type(o, "Creature")]
            opp_c = [o for o in opp_bf if _is_type(o, "Creature")]
            turn.my_creatures, turn.opponent_creatures = len(mine_c), len(opp_c)
            turn.my_power = sum((o.get("power") or {}).get("value", 0) for o in mine_c)
            turn.opponent_power = sum((o.get("power") or {}).get("value", 0) for o in opp_c)
        if not turn.mine:
            return
        if step == "Step_BeginCombat":
            turn.eligible_attackers = sum(
                1 for o in mine_bf
                if _is_type(o, "Creature") and not o.get("isTapped") and not o.get("hasSummoningSickness")
            )
            turn.opponent_blockers = sum(1 for o in opp_bf if _is_type(o, "Creature") and not o.get("isTapped"))
        if step in ("Step_DeclareAttack", "Step_DeclareBlock", "Step_CombatDamage"):
            attacking = sum(1 for o in mine_bf if str(o.get("attackState", "")).startswith("AttackState_"))
            turn.attackers = max(turn.attackers, attacking)
        if phase in ("Phase_Main2", "Phase_Ending") or step == "Step_End":
            hand = self._zone_objects("ZoneType_Hand", self.seat)
            turn.end_hand = [o["grpId"] for o in hand]
            turn.end_hand_lands = sum(_is_type(o, "Land") for o in hand)
            lands = [o for o in mine_bf if _is_type(o, "Land")]
            turn.lands_on_battlefield = len(lands)
            turn.land_colors = [_land_colors(o) for o in lands]
            untapped = [o for o in lands if not o.get("isTapped")]
            turn.end_untapped_lands = len(untapped)
            turn.end_untapped_colors = [_land_colors(o) for o in untapped]


def record_games(log_text: str) -> List[GameLog]:
    recorder = GameRecorder()
    for line in log_text.splitlines():
        recorder.feed_line(line)
    return [g for g in recorder.games if g.turns]


# --- review ---------------------------------------------------------------


@dataclass
class Finding:
    turn: int
    level: str  # "ok", "review", "info"
    text: str
    kind: str = ""  # short label used to group flags across games


def _rate(cell: Optional[Dict]):
    if not cell or not cell.get("games"):
        return None, 0
    return cell["wins"] / cell["games"], cell["games"]


def _mulligan_findings(game: GameLog, benchmarks: Optional[Dict]) -> List[Finding]:
    if not benchmarks or game.on_play is None:
        return []
    side = "play" if game.on_play else "draw"
    keep7 = benchmarks.get("keep7", {}).get(side, {})
    after_mull, mull_games = _rate(benchmarks.get("mulligans", {}).get(side, {}).get("1"))
    out = []
    if game.mulligans == 0 and game.opening_hand_lands is not None:
        keep, games = _rate(keep7.get(str(game.opening_hand_lands)))
        if keep is not None and games >= MIN_BENCHMARK_GAMES and after_mull is not None:
            text = (
                f"Kept 7 with {game.opening_hand_lands} lands on the {side}: such keeps win {keep:.0%} "
                f"vs {after_mull:.0%} after a mulligan (17Lands, {games:,} games; rough guide, the spells matter too)."
            )
            out.append(Finding(0, "ok" if keep >= after_mull else "review", text, "Risky keep"))
        elif games:
            out.append(Finding(0, "info", f"Kept 7 with {game.opening_hand_lands} lands: too few 17Lands games ({games}) to judge."))
    elif game.mulligans > 0 and game.first_hand_lands is not None:
        keep, games = _rate(keep7.get(str(game.first_hand_lands)))
        if keep is not None and games >= MIN_BENCHMARK_GAMES and after_mull is not None:
            text = (
                f"Mulliganed a 7 with {game.first_hand_lands} lands on the {side}: keeping such hands wins "
                f"{keep:.0%}, mulliganing {after_mull:.0%} (17Lands; rough guide, the spells matter too)."
            )
            out.append(Finding(0, "review" if keep > after_mull + 0.05 else "ok", text, "Mulliganed a keepable 7"))
    return out


def review_game(game: GameLog, lookup: Callable[[List[str]], List[Dict]], benchmarks: Optional[Dict] = None) -> List[Finding]:
    card = _card_cache(lookup)

    def name(grp_id) -> str:
        return card(grp_id).get(constants.DATA_FIELD_NAME) or f"#{grp_id}"

    findings = _mulligan_findings(game, benchmarks)
    my_turns = [t for t in game.turns if t.mine]
    held_turns: Dict[int, int] = {}  # creature grpId -> consecutive turns held while castable
    prioritized = set()  # cards already flagged by the prioritization check
    for index, turn in enumerate(my_turns, start=1):
        if turn.lands_played == 0 and turn.end_hand_lands > 0:
            findings.append(Finding(turn.number, "review", "No land played, but you had a land in hand.", "Missed land drop"))

        sources = turn.end_untapped_colors or [""] * turn.end_untapped_lands

        def payable(g):
            c = card(g)
            cmc = float(c.get("cmc", 0) or 0)
            return cmc > 0 and can_pay(c.get("mana_cost", ""), cmc, sources)

        castable = [
            g for g in turn.end_hand
            if "Land" not in card(g).get("types", []) and not _instant_speed(card(g)) and payable(g)
        ]
        # Mana left open for an instant (or flash card) you could cast is often deliberate.
        held_up = [g for g in turn.end_hand if _instant_speed(card(g)) and payable(g)]
        following = next((t for t in game.turns if t.number == turn.number + 1 and not t.mine), None)
        if castable and held_up and following is not None and not following.spells_cast:
            findings.append(
                Finding(
                    turn.number,
                    "review",
                    f"Kept {turn.end_untapped_lands} mana open for {name(held_up[0])} but cast nothing on the "
                    f"opponent's turn, while {name(castable[0])} sat in hand.",
                    "Unused mana held for instants",
                )
            )
        if castable and not held_up:
            best = max(castable, key=lambda g: float(card(g).get("cmc", 0)))
            findings.append(
                Finding(
                    turn.number,
                    "review",
                    f"Ended your turn with {turn.end_untapped_lands} untapped lands while holding "
                    f"{name(best)} (cost {card(best).get('cmc')}). Unused mana, if you had the colors.",
                    "Unused mana",
                )
            )

        # Creatures kept in hand with the mana to cast them, even when that mana
        # stayed open for instants: in Limited, developing usually beats holding up.
        castable_creatures = {
            g for g in turn.end_hand
            if "Creature" in card(g).get("types", []) and not _instant_speed(card(g)) and payable(g)
        }
        held_turns = {g: held_turns.get(g, 0) + 1 for g in castable_creatures}
        for g, held in held_turns.items():
            if held == HELD_CREATURE_TURNS:
                findings.append(
                    Finding(
                        turn.number,
                        "review",
                        f"Held {name(g)} for {held} turns with the mana to cast it"
                        + (" (kept mana open for instants instead)" if held_up else "")
                        + ". Deploying threats on curve usually beats holding counters in Limited.",
                        "Held a castable creature",
                    )
                )

        # Prioritization: you spent at least as much mana on weaker cards as a
        # clearly better card in hand would have cost, and your lands could pay for it.
        cast_here = [g for g in turn.spells_cast if card(g)]
        spent = sum(float(card(g).get("cmc", 0) or 0) for g in cast_here)
        best_cast = max((_gihwr(card(g)) for g in cast_here), default=None)
        all_lands = turn.land_colors or [""] * turn.lands_on_battlefield
        if best_cast is not None:
            better = [
                g for g in set(turn.end_hand)
                if g not in prioritized
                and "Land" not in card(g).get("types", [])
                and not _instant_speed(card(g))
                and 0 < float(card(g).get("cmc", 0) or 0) <= spent
                and _gihwr(card(g)) >= best_cast + PRIORITY_GAP
                and can_pay(card(g).get("mana_cost", ""), float(card(g).get("cmc", 0) or 0), all_lands)
            ]
            if better:
                g = max(better, key=lambda x: _gihwr(card(x)))
                prioritized.add(g)
                verdict = _priority_context(game, turn, g, cast_here, card)
                if verdict:
                    level, context = verdict
                    cast_names = ", ".join(name(x) for x in cast_here)
                    findings.append(
                        Finding(
                            turn.number,
                            level,
                            f"Cast {cast_names} while {name(g)} (GIH {_gihwr(card(g)):.1f}% vs {best_cast:.1f}%) stayed in hand "
                            f"and your lands could pay for it. {context}",
                            "Weaker card cast first",
                        )
                    )

        if turn.attackers > 0 and turn.life_end is not None and turn.opponent_power + CRACK_BACK_MARGIN >= turn.life_end:
            findings.append(
                Finding(
                    turn.number,
                    "review",
                    f"Attacked with {turn.attackers} creature(s) while the opponent had {turn.opponent_power} power "
                    f"on board and you were at {turn.life_end} life (pump and counters add more). "
                    f"Did you keep enough blockers for the crack-back?",
                    "Risky attack",
                )
            )

        if turn.eligible_attackers > 0 and turn.opponent_blockers == 0 and turn.attackers == 0:
            findings.append(
                Finding(turn.number, "review", f"Opponent had no untapped blockers and you didn't attack with {turn.eligible_attackers} creature(s).", "No attack into an empty board")
            )

        removal = [
            g for g in turn.end_hand
            if "removal" in card(g).get("tags", []) and not _is_counterspell(card(g)) and payable(g)
        ]
        if removal and turn.opponent_creatures:
            next_turn = next((t for t in game.turns if t.number == turn.number + 1), None)
            if next_turn and turn.life_end is not None and next_turn.life_end is not None:
                lost = turn.life_end - next_turn.life_end
                if lost >= LIFE_SWING_FOR_REMOVAL_CHECK:
                    findings.append(
                        Finding(
                            turn.number,
                            "review",
                            f"Held {name(removal[0])} with mana up, then took {lost} damage next turn. Worth using it earlier?",
                            "Held removal",
                        )
                    )

        if index == 4 and turn.lands_on_battlefield <= 2:
            findings.append(Finding(turn.number, "info", f"Mana screw: {turn.lands_on_battlefield} lands on your 4th turn (bad luck, not a decision)."))
        if index >= 6 and turn.lands_on_battlefield >= index + 2 and not [g for g in turn.end_hand if "Land" not in card(g).get("types", [])]:
            findings.append(Finding(turn.number, "info", f"Flood: {turn.lands_on_battlefield} lands and no spells in hand."))

    first_spell = next((t.number for t in my_turns if t.spells_cast), None)
    if first_spell:
        findings.append(Finding(first_spell, "info", f"First spell on turn {first_spell} (your turn {my_turns.index(next(t for t in my_turns if t.number == first_spell)) + 1})."))

    efficiency = mana_efficiency(game, card)
    if efficiency is not None:
        level = "review" if efficiency < LOW_MANA_USE else "ok"
        findings.append(
            Finding(0, level, f"Used {efficiency:.0%} of your available mana on your turns 2-6 "
                              f"(spells cast in your turn or the opponent's next turn).", "Low mana use")
        )

    behind = first_turn_behind(game)
    if behind:
        findings.append(
            Finding(behind.number, "review",
                    f"Behind on board: {behind.my_creatures} creature(s) / {behind.my_power} power vs "
                    f"{behind.opponent_creatures} / {behind.opponent_power}. What could have been deployed earlier?",
                    "Behind on board")
        )

    swing = biggest_hit(game)
    if swing:
        turn, lost = swing
        findings.append(Finding(turn, "info", f"Biggest hit: you lost {lost} life on turn {turn}. Look at how the board got there."))

    findings += deck_findings(game, card, benchmarks)
    return sorted(findings, key=lambda f: f.turn)


LOW_MANA_USE = 0.6
PRESSURE_POWER_GAP = 3
PRESSURE_LIFE = 10


def _is_defensive(c: Dict) -> bool:
    return "Creature" in c.get("types", []) or "removal" in c.get("tags", [])


def _priority_context(game: GameLog, turn: TurnLog, held: int, cast: List[int], card):
    """Decides how to report a stronger card left in hand, given the board and
    what you did next turn. Returns (level, explanation) or None to stay quiet."""
    # Planned double spell: you cast it on your next turn and used (almost) all your mana.
    later = [t for t in game.turns if t.mine and t.number > turn.number]
    if later and held in later[0].spells_cast:
        spent = sum(float(card(g).get("cmc", 0) or 0) for g in later[0].spells_cast)
        if spent >= later[0].lands_on_battlefield - 1:
            return None

    before = next((t for t in reversed(game.turns) if t.number < turn.number and not t.mine), None)
    life = turn.life_start if turn.life_start is not None else (before.life_end if before else None)
    board = ""
    pressure = False
    if before:
        board = f"Board before your turn: you {before.my_power} power vs opponent {before.opponent_power}" + (
            f", you at {life} life." if life is not None else ".")
        pressure = before.opponent_power - before.my_power >= PRESSURE_POWER_GAP or (life is not None and life <= PRESSURE_LIFE)

    if pressure:
        held_card = card(held)
        cast_creatures = [g for g in cast if "Creature" in card(g).get("types", [])]
        if "Creature" in held_card.get("types", []) and cast_creatures:
            held_toughness = game.card_stats.get(held, [0, 0])[1]
            best_toughness = max(game.card_stats.get(g, [0, 0])[1] for g in cast_creatures)
            if len(cast_creatures) > 1 or best_toughness >= held_toughness:
                return "info", (f"{board} Under pressure, sturdier or more blockers can be the right call; "
                                f"check whether {card(held).get('name')} would have stabilized better.")
        if not _is_defensive(held_card) and any(_is_defensive(card(g)) for g in cast):
            return "info", f"{board} Under pressure, deploying defense first can be right."
        return "review", f"{board} Even under pressure, this card looked like the stronger play."
    return "review", f"{board} Play your best card first unless you were setting up a double spell.".strip()
HELD_CREATURE_TURNS = 2
CRACK_BACK_MARGIN = 3  # pump spells, counters and tricks on the opponent's turn
PRIORITY_GAP = 4.0  # GIH WR points that make a card "clearly better"
LIMITED_DECK_SIZE = (38, 45)
BEHIND_POWER_GAP = 4
BIG_HIT = 6


def mana_efficiency(game: GameLog, card) -> Optional[float]:
    """Mana spent / lands available over your turns 2-6. Spells you cast in the
    opponent's following turn (instants, flash) count toward your turn."""
    spent = available = 0.0
    my_turns = [t for t in game.turns if t.mine][1:6]
    for turn in my_turns:
        following = next((t for t in game.turns if t.number == turn.number + 1 and not t.mine), None)
        cast = turn.spells_cast + (following.spells_cast if following else [])
        spent += sum(float(card(g).get("cmc", 0) or 0) for g in cast)
        available += turn.lands_on_battlefield
    return min(1.0, spent / available) if available >= 6 else None


def first_turn_behind(game: GameLog) -> Optional[TurnLog]:
    """First end of turn (by turn 8) where the opponent's board outclasses yours."""
    for turn in game.turns:
        if 3 <= turn.number <= 8 and turn.opponent_creatures >= turn.my_creatures + 2 and (
            turn.opponent_power - turn.my_power >= BEHIND_POWER_GAP
        ):
            return turn
    return None


def biggest_hit(game: GameLog):
    best = None
    previous = None
    for turn in game.turns:
        if previous is not None and not turn.mine and turn.life_end is not None and previous is not None:
            lost = previous - turn.life_end
            if lost >= BIG_HIT and (best is None or lost > best[1]):
                best = (turn.number, lost)
        if turn.life_end is not None:
            previous = turn.life_end
    return best


def deck_findings(game: GameLog, card, benchmarks: Optional[Dict]) -> List[Finding]:
    """Your deck's creature / instant counts next to how such decks do (17Lands)."""
    tables = (benchmarks or {}).get("deck", {})
    # Limited decks only: a stale constructed deck from an earlier connect must not be judged.
    if not tables or not LIMITED_DECK_SIZE[0] <= len(game.deck) <= LIMITED_DECK_SIZE[1]:
        return []
    nonland = [g for g in game.deck if card(g) and "Land" not in card(g).get("types", [])]
    counts = {
        "creatures": sum("Creature" in card(g).get("types", []) for g in nonland),
        "instants": sum("Instant" in card(g).get("types", []) for g in nonland),
    }
    out = []
    for kind, count in counts.items():
        table = tables.get(kind, {})
        mine, games = _rate(table.get(str(count)))
        rates = [(int(k), v["wins"] / v["games"]) for k, v in table.items() if v["games"] >= MIN_BENCHMARK_GAMES * 5]
        if mine is None or games < MIN_BENCHMARK_GAMES or not rates:
            continue
        best_k, best = max(rates, key=lambda kv: kv[1])
        out.append(
            Finding(0, "info", f"Deck: {count} {kind} — decks like that win {mine:.0%} on 17Lands "
                               f"(best range ~{best_k}: {best:.0%}).")
        )
    return out


def _card_cache(lookup):
    cards = {}

    def card(grp_id) -> Dict:
        if grp_id not in cards:
            found = lookup([str(grp_id)])
            cards[grp_id] = found[0] if found else {}
        return cards[grp_id]

    return card


def patterns(reviews: List, lookup) -> List[str]:
    """Cross-game patterns over [(GameLog, findings)], plus how much of the
    record could simply be variance."""
    games = [(g, f) for g, f in reviews if g.won is not None]
    if not games:
        return []
    wins = sum(g.won for g, _ in games)
    lines = [f"{len(games)} games: {wins}-{len(games) - wins}."]

    expected = 0.55  # roughly the average 17Lands drafter
    p_low = _binomial_cdf(wins, len(games), expected)
    lines.append(
        f"With a true {expected:.0%} win rate, a record this low or lower happens {p_low:.0%} of the time "
        f"by luck alone" + (" — too few games to conclude much." if p_low > 0.1 else ".")
    )

    for side in (True, False):
        subset = [g for g, _ in games if g.on_play is side]
        if subset:
            w = sum(g.won for g in subset)
            lines.append(f"On the {'play' if side else 'draw'}: {w}-{len(subset) - w}.")

    def avg(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    card = _card_cache(lookup)
    eff = {res: avg([mana_efficiency(g, card) for g, _ in games if g.won is res]) for res in (True, False)}
    if eff[True] is not None and eff[False] is not None:
        lines.append(f"Mana used on turns 2-6: {eff[True]:.0%} in wins vs {eff[False]:.0%} in losses.")

    length = {res: avg([len(g.turns) for g, _ in games if g.won is res]) for res in (True, False)}
    if length[True] is not None and length[False] is not None:
        lines.append(f"Game length: {length[True]:.0f} turns in wins vs {length[False]:.0f} in losses.")

    losses = [(g, fs) for g, fs in games if g.won is False]
    if losses:
        behind = sum(any(f.kind == "Behind on board" for f in fs) for _, fs in losses)
        lines.append(f"Behind on board by turn 8 in {behind} of {len(losses)} losses.")
        mulls = sum(g.mulligans > 0 for g, _ in losses)
        if mulls:
            lines.append(f"Mulliganed in {mulls} of {len(losses)} losses.")

    flags = {}
    for _, fs in games:
        for f in fs:
            if f.level == "review":
                flags[f.kind or "Other"] = flags.get(f.kind or "Other", 0) + 1
    if flags:
        top = sorted(flags.items(), key=lambda kv: -kv[1])[:3]
        lines.append("Most frequent flags: " + "; ".join(f"{k} ({n})" for k, n in top) + ".")
    return lines


def _binomial_cdf(k: int, n: int, p: float) -> float:
    from math import comb

    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def game_summary_text(game: GameLog, lookup, findings: List[Finding]) -> str:
    """Plain-text turn-by-turn summary, to paste into a chat for a deeper review."""

    def names(ids):
        out = []
        for g in ids:
            found = lookup([str(g)])
            out.append(found[0].get(constants.DATA_FIELD_NAME, f"#{g}") if found else f"#{g}")
        return ", ".join(out) or "-"

    result = {True: "WON", False: "LOST", None: "unknown result"}[game.won]
    lines = [
        f"Game {game.game_number} of match {game.match_id} ({game.event or 'event unknown'}): {result}",
        f"On the {'play' if game.on_play else 'draw'}, {game.mulligans} mulligan(s). Opening hand: {names(game.opening_hand)}",
    ]
    for t in game.turns:
        who = "You" if t.mine else "Opp"
        life = f"life {t.life_end if t.life_end is not None else '?'} vs {t.opponent_life_end if t.opponent_life_end is not None else '?'}"
        if t.mine:
            lines.append(
                f"T{t.number} {who}: lands played {t.lands_played}, cast: {names(t.spells_cast)}; "
                f"attacked with {t.attackers}/{t.eligible_attackers} (opp blockers {t.opponent_blockers}); "
                f"end: {t.end_untapped_lands} untapped lands, hand: {names(t.end_hand)}; {life}"
            )
        else:
            lines.append(f"T{t.number} {who}: {life}")
    lines.append("Flags: " + ("; ".join(f"T{f.turn} {f.text}" for f in findings if f.level == "review") or "none"))
    return "\n".join(lines)


# --- persistence ----------------------------------------------------------


def load_reviews(path: str = REVIEWS_FILE) -> Dict[str, Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def update_reviews(log_paths: List[str], events: Dict[str, str], path: str = REVIEWS_FILE) -> Dict[str, Dict]:
    """Records every finished game found in the logs; `events` maps match id -> event name."""
    stored = load_reviews(path)
    added = 0
    for log_path in log_paths:
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                games = record_games(f.read())
        except OSError:
            continue
        for game in games:
            old = stored.get(game.key)
            if game.won is None or (old and old.get("version") == RECORD_VERSION):
                continue
            game.event = events.get(game.match_id, "") or (old or {}).get("event", "")
            stored[game.key] = {**asdict(game), "version": RECORD_VERSION}
            added += 1
    if added:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(stored, f)
        except OSError as e:
            logger.warning(f"Game review save error: {e}")
    return stored


def load_benchmarks(set_code: str, event_type: str) -> Optional[Dict]:
    from src.advisor.pick_model import MODELS_FOLDER, find_model_path

    path = find_model_path(set_code, event_type, MODELS_FOLDER, "_benchmarks.json")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
