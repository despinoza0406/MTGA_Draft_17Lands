from src.advisor.deck_check import arena_steps, check_deck, deck_colors
from src.advisor.draft_review import draft_report


class Metrics:
    def get_metrics(self, color, field):
        return 55.0, 4.0


def card(name, cost="{1}{R}", cmc=2, types=("Creature",), wr=55.0, tags=(), count=1, colors=None):
    if colors is None:
        colors = sorted({c for c in cost if c in "WUBRG"})
    return {"name": name, "mana_cost": cost, "cmc": cmc, "types": list(types), "colors": colors,
            "tags": list(tags), "count": count, "deck_colors": {"All Decks": {"gihwr": wr}}}


def basic(name, color, count):
    return {"name": name, "cmc": 0, "types": ["Land", "Basic"], "colors": [color], "count": count}


def rg_deck(**over):
    spells = [card(f"R{i}", "{1}{R}", 2, wr=56) for i in range(6)]
    spells += [card(f"G{i}", "{2}{G}", 3, wr=56) for i in range(6)]
    spells += [card(f"Kill{i}", "{1}{R}", 2, ("Instant",), 57, ("removal",)) for i in range(3)]
    spells += [card(f"Big{i}", "{4}{G}", 5, wr=56) for i in range(3)]
    spells += [card(f"S{i}", "{2}{G}", 3, ("Sorcery",), 54) for i in range(5)]
    deck = spells + [basic("Mountain", "R", 8), basic("Forest", "G", 9)]
    return deck


def test_colors_and_splash():
    spells = [card("A", "{R}{R}", count=2), card("B", "{G}{G}", count=3), card("C", "{1}{U}")]
    assert deck_colors(spells) == (["R", "G"], ["U"])


def test_clean_deck_has_no_fixes():
    result = check_deck(rg_deck(), [], Metrics())
    assert result.colors == ["R", "G"] and result.estimate.endswith("wins")
    assert not [n for n in result.notes if n.level == "fix"], result.notes


def test_problems_are_flagged():
    deck = rg_deck()
    deck = [c for c in deck if not c["name"].startswith("Kill")]  # no removal
    deck += [card(f"Huge{i}", "{5}{G}", 6, wr=55) for i in range(4)]  # top-heavy, 41+ cards
    titles = {n.title for n in check_deck(deck, [], Metrics()).notes if n.level == "fix"}
    assert {"Interaction", "Top-heavy", "Deck size"} <= titles


def test_color_split_counts_double_pips():
    deck = rg_deck()
    deck += [card("RR", "{R}{R}", 2), card("RR2", "{1}{R}{R}", 3)]
    deck = [c for c in deck if c["name"] not in ("S0", "S1")]
    deck[-3:] = [c for c in deck[-3:]]
    for c in deck:
        if c["name"] == "Mountain":
            c["count"] = 5
        if c["name"] == "Forest":
            c["count"] = 12
    notes = [n for n in check_deck(deck, [], Metrics()).notes if n.title == "Color split"]
    assert notes and "need RR" in notes[0].text


def test_swaps_bring_in_better_castable_cards():
    sideboard = [card("Bomb", "{2}{R}", 3, wr=64), card("Offcolor", "{U}", 1, wr=66)]
    result = check_deck(rg_deck(), sideboard, Metrics())
    assert [s.into for s in result.swaps] == ["Bomb"]
    assert result.swaps[0].out == "S0" or result.swaps[0].out.startswith("S")
    assert any(n.title == "Bombs on the bench" for n in result.notes)


def test_arena_steps_from_scratch_and_from_a_previous_deck():
    deck = [card("A", cmc=3), card("B", cmc=1, count=2), basic("Forest", "G", 9)]
    text = arena_steps(deck)
    assert text.splitlines()[1:3] == ["  2 B", "  1 A"] and "9 Forest" in text
    diff = arena_steps(deck, ["A", "C", "B", "Forest"] + ["Forest"] * 9)
    assert "Take out:\n  1 C" in diff and "Put in:\n  1 B" in diff and "Forest 10 → 9" in diff


def test_draft_report_lines():
    picks = [card(f"R{i}", "{R}") for i in range(14)]
    picks[2] = card("Blue", "{U}")
    picks[10] = card("LateBomb", "{R}", wr=65)
    taken = picks + [card(f"P2_{i}", "{G}") for i in range(14)] + [card(f"P3_{i}", "{R}") for i in range(14)]
    deck = [c["name"] for c in taken if c["name"] != "Blue"][:23]
    lines = draft_report(taken, ["R", "G"], deck, 1, rating=lambda c: c["deck_colors"]["All Decks"]["gihwr"],
                         strong=60)
    text = "\n".join(lines)
    assert "P1 13/14" in text and "last off-color pick was P1p3" in text
    assert "P1p3 Blue" in text and "LateBomb (P1p11)" in text
