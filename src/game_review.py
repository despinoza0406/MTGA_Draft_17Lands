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
    lands_on_battlefield: int = 0
    eligible_attackers: int = 0
    opponent_blockers: int = 0
    opponent_creatures: int = 0
    attackers: int = 0
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
    turns: List[TurnLog] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.match_id}#{self.game_number}"

    @staticmethod
    def from_dict(data: Dict) -> "GameLog":
        turns = [TurnLog(**t) for t in data.get("turns", [])]
        return GameLog(**{**data, "turns": turns})


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
            self.current = GameLog(self.game_key[0], self.game_key[1])
            self.games.append(self.current)
            self.turn_number, self._mulligan_prompts = 0, 0
        if not self.current:
            return

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
            self.lives[player.get("systemSeatNumber")] = player.get("lifeTotal")
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
        if not turn.mine:
            return
        step, phase = turn_info.get("step"), turn_info.get("phase")
        mine_bf = self._battlefield(self.seat)
        opp_bf = self._battlefield(opp) if opp else []
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
            untapped = [o for o in lands if not o.get("isTapped")]
            turn.end_untapped_lands = len(untapped)
            turn.end_untapped_colors = [_land_colors(o) for o in untapped]
            turn.opponent_creatures = sum(1 for o in opp_bf if _is_type(o, "Creature"))


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
            out.append(Finding(0, "ok" if keep >= after_mull else "review", text))
        elif games:
            out.append(Finding(0, "info", f"Kept 7 with {game.opening_hand_lands} lands: too few 17Lands games ({games}) to judge."))
    elif game.mulligans > 0 and game.first_hand_lands is not None:
        keep, games = _rate(keep7.get(str(game.first_hand_lands)))
        if keep is not None and games >= MIN_BENCHMARK_GAMES and after_mull is not None:
            text = (
                f"Mulliganed a 7 with {game.first_hand_lands} lands on the {side}: keeping such hands wins "
                f"{keep:.0%}, mulliganing {after_mull:.0%} (17Lands; rough guide, the spells matter too)."
            )
            out.append(Finding(0, "review" if keep > after_mull + 0.05 else "ok", text))
    return out


def review_game(game: GameLog, lookup: Callable[[List[str]], List[Dict]], benchmarks: Optional[Dict] = None) -> List[Finding]:
    cards = {}

    def card(grp_id) -> Dict:
        if grp_id not in cards:
            found = lookup([str(grp_id)])
            cards[grp_id] = found[0] if found else {}
        return cards[grp_id]

    def name(grp_id) -> str:
        return card(grp_id).get(constants.DATA_FIELD_NAME) or f"#{grp_id}"

    findings = _mulligan_findings(game, benchmarks)
    my_turns = [t for t in game.turns if t.mine]
    for index, turn in enumerate(my_turns, start=1):
        if turn.lands_played == 0 and turn.end_hand_lands > 0:
            findings.append(Finding(turn.number, "review", "No land played, but you had a land in hand."))

        sources = turn.end_untapped_colors or [""] * turn.end_untapped_lands

        def payable(g):
            c = card(g)
            cmc = float(c.get("cmc", 0) or 0)
            return cmc > 0 and can_pay(c.get("mana_cost", ""), cmc, sources)

        castable = [
            g for g in turn.end_hand
            if "Land" not in card(g).get("types", []) and "Instant" not in card(g).get("types", []) and payable(g)
        ]
        # Mana left open for an instant you could cast is often deliberate.
        held_up = [g for g in turn.end_hand if "Instant" in card(g).get("types", []) and payable(g)]
        if castable and not held_up:
            best = max(castable, key=lambda g: float(card(g).get("cmc", 0)))
            findings.append(
                Finding(
                    turn.number,
                    "review",
                    f"Ended your turn with {turn.end_untapped_lands} untapped lands while holding "
                    f"{name(best)} (cost {card(best).get('cmc')}). Unused mana, if you had the colors.",
                )
            )

        if turn.eligible_attackers > 0 and turn.opponent_blockers == 0 and turn.attackers == 0:
            findings.append(
                Finding(turn.number, "review", f"Opponent had no untapped blockers and you didn't attack with {turn.eligible_attackers} creature(s).")
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
                        )
                    )

        if index == 4 and turn.lands_on_battlefield <= 2:
            findings.append(Finding(turn.number, "info", f"Mana screw: {turn.lands_on_battlefield} lands on your 4th turn (bad luck, not a decision)."))
        if index >= 6 and turn.lands_on_battlefield >= index + 2 and not [g for g in turn.end_hand if "Land" not in card(g).get("types", [])]:
            findings.append(Finding(turn.number, "info", f"Flood: {turn.lands_on_battlefield} lands and no spells in hand."))

    first_spell = next((t.number for t in my_turns if t.spells_cast), None)
    if first_spell:
        findings.append(Finding(first_spell, "info", f"First spell on turn {first_spell} (your turn {my_turns.index(next(t for t in my_turns if t.number == first_spell)) + 1})."))
    return sorted(findings, key=lambda f: f.turn)


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
            if game.won is None or game.key in stored:
                continue
            game.event = events.get(game.match_id, "")
            stored[game.key] = asdict(game)
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
