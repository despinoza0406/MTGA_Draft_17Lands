"""
src/advisor/deck_check.py
Checks a 40-card limited deck you built: what it does well, what it's short
of, and which sideboard cards would make it better, with the reason for each.
Also turns a deck into the steps to build it in Arena's deck builder, which
has no import in events.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src import constants
from src.advisor.deck_scorer import calculate_holistic_score, estimate_record, get_card_rating
from src.advisor.mana_base import calculate_dynamic_mana_base, count_fixing, is_castable
from src.mana_cost import mana_color_demand, off_color_requirements
from src.utils import normalize_color_string

BASIC_FOR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
MIN_SWAP_GAIN = 1.0  # GIHWR points a sideboard card must beat a maindeck card by
MAX_SWAPS = 5
MIN_CREATURES = 13
MIN_TWO_DROPS = 4
MAX_TOP_END = 6  # spells costing 5+; 17Lands data: flat up to 6, worse at 7+
MIN_REMOVAL = 2
SPLASH_SOURCES = 3


@dataclass
class Note:
    level: str  # "good", "fix" or "info"
    title: str
    text: str


@dataclass
class Swap:
    out: str
    into: str
    gain: float
    reason: str


@dataclass
class DeckCheck:
    colors: List[str]
    splash: List[str]
    estimate: str = ""
    score: float = 0.0
    model_estimate: str = ""
    model_score: Optional[float] = None
    model_same: int = 0
    model_deck: List[str] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)
    swaps: List[Swap] = field(default_factory=list)


def _name(card: Dict) -> str:
    return card.get(constants.DATA_FIELD_NAME, "")


def _types(card: Dict) -> List[str]:
    return card.get(constants.DATA_FIELD_TYPES, card.get("types", [])) or []


def _is_land(card: Dict) -> bool:
    return "Land" in _types(card)


def _is_basic(card: Dict) -> bool:
    return _name(card) in constants.BASIC_LANDS or "Basic" in _types(card)


def _count(cards: List[Dict], test=lambda c: True) -> int:
    return sum(c.get("count", 1) for c in cards if test(c))


def _cmc(card: Dict) -> int:
    try:
        return int(card.get("cmc", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _names(cards: List[Dict]) -> Counter:
    out = Counter()
    for c in cards:
        out[_name(c)] += c.get("count", 1)
    return out


def _list(names: List[str], limit: int = 4) -> str:
    counted = Counter(names)
    parts = [f"{n} ×{k}" if k > 1 else n for n, k in counted.most_common()]
    return ", ".join(parts[:limit]) + (f" +{len(parts) - limit} more" if len(parts) > limit else "")


def deck_colors(spells: List[Dict]):
    """(main colors, splash colors) from the spells' colored pips. A color is a
    splash when it has at most 3 pips over at most 2 cards."""
    pips = mana_color_demand(spells)
    cards_of = Counter()
    for c in spells:
        for color in {x for opts in off_color_requirements(c, []) for x in opts}:
            cards_of[color] += c.get("count", 1)
    used = sorted((c for c in constants.CARD_COLORS if pips.get(c)), key=lambda c: -pips[c])
    main = [c for c in used if not (pips[c] <= 3 and cards_of[c] <= 2)] or used[:1]
    if len(main) > 2:
        main = main[:2] + [c for c in main[2:] if pips[c] > 3]
    splash = [c for c in used if c not in main]
    order = constants.CARD_COLORS
    return sorted(main, key=order.index), sorted(splash, key=order.index)


def check_deck(deck: List[Dict], sideboard: List[Dict], metrics, model=None, is_bo3: bool = False,
               tier_data=None) -> DeckCheck:
    spells = [c for c in deck if not _is_land(c)]
    lands = [c for c in deck if _is_land(c)]
    main, splash = deck_colors(spells)
    colors = main + splash
    result = DeckCheck(main, splash)
    notes = result.notes
    if not spells:
        return result

    arch = [normalize_color_string("".join(main[:2]))] if main else ["All Decks"]
    mean, std = metrics.get_metrics("All Decks", "gihwr") if metrics else (0.0, 0.0)
    mean, std = mean or 54.0, std or 4.0

    def rating(card):
        return get_card_rating(card, arch, metrics, tier_data)

    pool = [c for c in deck + sideboard if not _is_basic(c)]
    pool_size = _count(pool)

    # --- Estimate vs the model's build -------------------------------------
    result.score, _ = calculate_holistic_score(spells, colors, max(pool_size, 42), metrics, tier_data)
    result.estimate = estimate_record(result.score, is_bo3)
    probs = {}
    if model is not None:
        try:
            probs = model.inclusion(pool)
            allowed = lambda c: _is_land(c) or not off_color_requirements(c, colors)
            built = [c for c in model.build(pool, allowed=allowed) if not _is_land(c)]
            if built:
                result.model_score, _ = calculate_holistic_score(built, colors, max(pool_size, 42), metrics, tier_data)
                result.model_estimate = estimate_record(result.model_score, is_bo3)
                result.model_deck = sorted(_names(built).elements())
                mine = _names(spells)
                result.model_same = sum((mine & _names(built)).values())
        except Exception:
            probs = {}

    # --- Size ---------------------------------------------------------------
    total = _count(deck)
    if total != 40:
        notes.append(Note("fix", "Deck size", f"{total} cards: limited decks are 40, so every extra card "
                                              "makes your best ones less likely to show up."))

    # --- Card quality and bombs ---------------------------------------------
    rated = [(rating(c), c) for c in spells for _ in range(c.get("count", 1))]
    rated = [(r, c) for r, c in rated if r > 0]
    if rated:
        z = (sum(r for r, _ in rated) / len(rated) - mean) / std
        if z >= 0.4:
            notes.append(Note("good", "Card quality", f"Your spells average {z:+.1f} std above the format: "
                                                      "strong cards are the biggest reason decks win."))
        elif z < -0.2:
            notes.append(Note("fix", "Card quality", f"Your spells average {z:+.1f} std versus the format. "
                                                     "Check the swaps below for stronger cards you left out."))
    bomb_line = mean + 1.5 * std
    bombs_in = [_name(c) for r, c in rated if r >= bomb_line]
    bombs_out = [_name(c) for c in sideboard if not _is_land(c) and rating(c) >= bomb_line
                 and is_castable(c, colors)]
    if bombs_in:
        notes.append(Note("good", "Bombs", f"Playing {_list(bombs_in)}."))
    if bombs_out:
        notes.append(Note("fix", "Bombs on the bench", f"{_list(bombs_out)} castable in your colors but in the sideboard."))

    # --- Creatures, curve, interaction ----------------------------------------
    creatures = _count(spells, lambda c: "Creature" in _types(c))
    if creatures < MIN_CREATURES:
        notes.append(Note("fix", "Creatures", f"{creatures} creatures. Most winning limited decks run 14-17: "
                                              "fewer and you run out of ways to block and pressure."))
    elif creatures >= 15:
        notes.append(Note("good", "Creatures", f"{creatures} creatures: enough bodies to block and attack."))

    early = _count(spells, lambda c: _cmc(c) <= 2)
    if early < MIN_TWO_DROPS:
        notes.append(Note("fix", "Early plays", f"Only {early} spells cost 2 or less. On the draw against a fast "
                                                "deck you'll take a lot before your first play."))
    top = [c for c in spells if _cmc(c) >= 5]
    if _count(top) > MAX_TOP_END:
        notes.append(Note("fix", "Top-heavy", f"{_count(top)} spells cost 5+ ({_list([_name(c) for c in top])}). "
                                              "Hands with several will be slow; cut the weakest one or two."))

    removal = [_name(c) for c in spells if "removal" in (c.get("tags") or [])]
    if len(removal) < MIN_REMOVAL:
        notes.append(Note("fix", "Interaction", f"{len(removal)} removal spells. With fewer than 2 you can't "
                                                "answer their best creature."))
    elif len(removal) >= 4:
        notes.append(Note("good", "Interaction", f"{len(removal)} removal spells: you can answer their threats."))

    # --- Lands and colors --------------------------------------------------
    land_count = _count(lands)
    avg_cmc = sum(_cmc(c) * c.get("count", 1) for c in spells) / max(1, _count(spells))
    if land_count < 16 or land_count > 18 or (land_count == 16 and avg_cmc >= 3.0) or (land_count == 18 and avg_cmc < 3.0):
        want = 16 if avg_cmc < 2.6 else 18 if avg_cmc >= 3.6 else 17
        notes.append(Note("fix", "Land count", f"{land_count} lands for an average cost of {avg_cmc:.1f}: "
                                               f"{want} fits this curve."))
    basics = [c for c in lands if _is_basic(c)]
    if basics and main:
        want = Counter(_name(c) for c in calculate_dynamic_mana_base(
            spells, [c for c in lands if not _is_basic(c)], colors, _count(basics)))
        have = Counter()
        for c in basics:
            have[_name(c)] += c.get("count", 1)
        def double_pips(color):
            return [_name(c) for c in spells
                    if sum(1 for opts in off_color_requirements(c, []) if opts == {color}) >= 2]

        short = [n for n in want if want[n] > have[n]]
        needy = max(short, key=lambda n: want[n] - have[n]) if short else None
        color = next((k for k, v in BASIC_FOR.items() if v == needy), None)
        heavy = double_pips(color) if color else []
        # One land off matters when the short color has double-pip cards.
        if needy and want[needy] - have[needy] >= (1 if heavy else 2):
            split = " / ".join(f"{want[n]} {n}" for n in sorted(want, key=lambda n: -want[n]))
            mine = " / ".join(f"{have[n]} {n}" for n in sorted(have, key=lambda n: -have[n]))
            why = f" {_list(heavy)} need {color}{color}, so each {needy} counts." if heavy else ""
            notes.append(Note("fix", "Color split", f"You run {mine}; your pips ask for {split}.{why}"))
    if len(main) >= 3:
        fixing = sum(c.get("count", 1) for c in deck if not _is_basic(c) and
                     sum(1 for v in count_fixing([c]).values() if v) >= 2)
        level = "fix" if fixing < len(main) - 1 else "info"
        notes.append(Note(level, "Three colors", f"{len(main)} main colors with {fixing} fixing cards. "
                                                 "Two-color decks win more unless the fixing is there."))
    for color in splash:
        sources = count_fixing(deck).get(color, 0)
        cards = [_name(c) for c in spells if any(color in o for o in off_color_requirements(c, main))]
        if sources < SPLASH_SOURCES:
            notes.append(Note("fix", "Splash", f"Splashing {color} for {_list(cards)} with {sources} sources: "
                                               f"{SPLASH_SOURCES}+ keep it castable when you draw it."))
        else:
            notes.append(Note("info", "Splash", f"Splashing {color} for {_list(cards)} with {sources} sources."))

    # --- Model agreement ----------------------------------------------------
    if probs:
        doubtful = [n for n in _names(spells) if probs.get(n, 1) < 0.2]
        if doubtful:
            notes.append(Note("info", "Model doubts", f"Players with a pool like yours rarely maindeck "
                                                      f"{_list(doubtful)}."))

    result.swaps = _swaps(spells, sideboard, colors, rating, probs, creatures)
    return result


def _swaps(spells, sideboard, colors, rating, probs, creatures) -> List[Swap]:
    top_end = _count(spells, lambda c: _cmc(c) >= 5)
    outs = sorted(((rating(c), c) for c in spells if rating(c) > 0), key=lambda x: x[0])
    ins = sorted(((rating(c), c) for c in sideboard
                  if not _is_land(c) and not _is_basic(c) and rating(c) > 0 and is_castable(c, colors)),
                 key=lambda x: -x[0])
    used_out, used_in, swaps = Counter(), Counter(), []
    for r_in, c_in in ins:
        if len(swaps) >= MAX_SWAPS:
            break
        if used_in[_name(c_in)] >= c_in.get("count", 1):
            continue
        for r_out, c_out in outs:
            if used_out[_name(c_out)] >= c_out.get("count", 1) or r_in - r_out < MIN_SWAP_GAIN:
                continue
            p_in, p_out = probs.get(_name(c_in)), probs.get(_name(c_out))
            if p_in is not None and p_out is not None and p_in + 0.1 < p_out:
                continue  # the model prefers what you play
            loses_body = "Creature" in _types(c_out) and "Creature" not in _types(c_in)
            if loses_body and creatures <= 14:
                continue
            if _cmc(c_in) >= 5 > _cmc(c_out) and top_end >= MAX_TOP_END:
                continue  # don't make a top-heavy deck heavier
            reason = f"+{r_in - r_out:.1f} win rate"
            if p_in is not None and p_out is not None:
                reason += f"; the model plays it {p_in:.0%} vs {p_out:.0%}"
            if _cmc(c_in) != _cmc(c_out):
                reason += f"; costs {_cmc(c_in)} instead of {_cmc(c_out)}"
            swaps.append(Swap(_name(c_out), _name(c_in), r_in - r_out, reason))
            used_out[_name(c_out)] += 1
            used_in[_name(c_in)] += 1
            creatures += ("Creature" in _types(c_in)) - ("Creature" in _types(c_out))
            top_end += (_cmc(c_in) >= 5) - (_cmc(c_out) >= 5)
            break
    return swaps


def arena_steps(deck: List[Dict], current_main: Optional[List[str]] = None) -> str:
    """What to click in Arena's deck builder to end up with `deck`.

    current_main: the maindeck card names Arena has now (your last Done), or None
    when starting from an empty deck."""
    spells = [c for c in deck if not _is_basic(c)]
    target = _names(spells)
    basics = Counter()
    for c in deck:
        if _is_basic(c):
            basics[_name(c)] += c.get("count", 1)
    by_cost = {_name(c): (_cmc(c), _name(c)) for c in spells}

    def lines(counter):
        return [f"  {k} {n}" for n, k in sorted(counter.items(), key=lambda kv: by_cost.get(kv[0], (99, kv[0])))]

    out = []
    if current_main is None:
        out.append(f"Add from your pool ({sum(target.values())} cards, cheapest first):")
        out += lines(target)
    else:
        now = Counter(n for n in current_main if n not in constants.BASIC_LANDS)
        remove, add = now - target, target - now
        if not remove and not add:
            out.append("Spells: already match.")
        if remove:
            out.append("Take out:")
            out += [f"  {k} {n}" for n, k in sorted(remove.items())]
        if add:
            out.append("Put in:")
            out += lines(add)
        had = Counter(n for n in current_main if n in constants.BASIC_LANDS)
        if had != basics:
            changes = [f"{n} {had[n]} → {basics[n]}" for n in sorted(set(had) | set(basics)) if had[n] != basics[n]]
            out.append("Basic lands: " + ", ".join(changes))
            basics = Counter()
    if basics:
        out.append("Basic lands: " + ", ".join(f"{k} {n}" for n, k in basics.most_common()))
    return "\n".join(out)
