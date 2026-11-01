"""
src/advisor/pick_two.py
Pick Two drafts take two cards from each pack. Ranking every card on its own
can pair a first pick with a second that fights its colors, so the second
card is chosen as if the first were already in your pool.
"""

from typing import Callable, Dict, List

from src import constants
from src.advisor.schema import Recommendation

PAIR_MARKS = {1: "①", 2: "②"}


def choose_pair(
    recommendations: List[Recommendation],
    pack_cards: List[Dict],
    taken_cards: List[Dict],
    evaluate: Callable[[List[Dict], List[Dict]], List[Recommendation]],
) -> List[Recommendation]:
    """Marks the best pair (pair_slot 1 and 2) and moves it to the top.

    recommendations: the pack ranked card by card, best first.
    evaluate(taken, pack): ranks a pack for a given pool; used to pick the
    second card with the first one added to the pool."""
    if len(recommendations) < 2 or len(pack_cards) < 2:
        return recommendations
    first = recommendations[0]
    first_card = next((c for c in pack_cards if c.get(constants.DATA_FIELD_NAME) == first.card_name), None)
    if first_card is None:
        return recommendations
    rest = list(pack_cards)
    rest.remove(first_card)
    try:
        second_ranked = evaluate(list(taken_cards) + [first_card], rest)
    except Exception:
        second_ranked = []
    second_name = second_ranked[0].card_name if second_ranked else recommendations[1].card_name

    first.pair_slot = 1
    if second_name == first.card_name:
        # Two copies of the same card: take both.
        first.pair_slot = 3
        first.reasoning = ["Pick Two: take both copies"] + list(first.reasoning)
        return recommendations
    second = next((r for r in recommendations if r.card_name == second_name), None)
    if second is None:
        return recommendations
    second.pair_slot = 2
    first.reasoning = [f"Pick Two: pair with {second.card_name}"] + list(first.reasoning)
    second.reasoning = [f"Best second pick once you have {first.card_name}"] + list(second.reasoning)
    return [first, second] + [r for r in recommendations if r is not first and r is not second]


def pair_mark(rec: Recommendation) -> str:
    """"①", "②", "①②" (both copies) or ""."""
    slot = getattr(rec, "pair_slot", 0)
    return "①②" if slot == 3 else PAIR_MARKS.get(slot, "")
