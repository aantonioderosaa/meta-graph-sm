"""Relation classification schemas (tech-spec §17.3)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RelationLabel(str, Enum):
    extends = "extends"
    none = "none"
    supersedes = "supersedes"
    updated_by = "updated_by"


class RelationClassification(BaseModel):
    relation: RelationLabel
