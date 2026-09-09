"""M1 — one structured LLM summary per lexical zone.

Re-ingest cache lives on ``:Zona`` (not ``:EgChunk``). Optional Neo4j session
MATCH/MERGEs summary fields plus ``testo`` (needed to re-expand a zone).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.models.event_graph import ZonaSummary
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.zona_segmentation import Zona

_MATCH_ZONA = (
    "MATCH (z:Zona {id:$id}) "
    "RETURN z.riassunto, z.entita_principali, z.ancore_temporali, "
    "z.evento_centrale, z.versione_regole"
)
_MERGE_ZONA = (
    "MERGE (z:Zona {id:$id}) "
    "SET z.riassunto=$riassunto, "
    "z.entita_principali=$entita_principali, "
    "z.ancore_temporali=$ancore_temporali, "
    "z.evento_centrale=$evento_centrale, "
    "z.documento=$documento, "
    "z.offset_inizio=$offset_inizio, "
    "z.offset_fine=$offset_fine, "
    "z.ordinale=$ordinale, "
    "z.espansa=$espansa, "
    "z.regola=$regola, "
    "z.versione_regole=$versione_regole, "
    "z.testo=$testo, "
    "z.su_via_principale=$su_via_principale"
)

SYSTEM_ZONA_SUMMARY = """\
You produce a ZonaSummary for one lexical zone of Italian or English text.

Return structured output only. Temperature is 0. Never call tools. Never open
a tool loop. No function calling. Structured fields only.

Fields:
- riassunto: 2–4 sentences / 2–4 frasi. State only what the zone text says.
  Nessuna inferenza oltre il testo della zona. No inference beyond the zone text.
- entita_principali: referential names exactly as they appear (persone, luoghi,
  organizzazioni). Do not invent entities that are not in the zone text.
  Non inventare entità assenti dal testo.
- ancore_temporali: dates and temporal expressions as they appear (ieri, nel 1994,
  alle tre, yesterday, in 1994). May be empty / può essere vuota.
- evento_centrale: lemma or short span of the central event, or null if none.

English and Italian. Conservative. Do not invent / non inventare.
"""


def user_zona_summary(zona: Zona) -> str:
    return (
        "Riassumi solo il testo della zona / Summarize only this zone text.\n"
        "Non inventare entità, date o eventi assenti dal testo.\n"
        "Do not invent entities, dates, or events that are not in the text.\n\n"
        f"zona_id: {zona.id}\n"
        f"ordinale: {zona.ordinale}\n"
        f"documento: {zona.documento}\n\n"
        f"{zona.testo}\n"
    )


def applica_summary(zona: Zona, summary: ZonaSummary) -> Zona:
    """Copy ZonaSummary fields onto a new Zona (Zona is not frozen)."""
    return replace(
        zona,
        riassunto=summary.riassunto or "",
        entita_principali=list(summary.entita_principali),
        ancore_temporali=list(summary.ancore_temporali),
        evento_centrale=summary.evento_centrale,
        avviso=None,
    )


def _has_cached_summary(zona: Zona) -> bool:
    return bool((zona.riassunto or "").strip()) and zona.versione_regole == RULESET_VERSION


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def session_run(
    session: Any, query: str, parameters: dict | None = None, **kwargs: Any
) -> Any:
    """Run Cypher on a real AsyncSession or a test FakeSession."""
    params = {**(parameters or {}), **kwargs}
    try:
        result = session.run(query, **params)
    except TypeError:
        result = session.run(query, params)
    return await _maybe_await(result)


def _record_mapping(record: Any) -> Mapping[str, Any]:
    if record is None:
        return {}
    if isinstance(record, Mapping):
        return record
    data_fn = getattr(record, "data", None)
    if callable(data_fn):
        try:
            data = data_fn()
        except TypeError:
            data = None
        if isinstance(data, Mapping):
            return data
    keys_fn = getattr(record, "keys", None)
    if callable(keys_fn):
        try:
            return {key: record[key] for key in keys_fn()}
        except Exception:
            return {}
    return {}


def _mapping_get(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return []


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def load_cached_zona_summary(session: Any, zona_id: str) -> ZonaSummary | None:
    """Reuse ``:Zona`` summary when versione_regole matches and riassunto is set."""
    try:
        result = await session_run(session, _MATCH_ZONA, id=zona_id)
    except Exception:
        return None
    single_fn = getattr(result, "single", None)
    if single_fn is None:
        return None
    try:
        record = await _maybe_await(single_fn())
    except Exception:
        return None
    data = _record_mapping(record)
    riassunto = _mapping_get(data, "z.riassunto", "riassunto")
    versione = _mapping_get(data, "z.versione_regole", "versione_regole")
    if not (isinstance(riassunto, str) and riassunto.strip()):
        return None
    if versione != RULESET_VERSION:
        return None
    return ZonaSummary(
        riassunto=riassunto,
        entita_principali=_as_str_list(
            _mapping_get(data, "z.entita_principali", "entita_principali")
        ),
        ancore_temporali=_as_str_list(
            _mapping_get(data, "z.ancore_temporali", "ancore_temporali")
        ),
        evento_centrale=_optional_str(
            _mapping_get(data, "z.evento_centrale", "evento_centrale")
        ),
    )


async def persist_zona_summary(session: Any, zona: Zona) -> None:
    """MERGE ``:Zona`` with summary fields plus identity/offset metadata."""
    await session_run(
        session,
        _MERGE_ZONA,
        id=zona.id,
        riassunto=zona.riassunto,
        entita_principali=list(zona.entita_principali),
        ancore_temporali=list(zona.ancore_temporali),
        evento_centrale=zona.evento_centrale,
        documento=zona.documento,
        offset_inizio=zona.offset_inizio,
        offset_fine=zona.offset_fine,
        ordinale=zona.ordinale,
        espansa=zona.espansa,
        regola=zona.regola,
        versione_regole=zona.versione_regole,
        testo=zona.testo,
        su_via_principale=list(zona.su_via_principale),
    )


def _as_summary(parsed: Any) -> ZonaSummary:
    if isinstance(parsed, ZonaSummary):
        return parsed
    return ZonaSummary.model_validate(parsed)


async def riassumi_zona(
    zona: Zona,
    *,
    job_id: str | None = None,
    session: Any = None,
) -> Zona:
    """One LLM call per zone. Cache hit or empty text skips the call."""
    if not (zona.testo or "").strip():
        return zona
    if _has_cached_summary(zona):
        return zona
    if session is not None:
        cached = await load_cached_zona_summary(session, zona.id)
        if cached is not None:
            return applica_summary(zona, cached)
    try:
        parsed = await call_structured(
            SYSTEM_ZONA_SUMMARY,
            user_zona_summary(zona),
            ZonaSummary,
            temperature=0,
            job_id=job_id,
        )
        summary = _as_summary(parsed)
    except Exception:
        return replace(zona, avviso="estrazione fallita")
    updated = applica_summary(zona, summary)
    if session is not None:
        try:
            await persist_zona_summary(session, updated)
        except Exception:
            pass
    return updated


async def riassumi_zone(
    zone: list[Zona],
    *,
    job_id: str | None = None,
    session: Any = None,
) -> list[Zona]:
    """Summarize each zone. ``call_structured`` already limits concurrency."""
    if not zone:
        return []
    return list(
        await asyncio.gather(
            *(riassumi_zona(item, job_id=job_id, session=session) for item in zone)
        )
    )


__all__ = [
    "SYSTEM_ZONA_SUMMARY",
    "applica_summary",
    "load_cached_zona_summary",
    "persist_zona_summary",
    "riassumi_zona",
    "riassumi_zone",
    "user_zona_summary",
]
