"""MT4 — total order, CONTIENE forest, implicit intervals, open anchors.

Pure functions. No LLM, no Neo4j, no I/O in the core path. Isolation D6:
``app.pipeline.event_graph.*`` and ``app.models.event_graph`` only. Malformed
input is skipped (empty result), never swallowed with a bare ``except Exception``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from app.models.event_graph import (
    AncoraTemporaleProposta,
    LivelloAncoreResult,
    NaturaAncora,
    SegnaleAncoraEvento,
    TipoAncora,
)
from app.pipeline.event_graph.ids import ancora_temporale_id
from app.pipeline.event_graph.infra.bus import publish
from app.pipeline.event_graph.tempo_iso import (
    SCALA_CHIAVE,
    analizza,
    bounds,
    chiave_ordine,
    piu_grossa,
    rango_granularita,
)

ETICHETTA_MAX_CHAR: Final[int] = 40
STAGE: Final[str] = "collocazione_temporale"
EVENTO: Final[str] = "ancore_linea"

_POS_MANCANTE: Final[int] = 10**12
_SECONDI_AL_GIORNO: Final[int] = 86_400

_PRIMA = re.compile(
    r"^\s*(?:prima\s+(?:di|del|dello|della|dell['’]|lo|la|il|l['’])\s+|"
    r"before\s+(?:the\s+)?)",
    re.IGNORECASE,
)
_DOPO = re.compile(
    r"^\s*(?:dopo\s+(?:di|il|lo|la|l['’]|del|dello|della|dell['’])?\s*|"
    r"after\s+(?:the\s+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LineaAncore:
    """Timeline built from explicit ancore: forest + intervals + open leaves."""

    ancore: list[AncoraTemporaleProposta] = field(default_factory=list)
    chiave_ordine: dict[str, int] = field(default_factory=dict)
    ordinale: dict[str, int] = field(default_factory=dict)
    successione: list[tuple[str, str]] = field(default_factory=list)
    n_in: int = 0
    n_intervalli: int = 0
    n_aperte: int = 0
    n_successione: int = 0


def etichetta_normalizzata(valore: object) -> str:
    """Etichetta confrontabile: spazi collassati, maiuscole appiattite."""
    if not isinstance(valore, str):
        return ""
    return " ".join(valore.split()).casefold()


def chiave_ordine_ancora(ancora: AncoraTemporaleProposta) -> int | None:
    """``tempo_iso.chiave_ordine`` of the ancora, or None if undated."""
    if not isinstance(ancora, AncoraTemporaleProposta):
        return None
    return chiave_ordine(ancora.inizio, ancora.granularita)


def granularita_effettiva(ancora: AncoraTemporaleProposta) -> str | None:
    """Granularity of the occupied window: max(string precision, declared)."""
    if not isinstance(ancora, AncoraTemporaleProposta):
        return None
    tempo = analizza(ancora.inizio)
    if tempo is not None:
        return piu_grossa(tempo.precisione, ancora.granularita) or tempo.precisione
    if rango_granularita(ancora.granularita) is None:
        return None
    return ancora.granularita


def costruisci_linea(
    ancore: list[AncoraTemporaleProposta] | LivelloAncoreResult | object,
    *,
    documento: str = "",
    segnali: list[SegnaleAncoraEvento] | None = None,
) -> LineaAncore:
    """Build the timeline forest, implicit intervals, and open anchors. Sync/pure."""
    return _esito_linea(ancore, documento=documento, segnali=segnali)


async def esegui_ancore_linea(
    ancore: list[AncoraTemporaleProposta] | LivelloAncoreResult | object,
    *,
    documento: str = "",
    segnali: list[SegnaleAncoraEvento] | None = None,
    job_id: str | None = None,
) -> LineaAncore:
    """Same as ``costruisci_linea``; publishes a summary when ``job_id`` is set."""
    esito = _esito_linea(ancore, documento=documento, segnali=segnali)
    if job_id:
        await publish(
            job_id,
            STAGE,
            EVENTO,
            {
                "n_in": esito.n_in,
                "n_intervalli": esito.n_intervalli,
                "n_aperte": esito.n_aperte,
                "n_successione": esito.n_successione,
                "documento": documento if isinstance(documento, str) else "",
            },
        )
    return esito


def violazioni_foresta(ancore: object) -> list[str]:
    """Independent forest check: ≤1 parent, no cycles, coarser nested parent."""
    validi = [a for a in _come_lista(ancore) if _testo(a.etichetta)]
    fuori: list[str] = []
    per_etichetta: dict[str, AncoraTemporaleProposta] = {}
    for ancora in validi:
        etichetta = etichetta_normalizzata(ancora.etichetta)
        if etichetta in per_etichetta:
            fuori.append(f"etichetta duplicata: {etichetta}")
            continue
        per_etichetta[etichetta] = ancora

    for etichetta, ancora in per_etichetta.items():
        padre_nome = etichetta_normalizzata(ancora.padre)
        if not padre_nome:
            continue
        if padre_nome == etichetta:
            fuori.append(f"padre di se stesso: {etichetta}")
            continue
        padre = per_etichetta.get(padre_nome)
        if padre is None:
            fuori.append(f"padre irrisolvibile: {etichetta} -> {padre_nome}")
            continue
        if not _granularita_ammessa(padre, ancora):
            fuori.append(f"granularita non decrescente: {padre_nome} -> {etichetta}")
        if not _finestre_annidate(padre, ancora):
            fuori.append(f"finestre non annidate: {padre_nome} -> {etichetta}")

    for etichetta in per_etichetta:
        visti: set[str] = {etichetta}
        corrente = etichetta_normalizzata(per_etichetta[etichetta].padre)
        while corrente and corrente in per_etichetta:
            if corrente in visti:
                fuori.append(f"ciclo su: {etichetta}")
                break
            visti.add(corrente)
            corrente = etichetta_normalizzata(per_etichetta[corrente].padre)
    return sorted(set(fuori))


def _esito_linea(
    ancore: object,
    *,
    documento: object,
    segnali: object,
) -> LineaAncore:
    doc = documento if isinstance(documento, str) else ""
    valide = _come_lista(ancore)
    n_in = len(valide)
    segnali_ok = _come_segnali(ancore, segnali)
    if not valide:
        return LineaAncore(n_in=n_in)

    univoche = _etichette_univoche(valide)
    foresta = _costruisci_foresta(univoche)
    gruppi = _raggruppa(foresta)
    prese = {etichetta_normalizzata(a.etichetta) for a in foresta}
    n_intervalli = _inserisci_intervalli(gruppi, prese, documento=doc)
    n_aperte = _inserisci_aperte(gruppi, prese, segnali_ok, documento=doc)

    ancore_out = _appiattisci(gruppi)
    successione = _emetti_successione(gruppi)
    ordinale = _emetti_ordinali(gruppi)
    chiavi = {
        _testo(a.etichetta): chiave
        for a in ancore_out
        if _testo(a.etichetta) and (chiave := chiave_ordine_ancora(a)) is not None
    }
    return LineaAncore(
        ancore=ancore_out,
        chiave_ordine=chiavi,
        ordinale=ordinale,
        successione=successione,
        n_in=n_in,
        n_intervalli=n_intervalli,
        n_aperte=n_aperte,
        n_successione=len(successione),
    )


def _come_lista(ancore: object) -> list[AncoraTemporaleProposta]:
    if ancore is None:
        return []
    if isinstance(ancore, AncoraTemporaleProposta):
        return [ancore]
    if isinstance(ancore, LivelloAncoreResult):
        return list(ancore.ancore)
    if isinstance(ancore, Sequence) and not isinstance(ancore, (str, bytes)):
        return [item for item in ancore if isinstance(item, AncoraTemporaleProposta)]
    return []


def _come_segnali(ancore: object, segnali: object) -> list[SegnaleAncoraEvento]:
    if segnali is None and isinstance(ancore, LivelloAncoreResult):
        segnali = ancore.segnali
    if segnali is None:
        return []
    if isinstance(segnali, SegnaleAncoraEvento):
        return [segnali]
    if isinstance(segnali, Sequence) and not isinstance(segnali, (str, bytes)):
        return [item for item in segnali if isinstance(item, SegnaleAncoraEvento)]
    return []


def _testo(valore: object) -> str:
    return valore.strip() if isinstance(valore, str) else ""


def _ordinamento(ancora: AncoraTemporaleProposta, indice: int = 0) -> tuple:
    ordine = chiave_ordine_ancora(ancora)
    pos = ancora.posizione_doc_min if isinstance(ancora.posizione_doc_min, int) else _POS_MANCANTE
    off = ancora.offset_inizio if isinstance(ancora.offset_inizio, int) else _POS_MANCANTE
    etichetta = etichetta_normalizzata(ancora.etichetta)
    if ordine is None:
        return (1, 0, pos, off, etichetta, indice)
    return (0, ordine, pos, off, etichetta, indice)


def _etichetta_base(ancora: AncoraTemporaleProposta) -> str:
    base = _testo(ancora.etichetta) or _testo(ancora.espressione)
    if base:
        return base
    tempo = analizza(ancora.inizio)
    if tempo is not None:
        return tempo.canonico
    return "ancora"


def _etichetta_con_suffisso(base: str, coda: str) -> str:
    extra = f" ({coda.strip()})"
    if len(extra) >= ETICHETTA_MAX_CHAR:
        return extra[:ETICHETTA_MAX_CHAR]
    room = ETICHETTA_MAX_CHAR - len(extra)
    tronco = base[:room].rstrip()
    if not tronco:
        return extra[:ETICHETTA_MAX_CHAR]
    return f"{tronco}{extra}"


def _etichette_univoche(
    ancore: list[AncoraTemporaleProposta],
) -> list[AncoraTemporaleProposta]:
    if not ancore:
        return []
    ordine = sorted(range(len(ancore)), key=lambda i: _ordinamento(ancore[i], i))
    prese: set[str] = set()
    nuovi: dict[int, str] = {}
    vecchi: dict[int, str] = {}
    for posizione, indice in enumerate(ordine, start=1):
        ancora = ancore[indice]
        base = _etichetta_base(ancora)
        vecchi[indice] = ancora.etichetta
        candidato = base[:ETICHETTA_MAX_CHAR]
        if etichetta_normalizzata(candidato) in prese:
            tempo = analizza(ancora.inizio)
            coda = tempo.canonico if tempo is not None else str(posizione)
            candidato = _etichetta_con_suffisso(base, coda)
        contatore = 2
        while etichetta_normalizzata(candidato) in prese:
            candidato = _etichetta_con_suffisso(base, str(contatore))
            contatore += 1
        prese.add(etichetta_normalizzata(candidato))
        nuovi[indice] = candidato

    old_to_idx: dict[str, list[int]] = {}
    for indice, vecchia in vecchi.items():
        chiave = etichetta_normalizzata(vecchia)
        if chiave:
            old_to_idx.setdefault(chiave, []).append(indice)
    new_by_norm = {etichetta_normalizzata(nome): nome for nome in nuovi.values()}
    new_norms = set(new_by_norm)

    fuori: list[AncoraTemporaleProposta] = []
    for indice, ancora in enumerate(ancore):
        padre = ancora.padre
        nuovo_padre = padre
        if padre:
            pk = etichetta_normalizzata(padre)
            if pk in new_by_norm:
                nuovo_padre = new_by_norm[pk]
            elif pk in old_to_idx and len(old_to_idx[pk]) == 1:
                nuovo_padre = nuovi[old_to_idx[pk][0]]
            if etichetta_normalizzata(nuovo_padre) not in new_norms:
                nuovo_padre = None
            if etichetta_normalizzata(nuovo_padre) == etichetta_normalizzata(
                nuovi[indice]
            ):
                nuovo_padre = None
        fuori.append(
            ancora.model_copy(update={"etichetta": nuovi[indice], "padre": nuovo_padre})
        )
    return fuori


def _finestra(ancora: AncoraTemporaleProposta) -> tuple[int, int] | None:
    inizio = bounds(ancora.inizio, ancora.granularita)
    if inizio is None:
        return None
    fine = bounds(ancora.fine, ancora.granularita)
    return inizio[0], inizio[1] if fine is None else max(inizio[1], fine[1])


def _granularita_ammessa(
    padre: AncoraTemporaleProposta, figlio: AncoraTemporaleProposta
) -> bool:
    rango_padre = rango_granularita(granularita_effettiva(padre))
    rango_figlio = rango_granularita(granularita_effettiva(figlio))
    if rango_padre is None or rango_figlio is None:
        return True
    return rango_padre > rango_figlio


def _finestre_annidate(
    padre: AncoraTemporaleProposta, figlio: AncoraTemporaleProposta
) -> bool:
    finestra_padre = _finestra(padre)
    finestra_figlio = _finestra(figlio)
    if finestra_padre is None or finestra_figlio is None:
        return True
    return (
        finestra_padre[0] <= finestra_figlio[0]
        and finestra_figlio[1] <= finestra_padre[1]
    )


def _finestre_strettamente_annidate(
    padre: AncoraTemporaleProposta, figlio: AncoraTemporaleProposta
) -> bool:
    finestra_padre = _finestra(padre)
    finestra_figlio = _finestra(figlio)
    if finestra_padre is None or finestra_figlio is None:
        return False
    if not (
        finestra_padre[0] <= finestra_figlio[0]
        and finestra_figlio[1] <= finestra_padre[1]
    ):
        return False
    return finestra_padre != finestra_figlio


def _chiude_ciclo(padre_di: dict[str, str], figlio: str, padre: str) -> bool:
    corrente: str | None = padre
    for _ in range(len(padre_di) + 2):
        if corrente is None:
            return False
        if corrente == figlio:
            return True
        corrente = padre_di.get(corrente)
    return True


def _chiave_nodo(ancora: AncoraTemporaleProposta) -> str:
    return etichetta_normalizzata(ancora.etichetta)


def _mappa_etichette(
    ancore: list[AncoraTemporaleProposta],
) -> dict[str, AncoraTemporaleProposta | None]:
    fuori: dict[str, AncoraTemporaleProposta | None] = {}
    for ancora in ancore:
        etichetta = _chiave_nodo(ancora)
        if not etichetta:
            continue
        if etichetta in fuori and fuori[etichetta] is not ancora:
            fuori[etichetta] = None
            continue
        fuori[etichetta] = ancora
    return fuori


def _arco_valido(
    padre: AncoraTemporaleProposta,
    figlio: AncoraTemporaleProposta,
    *,
    stretto: bool,
) -> bool:
    if _chiave_nodo(padre) == _chiave_nodo(figlio):
        return False
    if not _granularita_ammessa(padre, figlio):
        return False
    if stretto:
        rango_padre = rango_granularita(granularita_effettiva(padre))
        rango_figlio = rango_granularita(granularita_effettiva(figlio))
        if rango_padre is None or rango_figlio is None:
            return False
        return _finestre_strettamente_annidate(padre, figlio)
    return _finestre_annidate(padre, figlio)


def _punteggio_contenitore(padre: AncoraTemporaleProposta) -> tuple[int, int, str]:
    finestra = _finestra(padre)
    larghezza = (finestra[1] - finestra[0]) if finestra is not None else 10**18
    rango = rango_granularita(granularita_effettiva(padre))
    return (larghezza, rango if rango is not None else 99, _chiave_nodo(padre))


def _costruisci_foresta(
    ancore: list[AncoraTemporaleProposta],
) -> list[AncoraTemporaleProposta]:
    validi = [a for a in ancore if _testo(a.etichetta)]
    if not validi:
        return []
    per_etichetta = _mappa_etichette(validi)
    ordinati = sorted(validi, key=lambda a: _ordinamento(a))
    padre_di: dict[str, str] = {}

    for ancora in ordinati:
        figlio_k = _chiave_nodo(ancora)
        riferimento = etichetta_normalizzata(ancora.padre)
        if not figlio_k or not riferimento:
            continue
        padre = per_etichetta.get(riferimento)
        if padre is None:
            continue
        padre_k = _chiave_nodo(padre)
        if not _arco_valido(padre, ancora, stretto=False):
            continue
        if _chiude_ciclo(padre_di, figlio_k, padre_k):
            continue
        padre_di[figlio_k] = padre_k

    for ancora in ordinati:
        figlio_k = _chiave_nodo(ancora)
        if not figlio_k or figlio_k in padre_di:
            continue
        candidati: list[AncoraTemporaleProposta] = []
        for padre in ordinati:
            if _chiave_nodo(padre) == figlio_k:
                continue
            if not _arco_valido(padre, ancora, stretto=True):
                continue
            padre_k = _chiave_nodo(padre)
            if _chiude_ciclo(padre_di, figlio_k, padre_k):
                continue
            candidati.append(padre)
        if not candidati:
            continue
        scelto = min(candidati, key=_punteggio_contenitore)
        padre_di[figlio_k] = _chiave_nodo(scelto)

    nomi = {_chiave_nodo(a): _testo(a.etichetta) for a in ordinati}
    fuori: list[AncoraTemporaleProposta] = []
    for ancora in ordinati:
        figlio_k = _chiave_nodo(ancora)
        padre_k = padre_di.get(figlio_k)
        fuori.append(
            ancora.model_copy(
                update={"padre": None if padre_k is None else nomi.get(padre_k)}
            )
        )
    return fuori


def _padre_key(ancora: AncoraTemporaleProposta) -> str:
    return etichetta_normalizzata(ancora.padre)


def _raggruppa(
    ancore: list[AncoraTemporaleProposta],
) -> dict[str, list[AncoraTemporaleProposta]]:
    gruppi: dict[str, list[AncoraTemporaleProposta]] = {}
    for ancora in ancore:
        gruppi.setdefault(_padre_key(ancora), []).append(ancora)
    for chiave, membri in gruppi.items():
        gruppi[chiave] = sorted(membri, key=lambda a: _ordinamento(a))
    return gruppi


def _riserva_etichetta(candidato: str, prese: set[str]) -> str:
    base = candidato[:ETICHETTA_MAX_CHAR]
    nome = base
    contatore = 2
    while etichetta_normalizzata(nome) in prese:
        nome = _etichetta_con_suffisso(base, str(contatore))
        contatore += 1
    prese.add(etichetta_normalizzata(nome))
    return nome


def _etichetta_fra(sinistra: str, destra: str) -> str:
    testo = f"fra {sinistra} e {destra}"
    if len(testo) <= ETICHETTA_MAX_CHAR:
        return testo
    budget = ETICHETTA_MAX_CHAR - len("fra  e ")
    if budget < 2:
        return testo[:ETICHETTA_MAX_CHAR]
    left = max(1, budget // 2)
    right = max(1, budget - left)
    return f"fra {sinistra[:left].rstrip()} e {destra[:right].rstrip()}"


def _etichetta_lato(lato: str, bersaglio: str) -> str:
    if lato == "prima":
        testo = f"prima di {bersaglio}"
    else:
        testo = f"dopo {bersaglio}"
    return testo[:ETICHETTA_MAX_CHAR]


def _iso_da_secondi(secondi: int) -> str | None:
    if secondi < 0:
        return None
    giorni, resto = divmod(secondi, _SECONDI_AL_GIORNO)
    try:
        giorno = date.fromordinal(giorni + 1)
    except (ValueError, OverflowError):
        return None
    if not 1 <= giorno.year <= 9999:
        return None
    ora, resto = divmod(resto, 3600)
    minuto, secondo = divmod(resto, 60)
    if secondo:
        return (
            f"{giorno.year:04d}-{giorno.month:02d}-{giorno.day:02d}"
            f"T{ora:02d}:{minuto:02d}:{secondo:02d}"
        )
    if minuto:
        return (
            f"{giorno.year:04d}-{giorno.month:02d}-{giorno.day:02d}T{ora:02d}:{minuto:02d}"
        )
    if ora:
        return f"{giorno.year:04d}-{giorno.month:02d}-{giorno.day:02d}T{ora:02d}"
    return f"{giorno.year:04d}-{giorno.month:02d}-{giorno.day:02d}"


def _iso_da_chiave(chiave: int) -> str | None:
    return _iso_da_secondi(chiave // SCALA_CHIAVE)


def _campi_gap(lo: int, hi: int) -> tuple[str, str | None, str] | None:
    if hi <= lo:
        return None
    inizio = _iso_da_chiave(lo)
    if inizio is None:
        return None
    durata = (hi - lo) // SCALA_CHIAVE
    if durata <= 0:
        return None
    ultimo = _iso_da_chiave(hi - SCALA_CHIAVE)
    if durata % _SECONDI_AL_GIORNO == 0 and "T" not in inizio:
        giorni = durata // _SECONDI_AL_GIORNO
        if giorni == 1:
            return inizio, None, "giorno"
        if ultimo is None:
            return None
        return inizio, ultimo.split("T", 1)[0], "giorno"
    if durata % 3600 == 0 and inizio.count(":") == 0:
        ore = durata // 3600
        if ore == 1:
            return inizio, None, "ora"
        if ultimo is None:
            return None
        return inizio, ultimo, "ora"
    if ultimo is None:
        return None
    if ultimo == inizio:
        return inizio, None, "secondo"
    return inizio, ultimo, "secondo"


def _chiave_collocazione_iso(ancora: AncoraTemporaleProposta) -> str:
    tempo = analizza(ancora.inizio)
    if tempo is not None:
        return tempo.canonico
    return etichetta_normalizzata(ancora.etichetta)


def _id_sintetica(
    *,
    documento: str,
    natura: NaturaAncora,
    tipo: TipoAncora,
    inizio: str | None,
    fine: str | None,
    chiave: str | None,
) -> str:
    return ancora_temporale_id(
        documento,
        natura,
        tipo,
        inizio=inizio,
        fine=fine,
        chiave=chiave,
    )


def _inserisci_intervalli(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
    prese: set[str],
    *,
    documento: str,
) -> int:
    creati = 0
    for chiave, membri in list(gruppi.items()):
        nuovi: list[AncoraTemporaleProposta] = []
        for indice, ancora in enumerate(membri):
            nuovi.append(ancora)
            if indice + 1 >= len(membri):
                continue
            successiva = membri[indice + 1]
            if ancora.natura != "esplicita" or successiva.natura != "esplicita":
                continue
            intervallo = _intervallo_fra(ancora, successiva, prese, documento=documento)
            if intervallo is None:
                continue
            nuovi.append(intervallo)
            creati += 1
        gruppi[chiave] = nuovi
    return creati


def _intervallo_fra(
    prima: AncoraTemporaleProposta,
    seconda: AncoraTemporaleProposta,
    prese: set[str],
    *,
    documento: str,
) -> AncoraTemporaleProposta | None:
    finestra_a = _finestra(prima)
    finestra_b = _finestra(seconda)
    if finestra_a is None or finestra_b is None:
        return None
    if finestra_a[0] >= finestra_b[0]:
        return None
    if finestra_a[1] > finestra_b[0]:
        return None
    inizio: str | None = None
    fine: str | None = None
    granularita: str | None = None
    if finestra_a[1] < finestra_b[0]:
        campi = _campi_gap(finestra_a[1], finestra_b[0])
        if campi is None:
            return None
        inizio, fine, granularita = campi
    etichetta = _riserva_etichetta(
        _etichetta_fra(_testo(prima.etichetta), _testo(seconda.etichetta)),
        prese,
    )
    chiave = None
    if inizio is None:
        chiave = f"fra|{_chiave_collocazione_iso(prima)}|{_chiave_collocazione_iso(seconda)}"
    _id_sintetica(
        documento=documento,
        natura="intervallo",
        tipo="data",
        inizio=inizio,
        fine=fine,
        chiave=chiave,
    )
    return AncoraTemporaleProposta(
        etichetta=etichetta,
        natura="intervallo",
        tipo="data",
        inizio=inizio,
        fine=fine,
        granularita=granularita,  # type: ignore[arg-type]
        stimato=True,
        padre=prima.padre,
        espressione=chiave,
        posizione_doc_min=_min_int(prima.posizione_doc_min, seconda.posizione_doc_min),
    )


def _min_int(prima: int | None, seconda: int | None) -> int | None:
    if prima is None:
        return seconda
    if seconda is None:
        return prima
    return min(prima, seconda)


def _indice_in(gruppo: list[AncoraTemporaleProposta], bersaglio: AncoraTemporaleProposta) -> int:
    chiave = _chiave_nodo(bersaglio)
    for indice, ancora in enumerate(gruppo):
        if _chiave_nodo(ancora) == chiave:
            return indice
    return -1


def _secchio_lato(
    gruppo: list[AncoraTemporaleProposta], indice: int, lato: str
) -> bool:
    if lato == "prima":
        if indice <= 0:
            return False
        return gruppo[indice - 1].natura in {"intervallo", "aperta"}
    if indice < 0 or indice + 1 >= len(gruppo):
        return False
    return gruppo[indice + 1].natura in {"intervallo", "aperta"}


def _testo_riferimento(ancora: AncoraTemporaleProposta) -> str:
    return _testo(ancora.espressione) or _testo(ancora.etichetta)


def _lato_da_testo(testo: str) -> str | None:
    if _PRIMA.search(testo):
        return "prima"
    if _DOPO.search(testo):
        return "dopo"
    return None


def _resto_dopo_lato(testo: str, lato: str) -> str:
    pattern = _PRIMA if lato == "prima" else _DOPO
    match = pattern.search(testo)
    if match is None:
        return testo.strip()
    return testo[match.end() :].strip(" .,:;")


def _risolvi_nome(
    nome: str, per_etichetta: dict[str, AncoraTemporaleProposta]
) -> AncoraTemporaleProposta | None:
    chiave = etichetta_normalizzata(nome)
    if not chiave:
        return None
    if chiave in per_etichetta:
        return per_etichetta[chiave]
    candidati = [
        ancora
        for etichetta, ancora in per_etichetta.items()
        if etichetta and (etichetta in chiave or chiave in etichetta)
    ]
    if len(candidati) == 1:
        return candidati[0]
    return None


def _per_etichetta_gruppi(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
) -> dict[str, AncoraTemporaleProposta]:
    fuori: dict[str, AncoraTemporaleProposta] = {}
    for membri in gruppi.values():
        for ancora in membri:
            fuori[_chiave_nodo(ancora)] = ancora
    return fuori


def _evidenze(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
    segnali: list[SegnaleAncoraEvento],
) -> list[tuple[str, AncoraTemporaleProposta, AncoraTemporaleProposta | None]]:
    per_etichetta = _per_etichetta_gruppi(gruppi)
    fuori: list[tuple[str, AncoraTemporaleProposta, AncoraTemporaleProposta | None]] = []
    visti: set[tuple[str, str]] = set()

    def _aggiungi(
        lato: str,
        bersaglio: AncoraTemporaleProposta,
        esistente: AncoraTemporaleProposta | None,
    ) -> None:
        chiave = (lato, _chiave_nodo(bersaglio))
        if chiave in visti:
            return
        visti.add(chiave)
        fuori.append((lato, bersaglio, esistente))

    for membri in gruppi.values():
        for ancora in membri:
            testo = _testo_riferimento(ancora)
            lato = _lato_da_testo(testo)
            if lato is None:
                continue
            if ancora.tipo not in {"vaga", "relativa"} and ancora.natura != "aperta":
                continue
            resto = _resto_dopo_lato(testo, lato)
            bersaglio = _risolvi_nome(resto, per_etichetta)
            if bersaglio is None:
                continue
            esistente = ancora if ancora is not bersaglio else None
            _aggiungi(lato, bersaglio, esistente)

    for segnale in segnali:
        if segnale.posizione not in {"prima", "dopo"}:
            continue
        bersaglio = _risolvi_nome(segnale.ancora or "", per_etichetta)
        if bersaglio is None:
            continue
        _aggiungi(segnale.posizione, bersaglio, None)
    return fuori


def _sposta_in_gruppo(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
    ancora: AncoraTemporaleProposta,
    destinazione: str,
) -> None:
    for chiave, membri in list(gruppi.items()):
        gruppi[chiave] = [item for item in membri if _chiave_nodo(item) != _chiave_nodo(ancora)]
        if not gruppi[chiave]:
            del gruppi[chiave]
    gruppi.setdefault(destinazione, []).append(ancora)


def _inserisci_aperte(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
    prese: set[str],
    segnali: list[SegnaleAncoraEvento],
    *,
    documento: str,
) -> int:
    evidenze = _evidenze(gruppi, segnali)
    creati = 0
    for lato, bersaglio, esistente in evidenze:
        padre_k = _padre_key(bersaglio)
        gruppo = gruppi.setdefault(padre_k, [])
        indice = _indice_in(gruppo, bersaglio)
        if indice < 0:
            continue
        if _secchio_lato(gruppo, indice, lato):
            continue
        if esistente is not None and esistente.natura in {"aperta", "relativa"}:
            aggiornata = esistente.model_copy(
                update={"natura": "aperta", "padre": bersaglio.padre, "stimato": True}
            )
            _sposta_in_gruppo(gruppi, esistente, padre_k)
            gruppo = gruppi.setdefault(padre_k, [])
            gruppo = [item for item in gruppo if _chiave_nodo(item) != _chiave_nodo(aggiornata)]
            indice = _indice_in(gruppo, bersaglio)
            if indice < 0:
                gruppo.append(aggiornata)
            elif lato == "prima":
                gruppo.insert(indice, aggiornata)
            else:
                gruppo.insert(indice + 1, aggiornata)
            gruppi[padre_k] = gruppo
            creati += 1
            continue
        etichetta = _riserva_etichetta(_etichetta_lato(lato, _testo(bersaglio.etichetta)), prese)
        chiave = f"{lato}|{_chiave_collocazione_iso(bersaglio)}"
        _id_sintetica(
            documento=documento,
            natura="aperta",
            tipo="vaga",
            inizio=None,
            fine=None,
            chiave=chiave,
        )
        aperta = AncoraTemporaleProposta(
            etichetta=etichetta,
            natura="aperta",
            tipo="vaga",
            stimato=True,
            padre=bersaglio.padre,
            espressione=chiave,
        )
        if lato == "prima":
            gruppo.insert(indice, aperta)
        else:
            gruppo.insert(indice + 1, aperta)
        gruppi[padre_k] = gruppo
        creati += 1
    return creati


def _appiattisci(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
) -> list[AncoraTemporaleProposta]:
    fuori: list[AncoraTemporaleProposta] = []

    def _visita(padre_k: str) -> None:
        for ancora in gruppi.get(padre_k, []):
            fuori.append(ancora)
            _visita(_chiave_nodo(ancora))

    _visita("")
    visti = {_chiave_nodo(a) for a in fuori}
    restanti = [
        ancora
        for membri in gruppi.values()
        for ancora in membri
        if _chiave_nodo(ancora) not in visti
    ]
    restanti.sort(key=lambda a: _ordinamento(a))
    fuori.extend(restanti)
    return fuori


def _emetti_successione(
    gruppi: dict[str, list[AncoraTemporaleProposta]],
) -> list[tuple[str, str]]:
    fuori: list[tuple[str, str]] = []
    for chiave in sorted(gruppi):
        membri = gruppi[chiave]
        for sinistra, destra in zip(membri, membri[1:], strict=False):
            fuori.append((_testo(sinistra.etichetta), _testo(destra.etichetta)))
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


__all__ = [
    "ETICHETTA_MAX_CHAR",
    "EVENTO",
    "STAGE",
    "LineaAncore",
    "chiave_ordine_ancora",
    "costruisci_linea",
    "esegui_ancore_linea",
    "etichetta_normalizzata",
    "granularita_effettiva",
    "violazioni_foresta",
]
