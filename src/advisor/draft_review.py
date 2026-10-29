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


def cards_per_pick(entries: List[Dict]) -> int:
    """How many cards leave the pack between consecutive picks you saw (1 in
    Premier/Quick Draft, 2 in Pick Two). In a pod the pack you see next is a
    different one, but every pack shrinks by the same amount per pick."""
    shrink = [
        (len(a.get("Cards", [])) - len(b.get("Cards", []))) // (b["Pick"] - a["Pick"])
        for a, b in zip(entries, entries[1:])
        if a.get("Pack") == b.get("Pack") and b.get("Pick", 0) > a.get("Pick", 0)
    ]
    shrink = sorted(s for s in shrink if s > 0)
    return shrink[len(shrink) // 2] if shrink else 1


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

    Formats that take several cards per pick (Pick Two) are detected from the
    pool size. A pick is reviewed only when the cards you took at that position
    are in the recorded pack, so partial or reordered histories never
    misattribute picks.
    """
    entries = sorted(history or [], key=lambda e: (e.get("Pack", 0), e.get("Pick", 0)))
    per_pick = cards_per_pick(entries)
    reviews = []
    for entry in entries:
        # Position of this pick in the whole draft, from its pack/pick numbers
        # (not its index), so a history that starts mid-draft still lines up.
        pack_size = len(entry.get("Cards", [])) + (entry["Pick"] - 1) * per_pick
        picks_per_pack = max(1, pack_size // per_pick)
        start = ((entry["Pack"] - 1) * picks_per_pack + entry["Pick"] - 1) * per_pick
        taken = taken_cards[start : start + per_pick]
        if not taken:
            continue
        pool = taken_cards[:start]
        picked = [c.get(constants.DATA_FIELD_NAME, "") for c in taken]
        pack_cards = lookup([str(c) for c in entry.get("Cards", [])])
        pack_names = [c.get(constants.DATA_FIELD_NAME, "") for c in pack_cards]
        if any(p not in pack_names for p in picked):
            continue

        scores = model.score_pack(pack_cards, pool, entry["Pack"], entry["Pick"])
        if scores and all(p in scores for p in picked):
            # The model "takes" as many cards as you did; a card of yours agrees
            # when it is among them, otherwise it is compared with the best
            # model choice you passed.
            ranked = sorted(scores, key=scores.get, reverse=True)
            model_top = ranked[: len(picked)]
            passed = [n for n in model_top if n not in picked]
            for name in sorted(picked, key=scores.get, reverse=True):
                if name in model_top:
                    reviews.append(PickReview(entry["Pack"], entry["Pick"], name, scores[name], name, scores[name]))
                else:
                    best = passed.pop(0)
                    reviews.append(PickReview(entry["Pack"], entry["Pick"], name, scores[name], best, scores[best]))
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
