"""MT5 — assign events to leaf ancore (APPARTIENE_A).

Dated events land on the most specific containing leaf via ``tempo_iso.bounds``.
Undated (and dated-but-uncontained) events are asked prima/durante/dopo against
the named ancore of one zona or small window — never the full document.
LLM failure is logged and published; an event the text does not place stays
unassigned (no APPARTIENE_A, no reserve bucket). Sterile ancore (no events in
the subtree) are dropped.

Isolation D6: ``app.pipeline.event_graph.*`` and ``app.models.event_graph``
only. LLM exclusively via ``infra.llm.call_structured``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, NamedTuple

from pydantic import ValidationError

from app.models.event_graph import (
    AncoraTemporaleProposta,
    EventoRisolto,
    LivelloAncoreResult,
    SegnaleAncoraEvento,
)
from app.pipeline.event_graph.ancore_linea import (
    ETICHETTA_MAX_CHAR,
    LineaAncore,
    chiave_ordine_ancora,
    etichetta_normalizzata,
    granularita_effettiva,
)
from app.pipeline.event_graph.ids import ancora_temporale_id
from app.pipeline.event_graph.infra.bus import publish
from app.pipeline.event_graph.infra.llm import call_structured as _call_structured
from app.pipeline.event_graph.tempo_iso import (
    SCALA_CHIAVE,
    analizza,
    bounds,
    rango_granularita,
)
from app.pipeline.event_graph.zona_segmentation import Zona

logger = logging.getLogger(__name__)

STAGE: Final[str] = "collocazione_temporale"
EVENTO: Final[str] = "ancore_smistamento"
EVENTO_FINESTRA: Final[str] = "ancore_smistamento_finestra"

MAX_EVENTI_FINESTRA: Final[int] = 12
MAX_CHAR_CAMPO: Final[int] = 240
_POS_MANCANTE: Final[int] = 10**12
_LARGHEZZA_MANCANTE: Final[int] = 10**18

SYSTEM_SMISTAMENTO = """\
You place events relative to named temporal anchors of ONE window.

Return structured output only. Temperature is 0. Never call tools.

For every event listed, emit one segnale:
- evento_id: copy the id exactly
- ancora: the etichetta of one named ancora from the list below
- posizione: prima | durante | dopo that ancora
- stimato: true when the text does not state the placement
- contemporaneo_a: always empty (not used here)

"durante" means the event happens inside that ancora.
"prima" / "dopo" mean before / after that named ancora, not inside it.
Do not invent ancore. Do not quote or repeat long document text.
"""


class AppartenenzaAncora(NamedTuple):
    """One APPARTIENE_A to persist in MT7: event → leaf etichetta."""

    evento_id: str
    etichetta_foglia: str
    confidenza: float
    stimato: bool
    base: str | None


@dataclass
class SmistamentoAncore:
    """Linea after assignment + sterile prune, plus leaf memberships."""

    linea: LineaAncore = field(default_factory=LineaAncore)
    appartenenze: list[AppartenenzaAncora] = field(default_factory=list)
    n_non_collocati: int = 0
    n_eventi: int = 0
    n_datati: int = 0
    n_llm: int = 0
    n_llm_fail: int = 0
    n_sterili: int = 0


def _testo(valore: object) -> str:
    return valore.strip() if isinstance(valore, str) else ""


def _chiave(ancora: AncoraTemporaleProposta) -> str:
    return etichetta_normalizzata(ancora.etichetta)


def _padre_key(ancora: AncoraTemporaleProposta) -> str:
    return etichetta_normalizzata(ancora.padre)


def _tronca(valore: object, massimo: int = MAX_CHAR_CAMPO) -> str:
    testo = _testo(valore)
    if len(testo) <= massimo:
        return testo
    return testo[:massimo].rstrip() + "…"


def _e_fuso(evento: EventoRisolto) -> bool:
    return bool(_testo(evento.fuso_in))


def _pos_evento(evento: EventoRisolto) -> int:
    if isinstance(evento.posizione_doc, int):
        return evento.posizione_doc
    if isinstance(evento.offset_inizio, int):
        return evento.offset_inizio
    return _POS_MANCANTE


def _pos_ancora(ancora: AncoraTemporaleProposta) -> int:
    if isinstance(ancora.posizione_doc_min, int):
        return ancora.posizione_doc_min
    if isinstance(ancora.offset_inizio, int):
        return ancora.offset_inizio
    return _POS_MANCANTE


def _iso_tempo_assoluto(valore: object) -> str | None:
    if isinstance(valore, str):
        testo = valore.strip()
        return testo if testo and analizza(testo) is not None else None
    if isinstance(valore, dict):
        for key in ("inizio", "canonico", "iso", "tempo", "value"):
            grezzo = valore.get(key)
            if isinstance(grezzo, str) and grezzo.strip() and analizza(grezzo.strip()):
                return grezzo.strip()
    return None


def _istante_evento(evento: EventoRisolto) -> int | None:
    iso = _iso_tempo_assoluto(evento.tempo_assoluto)
    if iso is None:
        return None
    finestra = bounds(iso)
    if finestra is None:
        tempo = analizza(iso)
        if tempo is None:
            return None
        return tempo.secondi * SCALA_CHIAVE
    return finestra[0]


def _finestra(ancora: AncoraTemporaleProposta) -> tuple[int, int] | None:
    inizio = bounds(ancora.inizio, ancora.granularita)
    if inizio is None:
        return None
    fine = bounds(ancora.fine, ancora.granularita)
    return inizio[0], inizio[1] if fine is None else max(inizio[1], fine[1])


def _contiene_istante(ancora: AncoraTemporaleProposta, istante: int) -> bool:
    finestra = _finestra(ancora)
    if finestra is None:
        return False
    return finestra[0] <= istante < finestra[1]


def _larghezza(ancora: AncoraTemporaleProposta) -> int:
    finestra = _finestra(ancora)
    if finestra is None:
        return _LARGHEZZA_MANCANTE
    return finestra[1] - finestra[0]


def _specificita(ancora: AncoraTemporaleProposta) -> tuple:
    rango = rango_granularita(granularita_effettiva(ancora))
    return (
        _larghezza(ancora),
        rango if rango is not None else 99,
        etichetta_normalizzata(ancora.etichetta),
        _pos_ancora(ancora),
    )


def scarta_contenitori_sterili(
    ancore: Sequence[AncoraTemporaleProposta] | LineaAncore | object,
) -> list[AncoraTemporaleProposta]:
    """Drop ancore with no events in the subtree. Clear ``padre`` if dropped."""
    validi = _come_ancore(ancore)
    if not validi:
        return []
    per_etichetta = {_chiave(a): a for a in validi if _chiave(a)}
    vivi = {
        _chiave(a)
        for a in validi
        if _chiave(a) and [eid for eid in (a.eventi or []) if isinstance(eid, str) and eid]
    }
    while True:
        aggiunti = {
            padre
            for etichetta in vivi
            if (padre := _padre_key(per_etichetta[etichetta]))
            and padre in per_etichetta
            and padre not in vivi
        }
        if not aggiunti:
            break
        vivi |= aggiunti

    fuori: list[AncoraTemporaleProposta] = []
    for ancora in validi:
        if _chiave(ancora) not in vivi:
            continue
        padre = _padre_key(ancora)
        if padre and padre not in vivi:
            ancora = ancora.model_copy(update={"padre": None})
        fuori.append(ancora)
    return fuori


def _come_ancore(ancore: object) -> list[AncoraTemporaleProposta]:
    if ancore is None:
        return []
    if isinstance(ancore, LineaAncore):
        return [a for a in ancore.ancore if isinstance(a, AncoraTemporaleProposta)]
    if isinstance(ancore, AncoraTemporaleProposta):
        return [ancore]
    if isinstance(ancore, Sequence) and not isinstance(ancore, (str, bytes)):
        return [item for item in ancore if isinstance(item, AncoraTemporaleProposta)]
    return []


def _come_eventi(eventi: object) -> list[EventoRisolto]:
    if eventi is None:
        return []
    if isinstance(eventi, EventoRisolto):
        return [eventi]
    if isinstance(eventi, Sequence) and not isinstance(eventi, (str, bytes)):
        return [item for item in eventi if isinstance(item, EventoRisolto)]
    return []


def _come_zone(zone: object) -> list[Zona]:
    if zone is None:
        return []
    if isinstance(zone, Zona):
        return [zone]
    if isinstance(zone, Sequence) and not isinstance(zone, (str, bytes)):
        return [item for item in zone if isinstance(item, Zona)]
    return []


def _figli(
    ancore: Sequence[AncoraTemporaleProposta], padre: AncoraTemporaleProposta
) -> list[AncoraTemporaleProposta]:
    chiave = _chiave(padre)
    if not chiave:
        return []
    return [a for a in ancore if _padre_key(a) == chiave]


def _is_leaf(
    ancore: Sequence[AncoraTemporaleProposta], ancora: AncoraTemporaleProposta
) -> bool:
    return not _figli(ancore, ancora)


def _foglie(ancore: Sequence[AncoraTemporaleProposta]) -> list[AncoraTemporaleProposta]:
    return [a for a in ancore if _testo(a.etichetta) and _is_leaf(ancore, a)]


def _discendenti_foglia(
    ancore: Sequence[AncoraTemporaleProposta], radice: AncoraTemporaleProposta
) -> list[AncoraTemporaleProposta]:
    fuori: list[AncoraTemporaleProposta] = []
    pila = list(_figli(ancore, radice))
    visti: set[str] = set()
    while pila:
        corrente = pila.pop()
        chiave = _chiave(corrente)
        if not chiave or chiave in visti:
            continue
        visti.add(chiave)
        kids = _figli(ancore, corrente)
        if not kids:
            fuori.append(corrente)
        else:
            pila.extend(kids)
    return fuori


def _foglia_contenente(
    ancore: Sequence[AncoraTemporaleProposta],
    istante: int,
    evento: EventoRisolto,
) -> AncoraTemporaleProposta | None:
    contenenti = [a for a in ancore if _testo(a.etichetta) and _contiene_istante(a, istante)]
    if not contenenti:
        return None
    foglie_ok = [a for a in contenenti if _is_leaf(ancore, a)]
    if foglie_ok:
        return min(foglie_ok, key=_specificita)
    migliore = min(contenenti, key=_specificita)
    if _is_leaf(ancore, migliore):
        return migliore
    disc = _discendenti_foglia(ancore, migliore)
    disc_ok = [a for a in disc if _contiene_istante(a, istante)]
    if disc_ok:
        return min(disc_ok, key=_specificita)
    if disc:
        pos = _pos_evento(evento)
        return min(
            disc,
            key=lambda a: (
                abs(_pos_ancora(a) - pos),
                etichetta_normalizzata(a.etichetta),
                _pos_ancora(a),
            ),
        )
    return None


def _foglia_piu_vicina(
    foglie: Sequence[AncoraTemporaleProposta], evento: EventoRisolto
) -> AncoraTemporaleProposta | None:
    if not foglie:
        return None
    pos = _pos_evento(evento)
    return min(
        foglie,
        key=lambda a: (
            abs(_pos_ancora(a) - pos),
            etichetta_normalizzata(a.etichetta),
            _pos_ancora(a),
        ),
    )


def _risolvi_nome(
    nome: str, ancore: Sequence[AncoraTemporaleProposta]
) -> AncoraTemporaleProposta | None:
    chiave = etichetta_normalizzata(nome)
    if not chiave:
        return None
    per: dict[str, AncoraTemporaleProposta] = {}
    for ancora in ancore:
        k = _chiave(ancora)
        if k and k not in per:
            per[k] = ancora
        espressione = etichetta_normalizzata(ancora.espressione)
        if espressione and espressione not in per:
            per[espressione] = ancora
    if chiave in per:
        return per[chiave]
    candidati = [
        ancora
        for etichetta, ancora in per.items()
        if etichetta and (etichetta in chiave or chiave in etichetta)
    ]
    visti: list[AncoraTemporaleProposta] = []
    chiavi: set[str] = set()
    for ancora in candidati:
        k = _chiave(ancora)
        if k in chiavi:
            continue
        chiavi.add(k)
        visti.append(ancora)
    if len(visti) == 1:
        return visti[0]
    return None


def _ids_unici(ids: Sequence[str]) -> list[str]:
    visti: set[str] = set()
    fuori: list[str] = []
    for eid in ids:
        if not eid or eid in visti:
            continue
        visti.add(eid)
        fuori.append(eid)
    return fuori


def _etichetta_lato(lato: str, bersaglio: str) -> str:
    if lato == "prima":
        testo = f"prima di {bersaglio}"
    else:
        testo = f"dopo {bersaglio}"
    return testo[:ETICHETTA_MAX_CHAR]


def _etichetta_con_suffisso(base: str, coda: str) -> str:
    extra = f" ({coda.strip()})"
    if len(extra) >= ETICHETTA_MAX_CHAR:
        return extra[:ETICHETTA_MAX_CHAR]
    room = ETICHETTA_MAX_CHAR - len(extra)
    tronco = base[:room].rstrip()
    if not tronco:
        return extra[:ETICHETTA_MAX_CHAR]
    return f"{tronco}{extra}"


def _riserva_etichetta(candidato: str, prese: set[str]) -> str:
    base = candidato[:ETICHETTA_MAX_CHAR]
    nome = base
    contatore = 2
    while etichetta_normalizzata(nome) in prese:
        nome = _etichetta_con_suffisso(base, str(contatore))
        contatore += 1
    prese.add(etichetta_normalizzata(nome))
    return nome


def _fratelli(
    ancore: Sequence[AncoraTemporaleProposta], ancora: AncoraTemporaleProposta
) -> list[AncoraTemporaleProposta]:
    padre = _padre_key(ancora)
    return [a for a in ancore if _padre_key(a) == padre]


def _indice_in(
    gruppo: Sequence[AncoraTemporaleProposta], bersaglio: AncoraTemporaleProposta
) -> int:
    chiave = _chiave(bersaglio)
    for indice, ancora in enumerate(gruppo):
        if _chiave(ancora) == chiave:
            return indice
    return -1


def _secchio_lato(
    gruppo: Sequence[AncoraTemporaleProposta], indice: int, lato: str
) -> AncoraTemporaleProposta | None:
    if lato == "prima":
        if indice <= 0:
            return None
        vicino = gruppo[indice - 1]
        if vicino.natura in {"intervallo", "aperta"}:
            return vicino
        return None
    if indice < 0 or indice + 1 >= len(gruppo):
        return None
    vicino = gruppo[indice + 1]
    if vicino.natura in {"intervallo", "aperta"}:
        return vicino
    return None


def _foglia_da_ancora(
    ancore: Sequence[AncoraTemporaleProposta],
    ancora: AncoraTemporaleProposta,
    evento: EventoRisolto,
) -> AncoraTemporaleProposta:
    if _is_leaf(ancore, ancora):
        return ancora
    disc = _discendenti_foglia(ancore, ancora)
    if not disc:
        return ancora
    istante = _istante_evento(evento)
    if istante is not None:
        contenenti = [a for a in disc if _contiene_istante(a, istante)]
        if contenenti:
            return min(contenenti, key=_specificita)
    vicino = _foglia_piu_vicina(disc, evento)
    return vicino if vicino is not None else disc[0]


def _evento_nella_zona(evento: EventoRisolto, zona: Zona) -> bool:
    start = evento.offset_inizio
    end = evento.offset_fine
    if start is None and end is None:
        if evento.contenuto_di and evento.contenuto_di == zona.id:
            return True
        pos = evento.posizione_doc
        if isinstance(pos, int):
            return zona.offset_inizio <= pos < zona.offset_fine
        return False
    if start is None:
        start = end
    if end is None:
        end = start
    if start is None or end is None:
        return False
    z0, z1 = zona.offset_inizio, zona.offset_fine
    if end == start:
        return z0 <= start < z1
    lo, hi = (start, end) if start <= end else (end, start)
    return lo < z1 and z0 < hi


def _zona_di(evento: EventoRisolto, zone: Sequence[Zona]) -> Zona | None:
    for zona in zone:
        if _evento_nella_zona(evento, zona):
            return zona
    return None


def _ancore_della_finestra(
    ancore: Sequence[AncoraTemporaleProposta],
    zona: Zona | None,
) -> list[AncoraTemporaleProposta]:
    if zona is None:
        return list(ancore)
    z0, z1 = zona.offset_inizio, zona.offset_fine
    scelti: list[AncoraTemporaleProposta] = []
    for ancora in ancore:
        off = ancora.offset_inizio
        pos = ancora.posizione_doc_min
        if isinstance(off, int) and z0 <= off < z1:
            scelti.append(ancora)
        elif isinstance(pos, int) and z0 <= pos < z1:
            scelti.append(ancora)
    if not scelti:
        return list(ancore)
    chiavi = {_chiave(a) for a in scelti}
    per = {_chiave(a): a for a in ancore if _chiave(a)}
    extra: list[AncoraTemporaleProposta] = []
    for ancora in scelti:
        corrente = _padre_key(ancora)
        while corrente and corrente in per and corrente not in chiavi:
            extra.append(per[corrente])
            chiavi.add(corrente)
            corrente = _padre_key(per[corrente])
        for figlio in ancore:
            if _padre_key(figlio) == _chiave(ancora) and _chiave(figlio) not in chiavi:
                extra.append(figlio)
                chiavi.add(_chiave(figlio))
    visti = {_chiave(a) for a in scelti}
    return scelti + [a for a in extra if _chiave(a) not in visti]


def user_smistamento_finestra(
    ancore: Sequence[AncoraTemporaleProposta],
    eventi: Sequence[EventoRisolto],
    *,
    finestra_id: str = "",
) -> str:
    """Prompt for one zona/window: named ancore + event rows, never the document."""
    righe_ancore: list[str] = []
    for ancora in ancore:
        inizio = ancora.inizio or "—"
        padre = ancora.padre or "—"
        righe_ancore.append(
            f"- etichetta={ancora.etichetta} | natura={ancora.natura}"
            f" | inizio={inizio} | padre={padre}"
        )
    blocco_ancore = "\n".join(righe_ancore) or "- (nessuna)"
    righe_eventi: list[str] = []
    for evento in eventi:
        span = _tronca(evento.span or evento.ancora or "")
        lemma = _tronca(evento.lemma or "")
        pos = evento.posizione_doc if evento.posizione_doc is not None else "—"
        righe_eventi.append(
            f"- id={evento.id} | lemma={lemma} | span={span} | posizione_doc={pos}"
        )
    blocco_eventi = "\n".join(righe_eventi) or "- (nessuno)"
    testa = f"finestra_id: {finestra_id}\n" if finestra_id else ""
    return (
        "Colloca gli eventi rispetto alle ancore nominate di QUESTA finestra.\n"
        "Non usare altro testo. Non inventare ancore.\n\n"
        f"{testa}"
        f"ANCORE:\n{blocco_ancore}\n\n"
        f"EVENTI:\n{blocco_eventi}\n"
    )


def _finestre_llm(
    eventi: Sequence[EventoRisolto],
    zone: Sequence[Zona],
    ancore: Sequence[AncoraTemporaleProposta],
) -> list[tuple[str, list[EventoRisolto], list[AncoraTemporaleProposta]]]:
    if not eventi:
        return []
    if zone:
        buckets: dict[str, list[EventoRisolto]] = {}
        zona_di: dict[str, Zona | None] = {}
        ordine_zone = {z.id: i for i, z in enumerate(zone)}
        for evento in eventi:
            zona = _zona_di(evento, zone)
            chiave = zona.id if zona is not None else "_fuori"
            buckets.setdefault(chiave, []).append(evento)
            zona_di[chiave] = zona
        fuori: list[tuple[str, list[EventoRisolto], list[AncoraTemporaleProposta]]] = []
        chiavi = sorted(
            buckets,
            key=lambda k: (0, ordine_zone[k]) if k in ordine_zone else (1, 0),
        )
        for chiave in chiavi:
            zona = zona_di[chiave]
            nominati = _ancore_della_finestra(ancore, zona)
            for chunk in _spezza(buckets[chiave], MAX_EVENTI_FINESTRA):
                fuori.append((chiave, chunk, nominati))
        return fuori
    fuori = []
    for indice, chunk in enumerate(_spezza(list(eventi), MAX_EVENTI_FINESTRA)):
        fuori.append((f"w-{indice}", chunk, list(ancore)))
    return fuori


def _spezza(eventi: list[EventoRisolto], misura: int) -> list[list[EventoRisolto]]:
    if misura < 1:
        return [eventi] if eventi else []
    return [eventi[i : i + misura] for i in range(0, len(eventi), misura)]


def _segnali_da_parsed(parsed: object) -> list[SegnaleAncoraEvento]:
    if parsed is None:
        return []
    if isinstance(parsed, SegnaleAncoraEvento):
        return [parsed]
    if isinstance(parsed, LivelloAncoreResult):
        return list(parsed.segnali)
    if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes)):
        return [item for item in parsed if isinstance(item, SegnaleAncoraEvento)]
    try:
        return list(LivelloAncoreResult.model_validate(parsed).segnali)
    except (ValidationError, TypeError, ValueError):
        return []


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _chiama_finestra(
    user_prompt: str,
    *,
    job_id: str | None,
    llm: Any,
    finestra_id: str,
) -> tuple[list[SegnaleAncoraEvento], bool]:
    try:
        parsed = await _maybe_await(
            llm(
                SYSTEM_SMISTAMENTO,
                user_prompt,
                LivelloAncoreResult,
                temperature=0,
                job_id=job_id,
            )
        )
    except Exception:
        logger.exception(
            "ancore_smistamento finestra=%s llm failed; falling back by posizione_doc",
            finestra_id,
        )
        return [], True
    return _segnali_da_parsed(parsed), False


async def _pubblica(
    job_id: str | None,
    event: str,
    payload: dict[str, Any],
) -> None:
    if not job_id:
        return
    await publish(job_id, STAGE, event, payload)


def _raggruppa(
    ancore: Sequence[AncoraTemporaleProposta],
    ordine: dict[str, float],
) -> dict[str, list[AncoraTemporaleProposta]]:
    gruppi: dict[str, list[AncoraTemporaleProposta]] = {}
    for ancora in ancore:
        gruppi.setdefault(_padre_key(ancora), []).append(ancora)

    def _chiave_ord(ancora: AncoraTemporaleProposta) -> tuple:
        k = _chiave(ancora)
        chiave_t = chiave_ordine_ancora(ancora)
        return (
            ordine.get(k, float(_POS_MANCANTE)),
            chiave_t if chiave_t is not None else 0,
            _pos_ancora(ancora),
            k,
        )

    for chiave, membri in gruppi.items():
        gruppi[chiave] = sorted(membri, key=_chiave_ord)
    return gruppi


def _appiattisci(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
) -> list[AncoraTemporaleProposta]:
    fuori: list[AncoraTemporaleProposta] = []

    def _visita(padre_k: str) -> None:
        for ancora in gruppi.get(padre_k, []):
            fuori.append(ancora)
            _visita(_chiave(ancora))

    _visita("")
    visti = {_chiave(a) for a in fuori}
    restanti = [
        ancora
        for membri in gruppi.values()
        for ancora in membri
        if _chiave(ancora) not in visti
    ]
    restanti.sort(key=lambda a: (_pos_ancora(a), _chiave(a)))
    fuori.extend(restanti)
    return fuori


def _emetti_successione(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
) -> list[tuple[str, str]]:
    fuori: list[tuple[str, str]] = []
    for chiave in sorted(gruppi):
        membri = gruppi[chiave]
        for sinistra, destra in zip(membri, membri[1:], strict=False):
            et_s = _testo(sinistra.etichetta)
            et_d = _testo(destra.etichetta)
            if et_s and et_d:
                fuori.append((et_s, et_d))
    return fuori


def _emetti_ordinali(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
) -> dict[str, int]:
    fuori: dict[str, int] = {}
    for membri in gruppi.values():
        for indice, ancora in enumerate(membri):
            nome = _testo(ancora.etichetta)
            if nome:
                fuori[nome] = indice
    return fuori


def _linea_da_ancore(
    ancore: list[AncoraTemporaleProposta],
    precedente: LineaAncore,
    ordine: dict[str, float],
) -> LineaAncore:
    gruppi = _raggruppa(ancore, ordine)
    ordinati = _appiattisci(gruppi)
    successione = _emetti_successione(gruppi)
    ordinale = _emetti_ordinali(gruppi)
    chiavi = {
        _testo(a.etichetta): chiave
        for a in ordinati
        if _testo(a.etichetta) and (chiave := chiave_ordine_ancora(a)) is not None
    }
    return LineaAncore(
        ancore=ordinati,
        chiave_ordine=chiavi,
        ordinale=ordinale,
        successione=successione,
        n_in=precedente.n_in,
        n_intervalli=sum(1 for a in ordinati if a.natura == "intervallo"),
        n_aperte=sum(1 for a in ordinati if a.natura == "aperta"),
        n_successione=len(successione),
    )


@dataclass
class _Stato:
    ancore: list[AncoraTemporaleProposta]
    ordine: dict[str, float]
    ids_per_foglia: dict[str, list[str]] = field(default_factory=dict)

    def per_etichetta(self) -> dict[str, AncoraTemporaleProposta]:
        return {_chiave(a): a for a in self.ancore if _chiave(a)}

    def sostituisci(self, nuova: AncoraTemporaleProposta) -> None:
        chiave = _chiave(nuova)
        for indice, ancora in enumerate(self.ancore):
            if _chiave(ancora) == chiave:
                self.ancore[indice] = nuova
                return
        self.ancore.append(nuova)

    def aggiungi(
        self,
        nuova: AncoraTemporaleProposta,
        *,
        ordine: float,
    ) -> AncoraTemporaleProposta:
        self.ancore.append(nuova)
        self.ordine[_chiave(nuova)] = ordine
        return nuova


def _crea_aperta(
    stato: _Stato,
    bersaglio: AncoraTemporaleProposta,
    lato: str,
    *,
    documento: str,
) -> AncoraTemporaleProposta:
    prese = {_chiave(a) for a in stato.ancore if _chiave(a)}
    etichetta = _riserva_etichetta(
        _etichetta_lato(lato, _testo(bersaglio.etichetta)),
        prese,
    )
    chiave_id = f"{lato}|{etichetta_normalizzata(bersaglio.etichetta)}"
    ancora_temporale_id(
        documento,
        "aperta",
        "relativa",
        inizio=None,
        fine=None,
        chiave=chiave_id,
    )
    aperta = AncoraTemporaleProposta(
        etichetta=etichetta,
        natura="aperta",
        tipo="relativa",
        stimato=True,
        padre=bersaglio.padre,
        espressione=chiave_id,
        posizione_doc_min=bersaglio.posizione_doc_min,
    )
    base_ordine = stato.ordine.get(_chiave(bersaglio), 0.0)
    delta = -0.5 if lato == "prima" else 0.5
    return stato.aggiungi(aperta, ordine=base_ordine + delta)


def _secchio_prima_dopo(
    stato: _Stato,
    bersaglio: AncoraTemporaleProposta,
    lato: str,
    evento: EventoRisolto,
    *,
    documento: str,
) -> AncoraTemporaleProposta:
    gruppi = _raggruppa(stato.ancore, stato.ordine)
    gruppo = gruppi.get(_padre_key(bersaglio), [])
    indice = _indice_in(gruppo, bersaglio)
    esistente = _secchio_lato(gruppo, indice, lato)
    if esistente is not None:
        return _foglia_da_ancora(stato.ancore, esistente, evento)
    return _foglia_da_ancora(
        stato.ancore,
        _crea_aperta(stato, bersaglio, lato, documento=documento),
        evento,
    )


def _applica_segnale(
    stato: _Stato,
    segnale: SegnaleAncoraEvento,
    evento: EventoRisolto,
    *,
    documento: str,
) -> AncoraTemporaleProposta | None:
    if segnale.posizione not in {"prima", "durante", "dopo"}:
        return None
    bersaglio = _risolvi_nome(segnale.ancora or "", stato.ancore)
    if bersaglio is None:
        return None
    if segnale.posizione == "durante":
        return _foglia_da_ancora(stato.ancore, bersaglio, evento)
    return _secchio_prima_dopo(
        stato, bersaglio, segnale.posizione, evento, documento=documento
    )


def _assegna(
    stato: _Stato,
    evento: EventoRisolto,
    foglia: AncoraTemporaleProposta,
    *,
    confidenza: float,
    stimato: bool,
    base: str | None,
) -> AppartenenzaAncora:
    etichetta = _testo(foglia.etichetta)
    stato.ids_per_foglia.setdefault(_chiave(foglia), []).append(evento.id)
    return AppartenenzaAncora(
        evento_id=evento.id,
        etichetta_foglia=etichetta,
        confidenza=confidenza,
        stimato=stimato,
        base=base,
    )


def _scrivi_eventi(stato: _Stato) -> None:
    ids_map = {k: _ids_unici(v) for k, v in stato.ids_per_foglia.items()}
    foglie = {_chiave(a) for a in _foglie(stato.ancore)}
    nuovi: list[AncoraTemporaleProposta] = []
    for ancora in stato.ancore:
        chiave = _chiave(ancora)
        if chiave in foglie:
            ids = ids_map.get(chiave, [])
            nuovi.append(ancora.model_copy(update={"eventi": ids}))
        else:
            nuovi.append(ancora.model_copy(update={"eventi": []}))
    stato.ancore = nuovi


async def smista_eventi(
    linea: LineaAncore | Sequence[AncoraTemporaleProposta] | object,
    eventi: Sequence[EventoRisolto] | None,
    *,
    zone: Sequence[Zona] | None = None,
    job_id: str | None = None,
    call_structured: Any = None,
    documento: str = "",
) -> SmistamentoAncore:
    """Assign placeable non-fused events to a leaf. Unplaced events stay free."""
    llm = call_structured if call_structured is not None else _call_structured
    if isinstance(linea, LineaAncore):
        linea_in = linea
        ancore0 = list(linea.ancore)
    else:
        ancore0 = _come_ancore(linea)
        linea_in = LineaAncore(ancore=ancore0)
    items = [e for e in _come_eventi(eventi) if not _e_fuso(e)]
    zone_ok = _come_zone(zone)
    doc = documento if isinstance(documento, str) else ""
    if not doc:
        for evento in items:
            if evento.documento:
                doc = evento.documento
                break

    stato = _Stato(
        ancore=list(ancore0),
        ordine={_chiave(a): float(i) for i, a in enumerate(ancore0) if _chiave(a)},
    )
    appartenenze: list[AppartenenzaAncora] = []
    collocati: set[str] = set()
    n_datati = 0
    n_llm = 0
    n_llm_fail = 0

    restanti: list[EventoRisolto] = []
    for evento in items:
        if not evento.id:
            restanti.append(evento)
            continue
        istante = _istante_evento(evento)
        if istante is None:
            restanti.append(evento)
            continue
        foglia = _foglia_contenente(stato.ancore, istante, evento)
        if foglia is None:
            restanti.append(evento)
            continue
        n_datati += 1
        appartenenze.append(
            _assegna(
                stato,
                evento,
                foglia,
                confidenza=1.0,
                stimato=False,
                base="tempo_assoluto",
            )
        )
        collocati.add(evento.id)

    da_llm = [e for e in restanti if e.id and e.id not in collocati]
    n_llm = len(da_llm)
    segnali_per_id: dict[str, SegnaleAncoraEvento] = {}
    for finestra_id, chunk, nominati in _finestre_llm(da_llm, zone_ok, stato.ancore):
        prompt = user_smistamento_finestra(
            nominati, chunk, finestra_id=str(finestra_id)
        )
        segnali, failed = await _chiama_finestra(
            prompt, job_id=job_id, llm=llm, finestra_id=str(finestra_id)
        )
        if failed:
            n_llm_fail += 1
            await _pubblica(
                job_id,
                EVENTO_FINESTRA,
                {
                    "finestra_id": finestra_id,
                    "n_eventi": len(chunk),
                    "failed": True,
                },
            )
            continue
        by_id = {s.evento_id: s for s in segnali if s.evento_id}
        for evento in chunk:
            if evento.id in by_id:
                segnali_per_id[evento.id] = by_id[evento.id]
        await _pubblica(
            job_id,
            EVENTO_FINESTRA,
            {
                "finestra_id": finestra_id,
                "n_eventi": len(chunk),
                "n_segnali": len(segnali),
                "failed": False,
            },
        )

    for evento in da_llm:
        if evento.id in collocati:
            continue
        segnale = segnali_per_id.get(evento.id)
        foglia: AncoraTemporaleProposta | None = None
        confidenza = 0.5
        stimato = True
        base = "llm"
        if segnale is not None:
            foglia = _applica_segnale(stato, segnale, evento, documento=doc)
            if foglia is not None:
                confidenza = segnale.confidenza
                stimato = bool(segnale.stimato)
                base = segnale.base or "llm"
        if foglia is None:
            continue
        appartenenze.append(
            _assegna(
                stato,
                evento,
                foglia,
                confidenza=confidenza,
                stimato=stimato,
                base=base,
            )
        )
        collocati.add(evento.id)

    _scrivi_eventi(stato)
    prima_potatura = len(stato.ancore)
    potate = scarta_contenitori_sterili(stato.ancore)
    n_sterili = prima_potatura - len(potate)
    stato.ancore = potate
    vivi = {_chiave(a) for a in stato.ancore if _chiave(a)}
    linea_out = _linea_da_ancore(stato.ancore, linea_in, stato.ordine)
    foglie_vivi = {_testo(a.etichetta) for a in _foglie(linea_out.ancore)}
    appartenenze_ok = [
        item
        for item in appartenenze
        if etichetta_normalizzata(item.etichetta_foglia) in vivi
        and item.etichetta_foglia in foglie_vivi
    ]
    collocati_ok = {item.evento_id for item in appartenenze_ok}
    n_non_collocati = sum(1 for e in items if e.id and e.id not in collocati_ok)
    esito = SmistamentoAncore(
        linea=linea_out,
        appartenenze=appartenenze_ok,
        n_non_collocati=n_non_collocati,
        n_eventi=len(items),
        n_datati=n_datati,
        n_llm=n_llm,
        n_llm_fail=n_llm_fail,
        n_sterili=n_sterili,
    )
    await _pubblica(
        job_id,
        EVENTO,
        {
            "n_eventi": esito.n_eventi,
            "n_datati": esito.n_datati,
            "n_llm": esito.n_llm,
            "n_llm_fail": esito.n_llm_fail,
            "n_non_collocati": esito.n_non_collocati,
            "n_sterili": esito.n_sterili,
        },
    )
    return esito


esegui_ancore_smistamento = smista_eventi


__all__ = [
    "EVENTO",
    "EVENTO_FINESTRA",
    "MAX_EVENTI_FINESTRA",
    "STAGE",
    "SYSTEM_SMISTAMENTO",
    "AppartenenzaAncora",
    "SmistamentoAncore",
    "esegui_ancore_smistamento",
    "scarta_contenitori_sterili",
    "smista_eventi",
    "user_smistamento_finestra",
]
