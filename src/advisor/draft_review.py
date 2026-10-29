"""
src/advisor/draft_review.py
Post-draft review: replays every pick through the trained pick model and
compares what you took with what the model would have taken.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List

from src import constants


@dataclass
class PickReview:
    pack: int
    pick: int
    picked: str
    picked_prob: float
    model_pick: str
    model_prob: float

    @property
    def agrees(self) -> bool:
        return self.picked == self.model_pick

    @property
    def gap(self) -> float:
        """How much more the model liked its choice than yours (percentage points)."""
        return self.model_prob - self.picked_prob


def review_draft(
    model,
    history: List[Dict],
    taken_cards: List[Dict],
    lookup: Callable[[List[str]], List[Dict]],
) -> List[PickReview]:
    """
    history:     scanner draft history, [{"Pack": 1, "Pick": 1, "Cards": [arena ids]}, ...]
    taken_cards: your picks in pick order (card dicts)
    lookup:      arena ids -> card dicts (Dataset.get_data_by_id)

    A pick is reviewed only when the card you took at that position is in the
    recorded pack, so partial or reordered histories never misattribute picks.
    """
    entries = sorted(history or [], key=lambda e: (e.get("Pack", 0), e.get("Pick", 0)))
    reviews, pool = [], []
    for index, entry in enumerate(entries):
        if index >= len(taken_cards):
            break
        picked = taken_cards[index].get(constants.DATA_FIELD_NAME, "")
        pack_cards = lookup([str(c) for c in entry.get("Cards", [])])
        if picked not in {c.get(constants.DATA_FIELD_NAME, "") for c in pack_cards}:
            continue

        scores = model.score_pack(pack_cards, pool, entry["Pack"], entry["Pick"])
        if scores and picked in scores:
            best = max(scores, key=scores.get)
            reviews.append(
                PickReview(entry["Pack"], entry["Pick"], picked, scores[picked], best, scores[best])
            )
        pool.append(taken_cards[index])
    return reviews


def summarize(reviews: List[PickReview], top_n: int = 5) -> Dict:
    agreed = sum(r.agrees for r in reviews)
    misses = sorted((r for r in reviews if not r.agrees), key=lambda r: r.gap, reverse=True)
    return {
        "reviewed": len(reviews),
        "agreed": agreed,
        "agreement": agreed / len(reviews) if reviews else 0.0,
        "biggest_misses": misses[:top_n],
    }
