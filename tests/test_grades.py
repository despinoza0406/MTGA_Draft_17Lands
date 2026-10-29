from src.ui.windows.grades import column_of, grade_grid


class Metrics:
    def get_metrics(self, color, field):
        return 55.0, 4.0


def card(name, colors, wr, rarity="common", games=500):
    return {"name": name, "colors": colors, "rarity": rarity,
            "deck_colors": {"All Decks": {"gihwr": wr, "gih": games}}}


def test_column_of():
    assert column_of(card("a", ["U"], 50)) == "U"
    assert column_of(card("a", ["U", "B"], 50)) == "M"
    assert column_of(card("a", [], 50)) == "C"


def test_grade_grid_places_and_filters():
    cards = [
        card("Bomb", ["R"], 64.0, "mythic"),
        card("Bomb", ["R"], 64.0, "mythic"),
        card("Good", ["U"], 59.5),
        card("Better", ["U"], 60.0),
        card("Rare few games", ["U"], 70.0, games=20),
        card("No data", ["G"], 0.0),
    ]
    grid = grade_grid(cards, Metrics())
    assert [e[0] for e in grid["A+"]["R"]] == ["Bomb"]
    assert [e[0] for e in grid["B+"]["U"]] == ["Better", "Good"]
    assert all("Rare few games" not in [e[0] for col in g.values() for e in col] for g in grid.values())
    assert grade_grid(cards, Metrics(), rarity="mythic").keys() == {"A+"}
