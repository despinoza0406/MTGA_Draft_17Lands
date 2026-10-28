"""
src/advisor/pick_model.py
Runs a trained pick model (see training/pick_model/train.py) with plain numpy.

The model scores every card in the pack given the current pool and the pick
position, and returns the probability that a winning 17Lands drafter takes it.
"""

import os
import glob
import json
import logging
from collections import Counter
from typing import Dict, List, Optional

import numpy as np

from src import constants

logger = logging.getLogger(__name__)

MODELS_FOLDER = os.path.join(constants.BASE_DIR, "Models")
MODEL_SUFFIX = "_pick_model.npz"
PACKS_PER_DRAFT = 3


def _name_key(name: str) -> str:
    return str(name).split(" // ")[0].strip().lower()


class PickModel:
    def __init__(self, path: str):
        weights = np.load(path)
        self.path = path
        self.pool_w = weights["pool_w"]
        self.pool_b = weights["pool_b"]
        self.pos_emb = weights["pos_emb"]
        self.hidden_w = weights["hidden_w"]
        self.hidden_b = weights["hidden_b"]
        self.card_emb = weights["card_emb"]
        self.card_bias = weights["card_bias"]
        self.metadata = json.loads(str(weights["metadata"]))
        self.picks_per_pack = self.pos_emb.shape[0] // PACKS_PER_DRAFT
        self.index = {_name_key(n): i for i, n in enumerate(weights["card_names"])}

    def _position(self, pack: int, pick: int) -> int:
        pos = (max(1, pack) - 1) * self.picks_per_pack + (max(1, pick) - 1)
        return min(pos, self.pos_emb.shape[0] - 1)

    def score_pack(
        self, pack_cards: List[Dict], taken_cards: List[Dict], pack: int, pick: int
    ) -> Dict[str, float]:
        """Returns {card name: pick probability in %} for the cards the model knows."""
        pack_idx = {}
        for card in pack_cards:
            name = card.get(constants.DATA_FIELD_NAME, "")
            idx = self.index.get(_name_key(name))
            if idx is not None:
                pack_idx[name] = idx
        if not pack_idx:
            return {}

        pool = np.zeros(len(self.index), dtype=np.float32)
        for name, count in Counter(
            c.get(constants.DATA_FIELD_NAME, "") for c in taken_cards
        ).items():
            idx = self.index.get(_name_key(name))
            if idx is not None:
                pool[idx] += count

        h = np.maximum(pool @ self.pool_w + self.pool_b + self.pos_emb[self._position(pack, pick)], 0)
        h = np.maximum(h @ self.hidden_w + self.hidden_b, 0)

        names = list(pack_idx)
        cols = np.array([pack_idx[n] for n in names])
        logits = self.card_emb[cols] @ h + self.card_bias[cols]
        probs = np.exp(logits - logits.max())
        probs /= probs.sum()
        return {n: float(p * 100.0) for n, p in zip(names, probs)}


_cache: Dict[str, Optional[PickModel]] = {}


def find_model_path(set_code: str, event_type: str, folder: str = MODELS_FOLDER) -> Optional[str]:
    """Prefers an exact set/event model, then the set's Premier Draft model, then any for the set."""
    if not set_code:
        return None
    for candidate in (f"{set_code}_{event_type}", f"{set_code}_PremierDraft"):
        path = os.path.join(folder, candidate + MODEL_SUFFIX)
        if os.path.exists(path):
            return path
    matches = sorted(glob.glob(os.path.join(folder, f"{set_code}_*{MODEL_SUFFIX}")))
    return matches[0] if matches else None


def load_pick_model(set_code: str, event_type: str, folder: str = MODELS_FOLDER) -> Optional[PickModel]:
    path = find_model_path(set_code, event_type, folder)
    if path is None:
        return None
    if path not in _cache:
        try:
            _cache[path] = PickModel(path)
            logger.info(f"Loaded pick model {path}")
        except Exception as e:
            logger.warning(f"Pick model load error ({path}): {e}")
            _cache[path] = None
    return _cache[path]
