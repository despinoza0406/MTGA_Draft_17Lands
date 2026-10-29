"""
tests/test_draft_review.py
Validation for the post-draft pick review against the trained pick model.
"""

from src.advisor.draft_review import review_draft, summarize


class FixedModel:
    """Always prefers cards in `ranking` order, ignoring the pool."""

    def __init__(self, ranking):
        self.ranking = ranking
        self.pools = []

    def score_pack(self, pack_cards, taken_cards, pack, pick):
        self.pools.append([c["name"] for c in taken_cards])
        names = [c["name"] for c in pack_cards if c["name"] in self.ranking]
        weights = {n: 2.0 ** -self.ranking.index(n) for n in names}
        total = sum(weights.values())
        return {n: 100.0 * w / total for n, w in weights.items()}


CARDS = {"1": "Ace", "2": "Bolt", "3": "Cat", "4": "Duck"}


def lookup(ids):
    return [{"name": CARDS[i]} for i in ids if i in CARDS]


def card(name):
    return {"name": name}


def test_compares_each_pick_with_the_model():
    model = FixedModel(["Ace", "Bolt", "Cat", "Duck"])
    history = [
        {"Pack": 1, "Pick": 1, "Cards": [1, 2, 3]},
        {"Pack": 1, "Pick": 2, "Cards": [2, 4]},
    ]
    reviews = review_draft(model, history, [card("Bolt"), card("Bolt")], lookup)

    assert [(r.pack, r.pick, r.picked, r.model_pick) for r in reviews] == [
        (1, 1, "Bolt", "Ace"),
        (1, 2, "Bolt", "Bolt"),
    ]
    assert not reviews[0].agrees and reviews[1].agrees
    assert reviews[0].gap > 0
    assert model.pools == [[], ["Bolt"]]


def test_skips_picks_missing_from_the_recorded_pack():
    model = FixedModel(["Ace", "Bolt"])
    history = [
        {"Pack": 1, "Pick": 2, "Cards": [1, 2]},
        {"Pack": 1, "Pick": 1, "Cards": [3, 4]},  # out of order on purpose
    ]
    # Pick 1 took "Ace" but the recorded P1p1 pack has no Ace -> skipped
    reviews = review_draft(model, history, [card("Ace"), card("Bolt")], lookup)

    assert [(r.pick, r.picked) for r in reviews] == [(2, "Bolt")]


def test_summary_lists_biggest_disagreements_first():
    model = FixedModel(["Ace", "Bolt", "Cat", "Duck"])
    history = [
        {"Pack": 1, "Pick": 1, "Cards": [1, 4]},
        {"Pack": 1, "Pick": 2, "Cards": [1, 2]},
        {"Pack": 1, "Pick": 3, "Cards": [3]},
    ]
    reviews = review_draft(model, history, [card("Duck"), card("Bolt"), card("Cat")], lookup)
    stats = summarize(reviews)

    assert stats["reviewed"] == 3 and stats["agreed"] == 1
    assert [r.picked for r in stats["biggest_misses"]] == ["Duck", "Bolt"]


def test_empty_history():
    assert review_draft(FixedModel([]), [], [card("Ace")], lookup) == []
    assert summarize([])["agreement"] == 0.0


def test_pick_two_reviews_both_cards_of_each_pick():
    """Pick Two takes two cards per pick; packs shrink by two between picks."""
    model = FixedModel(["Ace", "Bolt", "Cat", "Duck"])
    history = [
        {"Pack": 1, "Pick": 1, "Cards": [1, 2, 3, 4]},
        {"Pack": 1, "Pick": 2, "Cards": [3, 4]},
    ]
    taken = [card("Ace"), card("Cat"), card("Duck"), card("Cat")]
    reviews = review_draft(model, history, taken, lookup)

    assert [(r.pick, r.picked, r.model_pick) for r in reviews] == [
        (1, "Ace", "Ace"),
        (1, "Cat", "Bolt"),  # model's second card was Bolt, which you passed
        (2, "Cat", "Cat"),
        (2, "Duck", "Duck"),
    ]
    assert model.pools == [[], ["Ace", "Cat"]]


def test_history_starting_mid_draft_lines_up_by_pick_number():
    model = FixedModel(["Ace", "Bolt", "Cat", "Duck"])
    history = [{"Pack": 1, "Pick": 3, "Cards": [3, 4]}]  # picks 1-2 were never recorded
    taken = [card("Ace"), card("Bolt"), card("Duck")]
    reviews = review_draft(model, history, taken, lookup)

    assert [(r.pick, r.picked) for r in reviews] == [(3, "Duck")]
    assert model.pools == [["Ace", "Bolt"]]


def test_cards_per_pick_detection():
    from src.advisor.draft_review import cards_per_pick

    premier = [{"Pack": 1, "Pick": p, "Cards": list(range(15 - p))} for p in (1, 2, 3)]
    pick_two = [{"Pack": 1, "Pick": p, "Cards": list(range(16 - 2 * p))} for p in (1, 2, 3)]
    assert cards_per_pick(premier) == 1
    assert cards_per_pick(pick_two) == 2
    assert cards_per_pick([]) == 1
