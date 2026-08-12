"""Manual validation of relation classification against a real OpenAI model (R2.3).

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
from app.pipeline.relations import classify_relation  # noqa: E402


@dataclass(frozen=True)
class Case:
    name: str
    n_text: str
    v_text: str
    expected: RelationLabel
    same_chunk: bool = False
    same_doc: bool = False


# Fixed set: ≥2 none, ≥2 extends (incl. Sole/Vento), ≥2 replaces.
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
    Case(
        name="replaces_employer_change",
        n_text="Alice lavora a Beta Corp.",
        v_text="Alice lavora ad Acme Corp.",
        expected=RelationLabel.replaces,
    ),
    Case(
        name="replaces_office_city",
        n_text="L'ufficio è a Milano.",
        v_text="L'ufficio è a Roma.",
        expected=RelationLabel.replaces,
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
]


async def run() -> int:
    if not settings.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY non impostata — impossibile validare sul modello reale.")
        return 2

    print(f"Model: {settings.OPENAI_MODEL}")
    print(f"Cases: {len(CASES)}\n")

    failures = 0
    for case in CASES:
        result = await classify_relation(
            case.n_text,
            case.v_text,
            same_chunk=case.same_chunk,
            same_doc=case.same_doc,
        )
        got = result.relation
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
            f"RESULT: {failures}/{len(CASES)} mismatches — "
            "refine SYSTEM_PROMPT (R2.2) and re-run."
        )
        return 1
    print(f"RESULT: all {len(CASES)} expected outcomes confirmed.")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
