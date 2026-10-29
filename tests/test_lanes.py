from src.advisor.lanes import archetype_key, lane_archetypes

KEYS = ["All Decks", "W", "U", "WU", "UB", "BR", "RG", "WG", "WB", "BG", "UG", "UR", "WR"]


def card(*colors):
    return {"colors": list(colors)}


def test_archetype_key_matches_any_order():
    assert archetype_key("UW", KEYS) == "WU"
    assert archetype_key("BU", KEYS) == "UB"
    assert archetype_key("UBG", KEYS) is None


def test_lanes_follow_the_pool():
    pool = [card("U"), card("U"), card("W"), card("B"), card("W", "U")]
    assert lane_archetypes(pool, KEYS) == ["WU", "UB", "UR"]


def test_no_colored_picks_means_no_lanes():
    assert lane_archetypes([card(), card()], KEYS) == []
    assert lane_archetypes([], KEYS) == []
