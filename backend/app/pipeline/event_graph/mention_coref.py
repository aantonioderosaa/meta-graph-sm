"""§9 — conservative mention coreference at write time (piano sez. 9, D4).

Referential-form string match (exact / token-aware substring on the
normalized form), pronoun with a single compatible antecedent, and
sogg_nullo with a unique previous SOGG. No identity resolution beyond
those rules; no vector similarity.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterable, Sequence
from typing import Any

from app.models.event_graph import (
    ArgomentoRisolto,
    ChunkFactsheet,
    EventoRisolto,
    MenzioneRisolta,
    RiferimentoMenzione,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.ids import content_hash
from app.pipeline.event_graph.text_norm import _normalize_referential
from app.pipeline.event_graph.entita_forma import pulisci_forma

REGOLA = "mention_coref.risolvi_intra"
REGOLA_PERSISTENTE = "mention_coref.fondi_referenziali_vs_persistente"

_ANTECEDENT_TIPI = frozenset({"nome_proprio", "sn_comune"})
_REFERENTIAL_TIPI = _ANTECEDENT_TIPI
_PERSISTED_QUERY = (
    "MATCH (m:Menzione) "
    "RETURN m.id AS id, m.forma_canonica AS forma_canonica, "
    "m.forma AS forma, m.summary AS summary, m.riassunti AS riassunti, "
    "m.eventi AS eventi, m.riferimenti AS riferimenti, m.occorrenze AS occorrenze"
)


def _name_tokens(forma: str) -> list[str]:
    norm = _normalize_referential(forma)
    return norm.split() if norm else []


def _token_span_contained(short: list[str], long: list[str]) -> bool:
    if not short or not long or len(short) > len(long):
        return False
    span = len(short)
    for start in range(len(long) - span + 1):
        if long[start : start + span] == short:
            return True
    return False


def _names_match(a: str, b: str) -> bool:
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    # A coordination ("il Sole e il Vento") must not bridge two referents.
    if "e" in ta or "e" in tb or "and" in ta or "and" in tb:
        return False
    return _token_span_contained(ta, tb) or _token_span_contained(tb, ta)


def _compatibile(left: MenzioneRisolta, right: MenzioneRisolta) -> bool:
    def _dim(a: str, b: str) -> bool:
        return a == "ignoto" or b == "ignoto" or a == b

    return _dim(left.numero, right.numero) and _dim(left.genere, right.genere)


def _event_sort_key(event: EventoRisolto) -> tuple[int, int, int]:
    return (
        event.posizione_doc if event.posizione_doc is not None else 0,
        event.posizione_chunk if event.posizione_chunk is not None else 0,
        event.frase_indice if event.frase_indice is not None else 0,
    )


def _sorted_events(eventi: Sequence[EventoRisolto]) -> list[EventoRisolto]:
    return sorted(eventi, key=_event_sort_key)


def _unique_events(
    *groups: Iterable[EventoRisolto],
) -> list[EventoRisolto]:
    seen: set[object] = set()
    out: list[EventoRisolto] = []
    for group in groups:
        for event in group:
            key: object = event.id if event.id else id(event)
            if key in seen:
                continue
            seen.add(key)
            out.append(event)
    return out


def _unpack_eventi(
    eventi: Sequence[EventoRisolto] | Any,
) -> tuple[list[EventoRisolto], list[MenzioneRisolta]]:
    extra: list[MenzioneRisolta] = []
    if eventi is None:
        return [], extra
    if hasattr(eventi, "eventi") and hasattr(eventi, "menzioni"):
        extra = list(getattr(eventi, "menzioni") or [])
        return list(getattr(eventi, "eventi") or []), extra
    return list(eventi), extra


def _seed_mentions(sotto: SottoGrafo, menzioni: Iterable[MenzioneRisolta]) -> None:
    for mention in menzioni:
        key = mention.id or f"anon:{len(sotto.menzioni)}"
        esistente = sotto.menzioni.get(key)
        if esistente is not None and esistente is not mention:
            esistente.assorbi(mention)
        else:
            sotto.menzioni[key] = mention


def _canon_id(redirect: dict[str, str], mid: str | None) -> str | None:
    if not mid:
        return mid
    seen: set[str] = set()
    while mid in redirect and mid not in seen:
        seen.add(mid)
        mid = redirect[mid]
    return mid


def _retarget_args(
    eventi: Iterable[EventoRisolto],
    redirect: dict[str, str],
) -> None:
    if not redirect:
        return
    for event in eventi:
        for arg in event.argomenti:
            if arg.menzione_id:
                arg.menzione_id = _canon_id(redirect, arg.menzione_id)


def _drop_redirected(sotto: SottoGrafo, redirect: dict[str, str]) -> None:
    for old_id in list(sotto.menzioni):
        cid = _canon_id(redirect, old_id)
        if cid != old_id:
            sotto.menzioni.pop(old_id, None)


def _referential_id(forma_canonica: str) -> str:
    return content_hash(_normalize_referential(forma_canonica))


def _mentions_in_order(
    eventi: Sequence[EventoRisolto],
    by_id: dict[str, MenzioneRisolta],
) -> list[MenzioneRisolta]:
    ordered: list[MenzioneRisolta] = []
    seen: set[str] = set()
    for event in _sorted_events(eventi):
        for arg in event.argomenti:
            mid = arg.menzione_id
            if not mid or mid in seen:
                continue
            mention = by_id.get(mid)
            if mention is None:
                continue
            seen.add(mid)
            ordered.append(mention)
    for mid, mention in by_id.items():
        if mid not in seen:
            ordered.append(mention)
            seen.add(mid)
    return ordered


def _cluster_referential_forms(
    mentions: Sequence[MenzioneRisolta],
) -> list[list[MenzioneRisolta]]:
    n = len(mentions)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if not _compatibile(mentions[i], mentions[j]):
                continue
            if _names_match(mentions[i].forma_canonica, mentions[j].forma_canonica):
                union(i, j)

    groups: dict[int, list[MenzioneRisolta]] = {}
    for i, mention in enumerate(mentions):
        groups.setdefault(find(i), []).append(mention)
    return list(groups.values())


def _fuse_referential_forms(sotto: SottoGrafo, eventi: Sequence[EventoRisolto]) -> None:
    candidates = [
        m
        for m in _mentions_in_order(eventi, sotto.menzioni)
        if m.tipo_superficiale in _REFERENTIAL_TIPI
    ]
    if not candidates:
        return
    redirect: dict[str, str] = {}
    for cluster in _cluster_referential_forms(candidates):
        for member in cluster:
            pulita = pulisci_forma(member.forma_canonica or member.forma)
            if pulita:
                member.forma = pulita
                member.forma_canonica = pulita
        def _rank(m: MenzioneRisolta) -> tuple[int, int, int]:
            forma = m.forma_canonica or m.forma or ""
            tokens = [tok for tok in forma.split() if tok]
            proper = bool(tokens) and all(tok[:1].isupper() for tok in tokens)
            if proper:
                return (0, -len(tokens), -len(forma))
            return (1, len(tokens), len(forma))

        canonical = min(cluster, key=_rank)
        for member in cluster:
            if member is not canonical:
                canonical.assorbi(member)
        canon_id = _referential_id(canonical.forma_canonica)
        if canonical.id != canon_id:
            redirect[canonical.id] = canon_id
            canonical.id = canon_id
        canonical.non_risolto = False
        canonical.regola = REGOLA
        canonical.versione_regole = RULESET_VERSION
        sotto.menzioni[canon_id] = canonical
        for member in cluster:
            if member.id != canon_id:
                redirect[member.id] = canon_id
    _retarget_args(eventi, redirect)
    _drop_redirected(sotto, redirect)


def _lookup(
    sotto: SottoGrafo,
    mid: str | None,
) -> MenzioneRisolta | None:
    if not mid:
        return None
    return sotto.menzioni.get(mid)


def _antecedenti_finestra(
    event: EventoRisolto,
    all_events: Sequence[EventoRisolto],
    sotto: SottoGrafo,
    *,
    solo_frase_precedente: bool,
    solo_sogg: bool,
) -> list[MenzioneRisolta]:
    if event.frase_indice is None:
        return []
    if solo_frase_precedente:
        window = {event.frase_indice - 1}
    else:
        window = {event.frase_indice, event.frase_indice - 1}
    found: list[MenzioneRisolta] = []
    seen: set[str] = set()
    for other in all_events:
        if other.frase_indice not in window:
            continue
        for arg in other.argomenti:
            if solo_sogg and arg.ruolo != "SOGG":
                continue
            mid = arg.menzione_id
            if not mid or mid in seen:
                continue
            mention = _lookup(sotto, mid)
            if mention is None:
                continue
            if mention.tipo_superficiale not in _ANTECEDENT_TIPI:
                continue
            seen.add(mid)
            found.append(mention)
    return found


def _fuse_to(
    arg: ArgomentoRisolto,
    mention: MenzioneRisolta,
    target: MenzioneRisolta,
    sotto: SottoGrafo,
) -> None:
    arg.menzione_id = target.id
    mention.non_risolto = False
    mention.regola = REGOLA
    mention.versione_regole = RULESET_VERSION
    if mention.id and mention.id != target.id:
        sotto.menzioni.pop(mention.id, None)


def _risolvi_pronomi_e_nulli(
    current: Sequence[EventoRisolto],
    all_events: Sequence[EventoRisolto],
    sotto: SottoGrafo,
) -> None:
    ordered = _sorted_events(current)
    for event in ordered:
        for arg in event.argomenti:
            mention = _lookup(sotto, arg.menzione_id)
            if mention is None:
                continue
            tipo = mention.tipo_superficiale
            if tipo == "pronome":
                ants = [
                    a
                    for a in _antecedenti_finestra(
                        event, all_events, sotto,
                        solo_frase_precedente=False,
                        solo_sogg=False,
                    )
                    if _compatibile(mention, a)
                ]
                if len(ants) == 1:
                    _fuse_to(arg, mention, ants[0], sotto)
                else:
                    mention.non_risolto = True
            elif tipo == "sogg_nullo":
                soggs = [
                    a
                    for a in _antecedenti_finestra(
                        event, all_events, sotto,
                        solo_frase_precedente=True,
                        solo_sogg=True,
                    )
                    if _compatibile(mention, a)
                ]
                if len(soggs) == 1:
                    _fuse_to(arg, mention, soggs[0], sotto)
                else:
                    mention.non_risolto = True


def risolvi_intra(
    eventi: Sequence[EventoRisolto] | Any,
    factsheet: ChunkFactsheet | None,
    sotto: SottoGrafo,
) -> SottoGrafo:
    """Intra-document mention fusion. Mutates ``eventi`` and ``sotto.menzioni``.

    ``factsheet`` is accepted for the sez. 8 signature; rules read mentions
    already minted on arguments plus ``sotto.menzioni``.
    """
    del factsheet
    current, extra = _unpack_eventi(eventi)
    _seed_mentions(sotto, extra)
    visible = _unique_events(sotto.eventi, current)
    _fuse_referential_forms(sotto, visible)
    _risolvi_pronomi_e_nulli(current, visible, sotto)
    return sotto


def _row_id(row: dict[str, Any]) -> str | None:
    value = row.get("id")
    if value is None:
        value = row.get("m.id")
    return str(value) if value is not None else None


def _row_forma(row: dict[str, Any]) -> str:
    value = row.get("forma_canonica")
    if value is None:
        value = row.get("m.forma_canonica")
    return str(value) if value is not None else ""


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item)]
        return [value]
    return []


def _as_riferimenti(value: Any) -> list[RiferimentoMenzione]:
    items: list[Any]
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        items = parsed if isinstance(parsed, list) else []
    elif isinstance(value, list):
        items = value
    else:
        return []
    out: list[RiferimentoMenzione] = []
    for item in items:
        if isinstance(item, dict):
            out.append(
                RiferimentoMenzione(
                    evento_id=str(item.get("evento_id") or ""),
                    summary=str(item.get("summary") or ""),
                )
            )
        elif isinstance(item, str) and item.strip():
            out.append(RiferimentoMenzione(summary=item.strip()))
    return out


def _menzione_da_persistita(mapping: dict[str, Any], mid: str) -> MenzioneRisolta:
    forma = _row_forma(mapping)
    surface = mapping.get("forma") or mapping.get("m.forma") or forma
    eventi = _as_str_list(mapping.get("eventi") or mapping.get("m.eventi"))
    riassunti = _as_str_list(mapping.get("riassunti") or mapping.get("m.riassunti"))
    riferimenti = _as_riferimenti(
        mapping.get("riferimenti") or mapping.get("m.riferimenti")
    )
    if not riferimenti:
        n = max(len(eventi), len(riassunti))
        for i in range(n):
            riferimenti.append(
                RiferimentoMenzione(
                    evento_id=eventi[i] if i < len(eventi) else "",
                    summary=riassunti[i] if i < len(riassunti) else "",
                )
            )
    if riferimenti:
        if not eventi:
            seen_e: list[str] = []
            for item in riferimenti:
                if item.evento_id and item.evento_id not in seen_e:
                    seen_e.append(item.evento_id)
            eventi = seen_e
        if not riassunti:
            seen_s: list[str] = []
            for item in riferimenti:
                if item.summary and item.summary not in seen_s:
                    seen_s.append(item.summary)
            riassunti = seen_s
    occorrenze = mapping.get("occorrenze") or mapping.get("m.occorrenze")
    try:
        occorrenze_i = int(occorrenze) if occorrenze is not None else 0
    except (TypeError, ValueError):
        occorrenze_i = 0
    pulita = pulisci_forma(str(surface or forma)) or str(surface or forma)
    return MenzioneRisolta(
        id=mid,
        forma=pulita,
        forma_canonica=pulita or forma,
        summary=str(mapping.get("summary") or mapping.get("m.summary") or ""),
        riassunti=riassunti,
        eventi=eventi,
        occorrenze=occorrenze_i or len(riferimenti),
        riferimenti=riferimenti,
        tipo_superficiale="sn_comune",
        non_risolto=False,
    )


async def _load_persisted(session: Any) -> list[MenzioneRisolta]:
    raw = session.run(_PERSISTED_QUERY)
    if inspect.isawaitable(raw):
        raw = await raw
    records: list[Any] = []
    data_fn = getattr(raw, "data", None)
    if callable(data_fn):
        fetched = data_fn()
        if inspect.isawaitable(fetched):
            fetched = await fetched
        if fetched:
            records = list(fetched)
    if not records and hasattr(raw, "__aiter__"):
        async for row in raw:
            records.append(row)
    elif not records:
        values_fn = getattr(raw, "values", None)
        if callable(values_fn):
            fetched = values_fn()
            if inspect.isawaitable(fetched):
                fetched = await fetched
            if fetched:
                records = [
                    {"id": row[0], "forma_canonica": row[1]} if isinstance(row, (list, tuple)) else row
                    for row in fetched
                ]
    out: list[MenzioneRisolta] = []
    for row in records:
        mapping = row if isinstance(row, dict) else None
        if mapping is None:
            data_fn = getattr(row, "data", None)
            if callable(data_fn):
                mapping = data_fn()
                if inspect.isawaitable(mapping):
                    mapping = await mapping
        if not mapping:
            try:
                mapping = {"id": row["id"], "forma_canonica": row["forma_canonica"]}
            except Exception:
                continue
        mid = _row_id(mapping)
        if not mid:
            continue
        out.append(_menzione_da_persistita(mapping, mid))
    return out


def _pick_vs_persisted(
    mention: MenzioneRisolta,
    matches: list[MenzioneRisolta],
) -> MenzioneRisolta | None:
    """Reuse a persisted instance (append-only). First name-match wins."""
    if not matches:
        return None
    return matches[0]


async def fondi_referenziali_vs_persistente(session: Any, sotto: SottoGrafo) -> None:
    """Fase B: referential-form string match against persisted ``:Menzione``."""
    persisted = await _load_persisted(session)
    if not persisted:
        return
    redirect: dict[str, str] = {}
    for old_id, mention in list(sotto.menzioni.items()):
        if mention.tipo_superficiale not in _REFERENTIAL_TIPI:
            continue
        matches = [
            item
            for item in persisted
            if _names_match(mention.forma_canonica, item.forma_canonica or item.forma)
        ]
        if not matches:
            continue
        best = _pick_vs_persisted(mention, matches)
        if best is None:
            continue
        pulita = pulisci_forma(mention.forma_canonica or mention.forma)
        if pulita:
            mention.forma = pulita
            mention.forma_canonica = pulita
        mention.assorbi(best)
        mention.non_risolto = False
        mention.regola = REGOLA_PERSISTENTE
        mention.versione_regole = RULESET_VERSION
        if best.id == mention.id:
            continue
        redirect[old_id] = best.id
        mention.id = best.id
        sotto.menzioni.pop(old_id, None)
        esistente = sotto.menzioni.get(best.id)
        if esistente is not None and esistente is not mention:
            esistente.assorbi(mention)
        else:
            sotto.menzioni[best.id] = mention
    _retarget_args(sotto.eventi, redirect)
    _drop_redirected(sotto, redirect)


fondi_nomi_propri_vs_persistente = fondi_referenziali_vs_persistente


__all__ = [
    "REGOLA",
    "REGOLA_PERSISTENTE",
    "fondi_nomi_propri_vs_persistente",
    "fondi_referenziali_vs_persistente",
    "risolvi_intra",
]
