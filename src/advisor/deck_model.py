"""
src/advisor/deck_model.py
Runs a trained deckbuilding model (see training/deck_model/train.py) with numpy
and builds a 40-card deck from it: the cards winning 17Lands players maindeck
from a pool like yours, with basics filled in by the regular mana base math.
"""

import logging
from collections import Counter
from typing import Callable, Dict, List, Optional

import numpy as np

from src import constants
from src.advisor.pick_model import MODELS_FOLDER, _name_key, find_model_path

logger = logging.getLogger(__name__)

MODEL_SUFFIX = "_deck_model.npz"
DECK_SIZE = 40
MIN_SPELLS, MAX_SPELLS = 21, 25


def _is_basic(card: Dict) -> bool:
    return card.get(constants.DATA_FIELD_NAME) in constants.BASIC_LANDS or (
        "Basic" in card.get(constants.DATA_FIELD_TYPES, [])
    )


class DeckModel:
    def __init__(self, path: str):
        weights = np.load(path)
        self.path = path
        self.pool_w = weights["pool_w"]
        self.pool_b = weights["pool_b"]
        self.hidden_w = weights["hidden_w"]
        self.hidden_b = weights["hidden_b"]
        self.card_emb = weights["card_emb"]
        self.card_bias = weights["card_bias"]
        self.index = {_name_key(n): i for i, n in enumerate(weights["card_names"])}

    def inclusion(self, pool_cards: List[Dict]) -> Dict[str, float]:
        """Returns {card name: probability (0-1) each copy makes the maindeck}."""
        counts = Counter(
            c.get(constants.DATA_FIELD_NAME, "") for c in pool_cards if not _is_basic(c)
        )
        vec = np.zeros(len(self.index), dtype=np.float32)
        known = {}
        for name, count in counts.items():
            idx = self.index.get(_name_key(name))
            if idx is not None:
                vec[idx] += count
                known[name] = idx
        if not known:
            return {}
        h = np.maximum(vec @ self.pool_w + self.pool_b, 0)
        h = np.maximum(h @ self.hidden_w + self.hidden_b, 0)
        names = list(known)
        logits = self.card_emb[[known[n] for n in names]] @ h + self.card_bias[[known[n] for n in names]]
        return {n: float(p) for n, p in zip(names, 1.0 / (1.0 + np.exp(-logits)))}

    def build(self, pool_cards: List[Dict], allowed: Optional[Callable[[Dict], bool]] = None,
              min_spells: int = MIN_SPELLS) -> List[Dict]:
        """Chooses the maindeck copies (no basics). The number of cards follows the
        model's own expectation, clamped to a sane 21-25 range.

        allowed: limit the choice to some cards (e.g. one color pair). The model
        still reads the whole pool, so its sense of what the pool wants stays."""
        probs = self.inclusion(pool_cards)
        if allowed is not None:
            probs = {
                n: p for n, p in probs.items()
                if any(c.get(constants.DATA_FIELD_NAME) == n and allowed(c) for c in pool_cards)
            }
        # Every copy of a card shares one probability p, so with n copies the
        # model expects p*n of them: copy j scores clip(p*n - j, 0, 1).
        totals = Counter()
        for c in pool_cards:
            if not _is_basic(c) and c.get(constants.DATA_FIELD_NAME) in probs:
                totals[c[constants.DATA_FIELD_NAME]] += c.get("count", 1)
        seen = Counter()
        copies = []
        for i, c in enumerate(pool_cards):
            name = c.get(constants.DATA_FIELD_NAME)
            if _is_basic(c) or name not in probs:
                continue
            for _ in range(c.get("count", 1)):
                expected = probs[name] * totals[name]
                copies.append((min(1.0, max(0.0, expected - seen[name])), i))
                seen[name] += 1
        size = int(round(sum(p for p, _ in copies)))
        size = min(max(min_spells, min(MAX_SPELLS, size)), len(copies))
        chosen = Counter(i for _, i in sorted(copies, key=lambda x: -x[0])[:size])
        deck = []
        for i, n in chosen.items():
            card = dict(pool_cards[i])
            card["count"] = n
            deck.append(card)
        return deck


    def pair_strength(self, pool_cards: List[Dict]) -> List[tuple]:
        """Two-color pairs ranked by how many maindeck slots the model expects
        from their cards: [(pair, expected cards)], strongest first."""
        probs = self.inclusion(pool_cards)
        out = []
        for i, a in enumerate("WUBRG"):
            for b in "WUBRG"[i + 1:]:
                total = 0.0
                for c in pool_cards:
                    colors = set(c.get("colors") or [])
                    name = c.get(constants.DATA_FIELD_NAME)
                    if _is_basic(c) or name not in probs or not colors or not colors <= {a, b}:
                        continue
                    total += probs[name] * c.get("count", 1)
                out.append((a + b, total))
        return sorted(out, key=lambda kv: -kv[1])


_cache: Dict[str, Optional[DeckModel]] = {}


def load_deck_model(set_code: str, event_type: str, folder: str = MODELS_FOLDER) -> Optional[DeckModel]:
    path = find_model_path(set_code, event_type, folder, MODEL_SUFFIX)
    if path is None:
        return None
    if path not in _cache:
        try:
            _cache[path] = DeckModel(path)
            logger.info(f"Loaded deck model {path}")
        except Exception as e:
            logger.warning(f"Deck model load error ({path}): {e}")
            _cache[path] = None
    return _cache[path]
