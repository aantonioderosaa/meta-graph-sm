"""Content-addressed identifiers (piano sez. 6).

Inner ``hash(text)`` is SHA-1 hex of UTF-8 bytes — never Python's builtin
``hash()``, which is process-salted and unstable across runs.
Ids do **not** include ``RULESET_VERSION``.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

from app.pipeline.event_graph.tempo_iso import analizza
from app.pipeline.event_graph.text_norm import _normalize_referential

_CONTENT_TIPI = frozenset({"nome_proprio", "sn_comune"})


def content_hash(text: str) -> str:
    """SHA-1 hex digest of UTF-8 bytes."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def evento_id(doc_id: str, testo_chunk: str, indice_chunk: int | str) -> str:
    return content_hash(f"{doc_id}|{content_hash(testo_chunk)}|{indice_chunk}")


class MenzioneId(NamedTuple):
    """Mention id plus the unresolved flag the caller must persist."""

    id: str
    non_risolto: bool


def menzione_id(
    forma_canonica: str,
    tipo_superficiale: str,
    doc_id: str,
    chunk_id: str,
    indice_menzione: int | str,
) -> MenzioneId:
    forma = (forma_canonica or "").strip()
    if tipo_superficiale in _CONTENT_TIPI and forma:
        return MenzioneId(
            id=content_hash(_normalize_referential(forma)),
            non_risolto=False,
        )
    return MenzioneId(
        id=content_hash(f"{doc_id}|{chunk_id}|{indice_menzione}"),
        non_risolto=True,
    )


def eg_chunk_id(doc_id: str, ordinale: int | str, testo: str) -> str:
    return content_hash(f"{doc_id}|{ordinale}|{content_hash(testo)}")


def zona_id(doc_id: str, ordinale: int | str, testo: str) -> str:
    return content_hash(f"{doc_id}|{ordinale}|{content_hash(testo)}")


def cluster_temporale_id(doc_id: str, etichetta: str, tipo: str) -> str:
    return content_hash(f"{doc_id}|{etichetta}|{tipo}")


def _token_canonico(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def _chiave_undated(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().casefold().split())


def _iso_canonico(value: object) -> str:
    tempo = analizza(value)
    return "" if tempo is None else tempo.canonico


def ancora_temporale_id(
    documento: str,
    natura: str,
    tipo: str,
    inizio: str | None = None,
    fine: str | None = None,
    chiave: str | None = None,
) -> str:
    """Stable, document-local id for an ``:AncoraTemporale``.

    Hashes the normalized collocazione (documento + natura + tipo +
    canonical ISO inizio/fine), never a volatile etichetta. Two identical
    ingestions therefore produce the same id. When the ancora is undated,
    ``chiave`` is the identity key (normalized expression), not the display
    label. Ancore are document-local: two docs both talking about 1843 get
    two ids. ``RULESET_VERSION`` is not hashed.
    """
    natura_n = _token_canonico(natura)
    tipo_n = _token_canonico(tipo)
    inizio_n = _iso_canonico(inizio)
    fine_n = _iso_canonico(fine)
    if inizio_n or fine_n:
        payload = f"ancora|{documento}|{natura_n}|{tipo_n}|{inizio_n}|{fine_n}"
    else:
        payload = f"ancora|{documento}|{natura_n}|{tipo_n}|#{_chiave_undated(chiave)}"
    return content_hash(payload)


def quarantena_id(doc_id: str, testo_chunk: str, span: str, motivo: str) -> str:
    return content_hash(f"{doc_id}|{content_hash(testo_chunk)}|{span}|{motivo}")
