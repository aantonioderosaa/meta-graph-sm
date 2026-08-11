"""Dreaming consolidation schemas (tech-spec §17.2)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, model_validator

from app.models.extraction import FactType


class ConsolidationOutcome(str, Enum):
    abstraction = "abstraction"
    cleaned_fact = "cleaned_fact"


class ConsolidationResult(BaseModel):
    outcome: ConsolidationOutcome
    text: str
    type: FactType
    source_fact_ids: list[str] = []

    @model_validator(mode="after")
    def check_sources(self) -> ConsolidationResult:
        if self.outcome == ConsolidationOutcome.abstraction and not self.source_fact_ids:
            raise ValueError("abstraction richiede almeno un source_fact_id")
        return self
