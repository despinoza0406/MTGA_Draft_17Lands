"""
src/advisor/lanes.py
Picks the two-color archetypes a draft is leaning toward, so the pack table can
show each card's win rate inside the lanes that matter instead of only overall.
"""

from itertools import combinations
from typing import Dict, Iterable, List, Optional

COLOR_ORDER = "WUBRG"


def archetype_key(colors: str, available: Iterable[str]) -> Optional[str]:
    """Return the 17lands deck_colors key for ``colors`` ("UW" -> "WU")."""
    wanted = set(colors)
    for key in available:
        if set(key) == wanted and len(key) == len(wanted):
            return key
    return None


def lane_archetypes(
    taken_cards: List[Dict], available: Iterable[str], count: int = 3
) -> List[str]:
    """Rank two-color archetypes by how many colored picks support them.

    A card counts toward a color for each of its colors, so gold cards pull both
    of their colors. Ties fall back to WUBRG order, which keeps the columns
    stable from pick to pick. With no colored picks yet nothing is returned.
    """
    weight = {c: 0.0 for c in COLOR_ORDER}
    for card in taken_cards or []:
        colors = [c for c in card.get("colors", []) if c in weight]
        for c in colors:
            weight[c] += 1.0 / len(colors) if len(colors) > 2 else 1.0
    if not any(weight.values()):
        return []
    keys = list(available)
    pairs = sorted(
        combinations(COLOR_ORDER, 2),
        key=lambda p: (-(weight[p[0]] + weight[p[1]]), -max(weight[p[0]], weight[p[1]])),
    )
    lanes = []
    for a, b in pairs:
        if weight[a] + weight[b] == 0:
            break
        key = archetype_key(a + b, keys)
        if key:
            lanes.append(key)
        if len(lanes) == count:
            break
    return lanes
