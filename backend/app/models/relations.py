"""Relation classification schemas (tech-spec §17.3)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RelationLabel(str, Enum):
    replaces = "replaces"
    extends = "extends"
    none = "none"


class RelationClassification(BaseModel):
    relation: RelationLabel
