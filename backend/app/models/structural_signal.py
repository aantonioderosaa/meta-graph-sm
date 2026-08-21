"""Structured verdict for the Fase 24 structural-signal model fallback."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MarkerCategoryName = Literal["quantifier", "retraction", "error", "succession"]


class StructuralSignalVerdict(BaseModel):
    """Model output: whether a fragment carries a structural context signal.

    Conservative: if unsure, ``has_signal`` must be false. Never invents
    witnesses. ``marker_category`` is required only when ``has_signal`` is true.
    """

    has_signal: bool = Field(
        description=(
            "True only if the fragment is a quantifier, retraction, correction, "
            "or state succession — not a simply new fact."
        )
    )
    marker_category: MarkerCategoryName | None = Field(
        default=None,
        description="quantifier, retraction, error, or succession when has_signal",
    )
    claim_target_hint: str = Field(
        default="",
        description="Optional hint of the claim or genre the signal is about",
    )
    reasoning: str = Field(
        default="",
        description="Short audit trail; empty is allowed when has_signal is false",
    )
