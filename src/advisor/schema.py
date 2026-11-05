"""
src/advisor/schema.py
Data models for the Draft Advisor's recommendations.
"""

from typing import List, Optional
from pydantic import BaseModel


class Recommendation(BaseModel):
    card_name: str
    contextual_score: float
    z_score: float
    wheel_chance: float  # 0.0 to 100.0 (Polynomial probability)
    reasoning: List[str]  # e.g. ["Uncastable (Double Pip)", "Wheels 80%"]
    is_elite: bool = False
    tags: List[str] = []
    model_pick: Optional[float] = None  # 0.0 to 100.0 (trained pick model)
    pair_slot: int = 0  # Pick Two: 1 = first card, 2 = second card, 3 = both copies
