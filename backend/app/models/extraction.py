"""Fact extraction schemas (tech-spec §17.1)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class FactType(str, Enum):
    fact = "fact"
    preference = "preference"
    episode = "episode"


class ExtractedFact(BaseModel):
    text: str
    type: FactType


class FactExtractionResult(BaseModel):
    facts: list[ExtractedFact]
