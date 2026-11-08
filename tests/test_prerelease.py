"""tests/test_prerelease.py: day-1 predicted ratings from pre-release reviews."""

import importlib.util
import json
import os

from src.utils import Result, check_file_integrity

_path = os.path.join(os.path.dirname(__file__), "..", "training", "prerelease", "prerelease.py")
_spec = importlib.util.spec_from_file_location("prerelease", _path)
prerelease = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prerelease)


def page(*cards):
    return "".join(f'<a href="https://mtgazone.com/cards/{slug}/">x</a><p>blah</p><h3>{rating}</h3>' for slug, rating in cards)


def test_ratings_come_from_both_review_formats():
    html = page(("academic-ascent", "Rating: 1.5/5"), ("uss-voyager-lone-wanderer", "Rating: 3.0 // 5.0"),
                ("sideboard-card", "Rating: SB // 5.0"))
    assert prerelease.parse_ratings(html) == {"academic-ascent": 1.5, "uss-voyager-lone-wanderer": 3.0}


def test_draftsim_ratings_belong_to_the_heading_before_them():
    html = ('<h3 class="h"><span id="a">Academic Ascent</span></h3><p><strong>Rating: 2/10</strong></p>'
            '<h3><span>Captain Jean-Luc Picard</span></h3><img alt="x"><p><strong>Rating: 8.5/10</strong></p>')
    assert prerelease.parse_draftsim(html) == {"Academic Ascent": 2.0, "Captain Jean-Luc Picard": 8.5}


def test_names_and_slugs_meet_on_one_key():
    assert prerelease.key("U.S.S. Voyager, Lone Wanderer") == prerelease.key("uss-voyager-lone-wanderer")
    assert prerelease.key("Set Phasers to . . .") == prerelease.key("set-phasers-to--")
    assert prerelease.key("Front // Back") == prerelease.key("front")


def card(name, rarity="common", cmc=2, colors=("R",), number="1"):
    return {"name": name, "cmc": cmc, "mana_cost": "{1}{R}", "types": ["Creature"], "rarity": rarity,
            "colors": list(colors), "tags": [], "set": "NEW", "collector_number": number}


def test_predicted_dataset_is_valid_and_ranks_by_review(tmp_path):
    names = [f"Card {i}" for i in range(12)]
    cards = {n: card(n, number=str(i)) for i, n in enumerate(names)}
    cards["Forest"] = {**card("Forest", number="99"), "types": ["Land", "Basic"]}
    # Card 11 rated best by MTGAZone; Draftsim reviewed only half the cards.
    reviews = {"mtgazone": {prerelease.key(n): i * 0.4 for i, n in enumerate(names)},
               "draftsim": {n: i for i, n in enumerate(names) if i % 2}}
    calibration = {"features": prerelease.FEATURES, "sets": ["OLD"],
                   "weights": [0.0, 1.0] + [0.0] * (len(prerelease.FEATURES) - 2)}

    data = prerelease.predicted_dataset("NEW", cards, reviews, calibration, "2026-11-10")
    ratings = data["card_ratings"]
    assert "Forest" not in {c["name"] for c in ratings.values()} and len(ratings) == 12
    best = max(ratings.values(), key=lambda c: c["deck_colors"]["All Decks"]["gihwr"])
    assert best["name"] == "Card 11" and data["meta"]["predicted"]
    assert set(best["review_ratings"]) == {"mtgazone", "draftsim"}
    gih = [c["deck_colors"]["All Decks"]["gihwr"] for c in ratings.values()]
    assert abs(sum(gih) / len(gih) - prerelease.GIH_MEAN) < 0.1

    path = tmp_path / "NEW_PremierDraft_All_Data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert check_file_integrity(str(path))[0] == Result.VALID


def test_calibration_learns_from_past_sets(tmp_path):
    reviews, sets = tmp_path / "reviews", tmp_path / "Sets"
    (reviews / "mtgazone").mkdir(parents=True), sets.mkdir()
    for code in ("AAA", "BBB"):
        ratings, cards = {}, {}
        for i in range(60):
            name = f"{code} card {i}"
            ratings[prerelease.key(name)] = (i % 10) / 2
            cards[str(i)] = {**card(name), "deck_colors": {"All Decks": {"gihwr": 50 + (i % 10) + (i % 3) * 0.1}}}
        (reviews / "mtgazone" / f"{code}.json").write_text(json.dumps({"ratings": ratings}))
        (sets / f"{code}_PremierDraft_All_Data.json").write_text(json.dumps({"card_ratings": cards}))
    out = tmp_path / "calibration.json"
    prerelease.calibrate(str(reviews), str(sets), str(out))
    saved = json.loads(out.read_text())
    assert saved["sets"] == ["AAA", "BBB"] and saved["weights"][1] > 0
    # A predicted dataset is never a past set to learn from.
    (reviews / "mtgazone" / "NEW.json").write_text(json.dumps({"ratings": {"newcard": 3.0}}))
    (sets / "NEW_PremierDraft_All_Data.json").write_text(json.dumps({"meta": {"predicted": True}, "card_ratings": {}}))
    assert prerelease.past_set_rows("NEW", str(reviews), str(sets)) == []
