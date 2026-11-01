from src.advisor.pick_two import choose_pair, pair_mark
from src.advisor.schema import Recommendation


def rec(name, score):
    return Recommendation(card_name=name, base_win_rate=55, contextual_score=score, z_score=0,
                          cast_probability=1, wheel_chance=0, functional_cmc=2, reasoning=[])


def test_second_pick_is_chosen_with_the_first_in_the_pool():
    pack = [{"name": "Red Bomb"}, {"name": "Blue Card"}, {"name": "Red Filler"}]
    ranked = [rec("Red Bomb", 90), rec("Blue Card", 80), rec("Red Filler", 75)]
    seen = {}

    def evaluate(taken, pack_left):
        seen["taken"] = [c["name"] for c in taken]
        seen["pack"] = [c["name"] for c in pack_left]
        return [rec("Red Filler", 85), rec("Blue Card", 70)]

    out = choose_pair(ranked, pack, [{"name": "Old"}], evaluate)
    assert seen == {"taken": ["Old", "Red Bomb"], "pack": ["Blue Card", "Red Filler"]}
    assert [r.card_name for r in out] == ["Red Bomb", "Red Filler", "Blue Card"]
    assert pair_mark(out[0]) == "①" and pair_mark(out[1]) == "②" and pair_mark(out[2]) == ""
    assert "Red Filler" in out[0].reasoning[0]


def test_two_copies_and_short_packs():
    pack = [{"name": "Twin"}, {"name": "Twin"}, {"name": "Other"}]
    out = choose_pair([rec("Twin", 90), rec("Other", 50)], pack, [], lambda t, p: [rec("Twin", 88)])
    assert pair_mark(out[0]) == "①②"
    single = [rec("Last", 50)]
    assert choose_pair(single, [{"name": "Last"}], [], lambda t, p: []) == single
