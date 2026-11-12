"""
tests/test_deck_model.py
Validation for the trained deckbuilding model runtime and the Model Build variant.
"""

import json
import numpy as np
import pytest
from src.advisor import deck_model
from src.advisor.deck_model import DeckModel, load_deck_model
from src.advisor.deck_builder import build_variant_model

HIDDEN = 2


def spell(name, color="U", count=1):
    return {
        "name": name,
        "types": ["Creature"],
        "colors": [color],
        "cmc": 2,
        "mana_cost": "{1}{" + color + "}",
        "set": "TST",
        "count": count,
    }


def write_model(path, names, bias):
    n = len(names)
    np.savez(
        path,
        card_names=np.array(names),
        pool_w=np.zeros((n, HIDDEN), np.float32),
        pool_b=np.zeros(HIDDEN, np.float32),
        hidden_w=np.zeros((HIDDEN, HIDDEN), np.float32),
        hidden_b=np.zeros(HIDDEN, np.float32),
        card_emb=np.zeros((n, HIDDEN), np.float32),
        card_bias=np.array(bias, np.float32),
        metadata=np.array(json.dumps({})),
    )
    return str(path)


@pytest.fixture
def pool():
    """24 strong blue cards, 6 weak red cards, plus basics that must be ignored."""
    cards = [spell(f"Blue {i}") for i in range(24)] + [spell(f"Red {i}", "R") for i in range(6)]
    cards.append({"name": "Island", "types": ["Land", "Basic"], "colors": ["U"], "set": "TST", "count": 7})
    return cards


@pytest.fixture
def model(tmp_path, pool):
    names = [c["name"] for c in pool if c["name"] != "Island"]
    bias = [3.0 if n.startswith("Blue") else -3.0 for n in names]  # p ~ 0.95 vs 0.05
    return DeckModel(write_model(tmp_path / "TST_PremierDraft_deck_model.npz", names, bias))


def test_inclusion_ignores_basics_and_unknown_cards(model, pool):
    probs = model.inclusion(pool + [spell("Unknown Card")])
    assert "Island" not in probs and "Unknown Card" not in probs
    assert probs["Blue 0"] > 0.9 > 0.1 > probs["Red 0"]


def test_build_takes_expected_number_of_best_cards(model, pool):
    deck = model.build(pool)
    names = {c["name"] for c in deck}
    # Expected size = round(24 * 0.95 + 6 * 0.05) = 23
    assert sum(c["count"] for c in deck) == 23
    assert all(n.startswith("Blue") for n in names)


def test_duplicate_copies_follow_expected_count(tmp_path):
    names = ["Twin"] + [f"Filler {i}" for i in range(22)]
    bias = [np.log(2 / 3) - np.log(1 / 3)] + [4.0] * 22  # Twin p = 2/3, fillers ~0.98
    model = DeckModel(write_model(tmp_path / "m.npz", names, bias))
    pool = [spell("Twin", count=3)] + [spell(n) for n in names[1:]]

    twin = next(c for c in model.build(pool) if c["name"] == "Twin")
    assert twin["count"] == 2  # 3 copies * 2/3, not all three


def test_model_variant_builds_full_deck(model, pool, monkeypatch):
    monkeypatch.setattr(deck_model, "load_deck_model", lambda set_code, event: model)
    deck, colors = build_variant_model(pool)

    assert colors == ["U"]
    assert sum(c.get("count", 1) for c in deck) == 40
    assert all("R" not in c.get("colors", []) for c in deck)


def test_model_variant_without_model(pool, monkeypatch):
    monkeypatch.setattr(deck_model, "load_deck_model", lambda set_code, event: None)
    assert build_variant_model(pool) == (None, [])


def test_load_finds_deck_model_by_set(tmp_path, monkeypatch):
    monkeypatch.setattr(deck_model, "_cache", {})
    write_model(tmp_path / "TST_PremierDraft_deck_model.npz", ["A"], [0.0])
    assert load_deck_model("TST", "QuickDraft", str(tmp_path)) is not None
    assert load_deck_model("NOP", "QuickDraft", str(tmp_path)) is None


def test_build_within_allowed_cards_fills_to_min_spells(model, pool):
    deck = model.build(pool, allowed=lambda c: "R" in c["colors"] or c["name"] in {f"Blue {i}" for i in range(20)},
                       min_spells=23)
    assert sum(c["count"] for c in deck) == 23
    assert {c["name"] for c in deck} >= {f"Red {i}" for i in range(3)}  # filled past the 20 blues


def test_pair_strength_ranks_the_pool_main_pair(model, pool):
    ranked = model.pair_strength(pool)
    assert ranked[0][0] in {"WU", "UB", "UR", "UG"}
    assert ranked[0][1] > 20


def test_model_variants_add_another_pair_only_when_it_is_a_real_deck(tmp_path, monkeypatch):
    from src.advisor.deck_builder import MODEL_VARIANT, build_model_variants

    pool = ([spell(f"Blue {i}") for i in range(24)] + [spell(f"Red {i}", "R") for i in range(14)]
            + [spell(f"Green {i}", "G") for i in range(12)])
    names = [c["name"] for c in pool]
    bias = [3.0 if n.startswith("Blue") else (0.5 if n.startswith("Red") else -3.0) for n in names]
    model = DeckModel(write_model(tmp_path / "m.npz", names, bias))
    monkeypatch.setattr(deck_model, "load_deck_model", lambda set_code, event: model)
    builds = build_model_variants(pool)
    labels = [name for name, _, _ in builds]
    assert labels[0] == MODEL_VARIANT
    assert f"{MODEL_VARIANT} (alt)" in labels
    for _, deck, _ in builds:
        assert sum(c.get("count", 1) for c in deck) == 40


def test_model_variant_drops_an_unsupported_double_pip_splash(tmp_path, monkeypatch):
    """The model loves a {B}{B} bomb in a UR pool with no black fixing; the build doesn't."""
    blue = [spell(f"Blue {i}") for i in range(12)]
    red = [spell(f"Red {i}", "R") for i in range(12)]
    bomb = {**spell("Bomb", "B"), "mana_cost": "{2}{B}{B}", "cmc": 4}
    single = {**spell("Single", "B"), "mana_cost": "{2}{B}", "cmc": 3}
    fixer = {"name": "Swamp Dual", "types": ["Land"], "colors": ["U", "B"], "set": "TST",
             "oracle_text": "{T}: Add {U} or {B}."}
    names = [c["name"] for c in blue + red] + ["Bomb", "Single", "Spare", "Swamp Dual"]
    spare = spell("Spare")
    model = DeckModel(write_model(tmp_path / "m.npz", names, [4.0] * 24 + [6.0, 6.0, -1.0, -6.0]))
    monkeypatch.setattr(deck_model, "load_deck_model", lambda set_code, event: model)

    deck, _ = build_variant_model(blue + red + [bomb, single, spare])
    assert not {"Bomb", "Single"} & {c["name"] for c in deck}  # no fixing at all

    deck, _ = build_variant_model(blue + red + [bomb, single, spare, fixer])
    names_in = {c["name"] for c in deck}
    assert "Single" in names_in and "Bomb" not in names_in  # one source carries one pip, not two


def test_model_variant_keeps_copies_of_a_stacked_sealed_pool(tmp_path, monkeypatch):
    # Sealed pools list each card once with its copies; the deck must keep both.
    names = [f"Blue {i}" for i in range(11)]
    model = DeckModel(write_model(tmp_path / "m.npz", names, [4.0] * 11))
    monkeypatch.setattr(deck_model, "load_deck_model", lambda set_code, event: model)
    stacked = [spell(n, count=2) for n in names]
    deck, _ = build_variant_model(stacked, "Sealed")
    assert sum(c["count"] for c in deck) == 40
    assert all(c["count"] == 2 for c in deck if c["name"].startswith("Blue"))


def test_sealed_events_use_the_sets_sealed_model(tmp_path):
    from src.advisor.pick_model import find_model_path

    for event in ("Sealed", "PremierDraft"):
        write_model(tmp_path / f"TST_{event}_deck_model.npz", ["A"], [0.0])
    find = lambda event: find_model_path("TST", event, str(tmp_path), "_deck_model.npz")
    assert find("TradSealed").endswith("TST_Sealed_deck_model.npz")
    assert find("ArenaDirect_Sealed").endswith("TST_Sealed_deck_model.npz")
    assert find("QuickDraft").endswith("TST_PremierDraft_deck_model.npz")
