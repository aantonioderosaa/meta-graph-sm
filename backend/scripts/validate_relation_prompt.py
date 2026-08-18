"""Manual validation of relation classification against a real OpenAI model (R2.3 / T1.3).

Not part of CI — requires OPENAI_API_KEY (from env or repo-root /.env).

Usage (from backend/):
  python scripts/validate_relation_prompt.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.models.relations import RelationLabel  # noqa: E402
from app.pipeline.entity_relation_resolution import classify_relation  # noqa: E402


@dataclass(frozen=True)
class Case:
    name: str
    n_text: str
    v_text: str
    expected: RelationLabel | None  # None = qualitative / borderline (no pass-fail)
    same_chunk: bool = False
    same_doc: bool = False


# Fixed set: R2.3 baseline + T1.3 temporal-marker cases.
CASES: list[Case] = [
    Case(
        name="extends_sole_vento_episode",
        n_text="Il sole uscì da dietro le nuvole e scaldò il viandante.",
        v_text="Il vento soffiava con forza cercando di togliere il mantello al viandante.",
        expected=RelationLabel.extends,
        same_chunk=True,
    ),
    Case(
        name="extends_same_episode_moments",
        n_text="Il viandante strinse il mantello intorno alle spalle.",
        v_text="Il vento soffiava con forza sulla strada di campagna.",
        expected=RelationLabel.extends,
        same_chunk=True,
    ),
    Case(
        name="none_unrelated_same_doc",
        n_text="Alice lavora ad Acme Corp.",
        v_text="Il vento soffiava forte sulla spiaggia.",
        expected=RelationLabel.none,
        same_doc=True,
    ),
    Case(
        name="none_genuinely_unrelated",
        n_text="Il prezzo del caffè è salito del 10%.",
        v_text="Il treno per Milano parte alle 9:00.",
        expected=RelationLabel.none,
    ),
    # R2.3 succession cases (T1 replaces → F9 supersedes).
    Case(
        name="supersedes_employer_change_temporal",
        n_text="Da gennaio 2024 Alice lavora a Beta Corp.",
        v_text="Fino al 2023 Alice lavorava ad Acme Corp.",
        expected=RelationLabel.supersedes,
    ),
    Case(
        name="supersedes_office_city_temporal",
        n_text="Ora l'ufficio è a Milano.",
        v_text="Fino al mese scorso l'ufficio era a Roma.",
        expected=RelationLabel.supersedes,
    ),
    Case(
        name="extends_complementary_detail",
        n_text="L'ufficio ha una palestra sul tetto.",
        v_text="L'ufficio è a Milano.",
        expected=RelationLabel.extends,
        same_doc=True,
    ),
    Case(
        name="none_same_doc_unrelated_topics",
        n_text="La ricetta richiede due uova.",
        v_text="Il server di produzione è in us-east-1.",
        expected=RelationLabel.none,
        same_doc=True,
    ),
    # T1.3: no temporal markers — sequential narrative moments → extends (not replaces).
    Case(
        name="extends_narrative_no_temporal_mantello",
        n_text="Il viandante strinse il mantello intorno alle spalle.",
        v_text="Il vento soffiava forte sulla strada.",
        expected=RelationLabel.extends,
        same_chunk=True,
    ),
    Case(
        name="extends_narrative_no_temporal_sole",
        n_text="Il sole uscì e scaldò il viandante.",
        v_text="Il vento soffiava cercando di strappargli il mantello.",
        expected=RelationLabel.extends,
        same_chunk=True,
    ),
    # T1.3: borderline / ambiguous — observe justification only.
    Case(
        name="borderline_job_without_temporal",
        n_text="Alice lavora a Beta Corp.",
        v_text="Alice lavora ad Acme Corp.",
        expected=None,
    ),
]


async def run() -> int:
    if not settings.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY non impostata — impossibile validare sul modello reale.")
        return 2

    print(f"Model: {settings.OPENAI_MODEL}")
    print(f"Cases: {len(CASES)}\n")

    failures = 0
    scored = 0
    for case in CASES:
        result = await classify_relation(
            case.n_text,
            case.v_text,
            same_chunk=case.same_chunk,
            same_doc=case.same_doc,
        )
        got = result.relation
        if case.expected is None:
            print(
                f"[OBS] {case.name}: got={got.value}"
                f" (same_chunk={case.same_chunk}, same_doc={case.same_doc})"
            )
            print(f"       N: {case.n_text}")
            print(f"       V: {case.v_text}")
            continue

        scored += 1
        ok = got == case.expected
        mark = "OK" if ok else "FAIL"
        if not ok:
            failures += 1
        print(
            f"[{mark}] {case.name}: expected={case.expected.value} got={got.value}"
            f" (same_chunk={case.same_chunk}, same_doc={case.same_doc})"
        )
        print(f"       N: {case.n_text}")
        print(f"       V: {case.v_text}")

    print()
    if failures:
        print(
            f"RESULT: {failures}/{scored} mismatches — "
            "refine SYSTEM_PROMPT (T1.1/T1.2/F9) and re-run."
        )
        return 1
    print(f"RESULT: all {scored} expected outcomes confirmed "
          f"({len(CASES) - scored} observational).")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
