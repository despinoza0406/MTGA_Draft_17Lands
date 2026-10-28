"""
tests/test_pick_model.py
Validation for the numpy runtime of the trained pick model.
"""

import json
import numpy as np
import pytest
from src.advisor import pick_model
from src.advisor.pick_model import PickModel, find_model_path, load_pick_model

CARDS = ["Alpha Bear", "Beta Wizard", "Gamma Bolt // Gamma Echo"]
HIDDEN = 4
PICKS_PER_PACK = 14


def write_model(path, card_bias=(0.0, 0.0, 0.0), pool_synergy=0.0):
    """A tiny model whose hidden layer passes the pool straight through, so
    holding 'Alpha Bear' raises 'Beta Wizard' by `pool_synergy` logits."""
    n = len(CARDS)
    pool_w = np.zeros((n, HIDDEN), np.float32)
    pool_w[0, 0] = 1.0  # Alpha Bear in the pool lights up hidden unit 0
    card_emb = np.zeros((n, HIDDEN), np.float32)
    card_emb[1, 0] = pool_synergy  # Beta Wizard reads hidden unit 0
    np.savez(
        path,
        card_names=np.array(CARDS),
        pool_w=pool_w,
        pool_b=np.zeros(HIDDEN, np.float32),
        pos_emb=np.zeros((3 * PICKS_PER_PACK, HIDDEN), np.float32),
        hidden_w=np.eye(HIDDEN, dtype=np.float32),
        hidden_b=np.zeros(HIDDEN, np.float32),
        card_emb=card_emb,
        card_bias=np.array(card_bias, np.float32),
        metadata=np.array(json.dumps({"top1": 0.5})),
    )
    return str(path)


def card(name):
    return {"name": name}


class TestPickModel:
    def test_probabilities_cover_known_pack_cards(self, tmp_path):
        model = PickModel(write_model(tmp_path / "m.npz", card_bias=(2.0, 0.0, 0.0)))
        scores = model.score_pack([card("Alpha Bear"), card("Beta Wizard"), card("Unknown Card")], [], 1, 1)

        assert set(scores) == {"Alpha Bear", "Beta Wizard"}
        assert sum(scores.values()) == pytest.approx(100.0)
        assert scores["Alpha Bear"] > scores["Beta Wizard"]

    def test_pool_changes_the_recommendation(self, tmp_path):
        model = PickModel(write_model(tmp_path / "m.npz", card_bias=(0.0, 0.0, 1.0), pool_synergy=3.0))
        pack = [card("Beta Wizard"), card("Gamma Bolt // Gamma Echo")]

        empty_pool = model.score_pack(pack, [], 1, 2)
        with_bear = model.score_pack(pack, [card("Alpha Bear")], 1, 2)

        assert empty_pool["Gamma Bolt // Gamma Echo"] > empty_pool["Beta Wizard"]
        assert with_bear["Beta Wizard"] > with_bear["Gamma Bolt // Gamma Echo"]

    def test_split_cards_match_by_front_face(self, tmp_path):
        model = PickModel(write_model(tmp_path / "m.npz"))
        assert "Gamma Bolt" in model.score_pack([card("Gamma Bolt"), card("Alpha Bear")], [], 1, 1)

    def test_position_is_clamped(self, tmp_path):
        model = PickModel(write_model(tmp_path / "m.npz"))
        assert model.picks_per_pack == PICKS_PER_PACK
        assert model._position(1, 1) == 0
        assert model._position(2, 3) == PICKS_PER_PACK + 2
        assert model._position(5, 20) == 3 * PICKS_PER_PACK - 1

    def test_find_model_prefers_exact_event_then_premier(self, tmp_path):
        premier = write_model(tmp_path / "FRA_PremierDraft_pick_model.npz")
        assert find_model_path("FRA", "QuickDraft", str(tmp_path)) == premier

        quick = write_model(tmp_path / "FRA_QuickDraft_pick_model.npz")
        assert find_model_path("FRA", "QuickDraft", str(tmp_path)) == quick
        assert find_model_path("DFT", "PremierDraft", str(tmp_path)) is None
        assert find_model_path("", "PremierDraft", str(tmp_path)) is None

    def test_load_caches_and_survives_bad_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pick_model, "_cache", {})
        (tmp_path / "BAD_PremierDraft_pick_model.npz").write_bytes(b"not a model")
        assert load_pick_model("BAD", "PremierDraft", str(tmp_path)) is None

        write_model(tmp_path / "FRA_PremierDraft_pick_model.npz")
        first = load_pick_model("FRA", "PremierDraft", str(tmp_path))
        assert first is load_pick_model("FRA", "PremierDraft", str(tmp_path))
