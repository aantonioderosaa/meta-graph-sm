"""Parte B / MT3+MT4 — collocazione temporale degli eventi e cluster annidati.

Estrazione best-effort. Nessuna persistenza Neo4j (è MT5). Isolamento D6: l'LLM
passa esclusivamente da ``infra.llm.call_structured``.

La riconciliazione fra finestre e la foresta ``CONTIENE`` stanno in
``foresta_temporale``, che non conosce l'LLM: qui si chiama il modello e si
sanifica ciò che risponde, là si decide quali cluster sono lo stesso cluster e
quale annidamento è vero.

Due regole governano questo modulo e non vanno confuse fra loro:

* **Non inventare un calendario.** ``tempo_assoluto`` / ``inizio`` restano solo
  se il testo scrive una data. Una stima (``stimato=true``) non è un orologio:
  l'ISO viene azzerata. L'ordine temporale viene dalla grammatica e, dove quella
  non basta, dalla comprensione del racconto (``precede`` / ``contemporaneo_a``).
* **Raggruppare richiede confidenza.** Sotto ``SOGLIA_CLUSTER`` l'appartenenza
  al cluster non viene emessa e l'evento resta una foglia a sé. Meglio un
  evento isolato che un gruppo inventato.

L'input non è più il testo integrale del documento ma i **riassunti delle zone
attraversate dalla finestra** più gli span degli eventi: il prompt cresce con la
finestra (al più 60 eventi, quindi al più ~60 zone), non con la lunghezza del
documento, che è ciò che faceva sfondare il contesto sui testi lunghi.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.event_graph import (
    ClusterTemporaleProposto,
    EventoRisolto,
    LivelloTemporaleResult,
    SegnaleTemporaleEvento,
)
from app.pipeline.event_graph import foresta_temporale, tempo_iso
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.ponte_verifica import evento_nella_zona
from app.pipeline.event_graph.temporal_placement import _apply_tempo
from app.pipeline.event_graph.zona_segmentation import Zona

LIVELLO_MAX_EVENTI_PER_CHIAMATA = 60

SOGLIA_CLUSTER = 0.6
"""Confidenza minima perché l'appartenenza di un evento a un cluster sia emessa.

Il confronto è ``confidenza < SOGLIA_CLUSTER``: esattamente 0.6 raggruppa. La
soglia si legge sulla ``confidenza`` del **cluster**, che è la sola confidenza
di raggruppamento che lo schema porta (quella del segnale riguarda la
collocazione, non l'appartenenza).
"""

LIVELLO_MAX_CHAR_RIASSUNTO = 600
"""Taglio difensivo di un riassunto di zona nel prompt (~150 token)."""

LIVELLO_MAX_CHAR_ANCORE = 200
"""Taglio difensivo delle ancore temporali di una zona (~50 token).

Con i due tagli il blocco di una zona sta sotto gli ~810 caratteri, quindi una
finestra da 60 eventi che tocchi 60 zone diverse — il caso peggiore possibile,
visto che le zone entrano solo se contengono un evento della finestra — resta
sotto i ~13.000 token: il prompt cresce con la finestra, non con il documento.
"""

SYSTEM_LIVELLO_TEMPORALE = """Ricevi i riassunti delle zone di un testo, in
ordine di lettura, e la lista degli eventi già identificati (id, zona, frase).
Il compito è l'ORDINE temporale, non un calendario.

Non inventare date, ore, minuti, anni. Nessun 24 dicembre e nessun orario se
il testo non li scrive.

SEGNALI — uno per OGNI evento della lista, nessuno escluso.
- tempo_assoluto: ISO 8601 a precisione variabile (1843, 1843-12-24, …) SOLO
  se quella data è nel testo. Altrimenti null.
- granularita: solo se c'è tempo_assoluto. La più fine giustificata dal testo.
- stimato: non serve a inventare un orologio. Se non c'è data, lascia
  tempo_assoluto null. Non stimare un secolo, un giorno o un'ora.
- espressione_relativa: le parole temporali COM'E' NEL TESTO ("un giorno",
  "poi", "alla fine", "da quel giorno", "il giorno dopo"). Vuota se non ci sono.
- contemporaneo_a: id degli eventi allo STESSO MOMENTO ("mentre X, Y").
- precede: id degli eventi che accadono DOPO questo.

Su ogni coppia, UNA sola relazione fra precede e contemporaneo, mai entrambe.
Scegli così:
1. Prima la grammatica: poi, allora, dopo, prima, mentre, quando, appena,
   trapassato, "alla fine", "da quel giorno".
2. Se la grammatica non basta, il significato del racconto.
3. Solo relazioni DIRETTE. Se A poi B poi C: A.precede=[B] e B.precede=[C].
   Non chiudere il transitorio (niente A.precede=[B,C]) a meno che il testo
   lo dica. Meglio pochi archi giusti che un torneo. Se non capisci, lascia
   la coppia vuota.

CLUSTER — solo eventi che il testo mette nello stesso momento.
- etichetta: breve, MASSIMO 40 caratteri, dalla lingua del testo
  ("un giorno", "da quel giorno", "inverno 1843" se l'inverno c'è). Non è
  il riassunto della scena: la prosa va in descrizione.
- inizio e fine: ISO solo se la data è nel testo. Altrimenti null.
- tipo: data_esplicita solo con una data scritta; altrimenti relativo o
  simbolico. Non inventare ore e minuti.
- padre: etichetta del cluster che contiene questo, identica, o null.
- eventi: id nel cluster più specifico. Contenitore puro: eventi vuoto.
- confidenza 0–1. Sotto 0.6 non raggruppare.

Usa solo gli id forniti. Italiano o inglese. Temperatura 0."""


def _evento_pos_key(evento: EventoRisolto) -> tuple[int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
        evento.id or "",
    )


def _span_for(evento: EventoRisolto) -> str:
    span = (evento.span or "").strip()
    if span:
        return span
    lemma = (evento.lemma or "").strip()
    ancora = (evento.ancora or "").strip()
    if lemma and ancora:
        return f"{lemma}|{ancora}"
    return lemma or ancora


def _evento_line(evento: EventoRisolto, numero_zona: int | None) -> str | None:
    eid = (evento.id or "").strip()
    if not eid:
        return None
    if numero_zona is None:
        return f"{eid} | {_span_for(evento)}"
    return f"{eid} | zona {numero_zona} | {_span_for(evento)}"


def _zona_ord_key(zona: Zona) -> tuple[int, int, str]:
    ordinale = getattr(zona, "ordinale", 0)
    return (
        ordinale if isinstance(ordinale, int) else 0,
        zona.offset_inizio,
        str(getattr(zona, "id", "") or ""),
    )


def _zone_valide(zone: list[Zona] | None) -> list[Zona]:
    """Le zone utilizzabili, in ordine di lettura.

    Tollerante per scelta: una zona senza offset interi non è collocabile
    rispetto agli eventi e viene ignorata invece di far cadere la finestra.
    """
    try:
        candidate = list(zone or [])
    except TypeError:
        return []
    valide = [
        zona
        for zona in candidate
        if isinstance(getattr(zona, "offset_inizio", None), int)
        and isinstance(getattr(zona, "offset_fine", None), int)
    ]
    valide.sort(key=_zona_ord_key)
    return valide


def _mappa_zone(
    window: list[EventoRisolto], zone: list[Zona]
) -> tuple[list[int], dict[str, int]]:
    """Indici delle zone attraversate dalla finestra, in ordine di lettura.

    Una zona è "coinvolta" se contiene almeno un evento della finestra secondo
    ``ponte_verifica.evento_nella_zona``. Un evento il cui span cade a cavallo di
    due zone viene riferito alla prima: al modello serve un contesto, non una
    partizione. La numerazione che finisce nel prompt (1, 2, 3…) è quella delle
    zone scelte, non l'ordinale nel documento: al modello serve un riferimento
    fra le righe che ha davanti.
    """
    scelte: list[int] = []
    per_evento: dict[str, int] = {}
    for indice, zona in enumerate(zone):
        membri: list[EventoRisolto] = []
        for evento in window:
            try:
                dentro = evento_nella_zona(evento, zona)
            except Exception:
                dentro = False
            if dentro:
                membri.append(evento)
        if not membri:
            continue
        scelte.append(indice)
        numero = len(scelte)
        for evento in membri:
            eid = (evento.id or "").strip()
            if eid and eid not in per_evento:
                per_evento[eid] = numero
    return scelte, per_evento


def _tronca(testo: str, massimo: int) -> str:
    if len(testo) <= massimo:
        return testo
    return testo[:massimo].rstrip() + "…"


def _contenuto_zona(zona: Zona) -> tuple[str, str] | None:
    """Riassunto e ancore temporali, o None se la zona non ha né l'uno né le altre."""
    riassunto = _tronca(
        (getattr(zona, "riassunto", "") or "").strip(), LIVELLO_MAX_CHAR_RIASSUNTO
    )
    ancore = "; ".join(
        ancora.strip()
        for ancora in (getattr(zona, "ancore_temporali", None) or [])
        if isinstance(ancora, str) and ancora.strip()
    )
    ancore = _tronca(ancore, LIVELLO_MAX_CHAR_ANCORE)
    if not riassunto and not ancore:
        return None
    return riassunto, ancore


def _blocco_zona(numero: int, contenuto: tuple[str, str]) -> str:
    riassunto, ancore = contenuto
    testa = f"zona {numero}"
    if ancore:
        testa += f" | ancore: {ancore}"
    return f"{testa}\n{riassunto}" if riassunto else testa


def user_livello_temporale(
    eventi: list[EventoRisolto],
    zone: list[Zona] | None = None,
) -> str:
    """Riassunti delle zone attraversate + una riga per evento della finestra.

    Gli eventi sono in ordine di esposizione (posizione_doc, posizione_chunk, id
    — lo stesso di ``SottoGrafo.eventi_per_posizione``) e usano lo span, o il
    fallback ``lemma|ancora`` quando lo span è vuoto. Nessun id inventato.

    Degradazione, in ordine: una zona senza riassunto entra comunque con le sue
    ancore temporali; una zona senza riassunto né ancore sparisce prima di essere
    numerata, e i suoi eventi restano nella lista senza riferimento di zona; se
    nessuna zona sopravvive (zone assenti, non ancora riassunte, o eventi senza
    offset) resta la sola lista degli eventi, che è pur sempre un contesto — mai
    un prompt vuoto, e mai il testo integrale del documento.
    """
    ordered = sorted(list(eventi or []), key=_evento_pos_key)
    utili = [
        (zona, contenuto)
        for zona in _zone_valide(zone)
        if (contenuto := _contenuto_zona(zona)) is not None
    ]
    scelte, per_evento = _mappa_zone(ordered, [zona for zona, _ in utili])
    blocchi = [
        _blocco_zona(numero, utili[indice][1])
        for numero, indice in enumerate(scelte, start=1)
    ]
    righe = [
        riga
        for evento in ordered
        if (riga := _evento_line(evento, per_evento.get((evento.id or "").strip())))
        is not None
    ]
    parti: list[str] = []
    if blocchi:
        parti.append("ZONE (contesto, in ordine di lettura)\n\n" + "\n\n".join(blocchi))
    if righe:
        intestazione = (
            "EVENTI DA COLLOCARE (id | zona | frase)"
            if blocchi
            else "EVENTI DA COLLOCARE (id | frase)"
        )
        parti.append(intestazione + "\n" + "\n".join(righe))
    return "\n\n".join(parti)


def _as_livello(parsed: Any) -> LivelloTemporaleResult | None:
    if isinstance(parsed, LivelloTemporaleResult):
        return parsed
    try:
        return LivelloTemporaleResult.model_validate(parsed)
    except Exception:
        return None


def _known_ids(eventi: list[EventoRisolto]) -> set[str]:
    return {evento.id for evento in eventi if evento.id}


_MESI_NOME: dict[int, tuple[str, ...]] = {
    1: ("gennaio", "january", "jan", "gen"),
    2: ("febbraio", "february", "feb"),
    3: ("marzo", "march", "mar"),
    4: ("aprile", "april", "apr"),
    5: ("maggio", "may", "mag"),
    6: ("giugno", "june", "jun", "giu"),
    7: ("luglio", "july", "jul", "lug"),
    8: ("agosto", "august", "aug", "ago"),
    9: ("settembre", "september", "sep", "set"),
    10: ("ottobre", "october", "oct", "ott"),
    11: ("novembre", "november", "nov"),
    12: ("dicembre", "december", "dec", "dic"),
}
_ORARIO_RE = re.compile(r"\b\d{1,2}\s*[:.h]\s*\d{2}\b")


def _corpus_ancoraggio(
    eventi: list[EventoRisolto], zone: list[Zona] | None
) -> tuple[str, bool]:
    """Testo su cui ancorare una data, e se c'è il documento (testo di zona)."""
    parti: list[str] = []
    ha_documento = False
    for evento in eventi or []:
        span = _span_for(evento)
        if span:
            parti.append(span)
        grezzo = getattr(evento, "tempo_assoluto_grezzo", None)
        if grezzo:
            parti.append(str(grezzo))
    for zona in zone or []:
        if not isinstance(zona, Zona):
            continue
        if (zona.testo or "").strip():
            ha_documento = True
            parti.append(zona.testo)
        if zona.riassunto:
            parti.append(zona.riassunto)
        if zona.ancore_temporali:
            parti.extend(zona.ancore_temporali)
    return " ".join(parti).casefold(), ha_documento


def _mese_nel_testo(mese: int, corpus: str) -> bool:
    token = f"-{mese:02d}"
    if token in corpus or f"/{mese:02d}" in corpus:
        return True
    return any(nome in corpus for nome in _MESI_NOME.get(mese, ()))


def _giorno_nel_testo(giorno: int, corpus: str) -> bool:
    return bool(re.search(rf"\b0?{giorno}\b", corpus))


def giustifica_iso(valore: object, corpus: str) -> str | None:
    """ISO tenuta solo nella precisione che il testo giustifica.

    Senza l'anno nel corpus la data è inventata e cade. Mese, giorno e ora
    restano solo se il testo li scrive; altrimenti si taglia alla precisione
    ancora ancorata. Non solleva.
    """
    letto = tempo_iso.analizza(valore)
    if letto is None or not corpus:
        return None
    if str(letto.anno) not in corpus:
        return None
    if letto.precisione == "anno":
        return f"{letto.anno:04d}"
    if not _mese_nel_testo(letto.mese, corpus):
        return f"{letto.anno:04d}"
    if letto.precisione == "mese":
        return f"{letto.anno:04d}-{letto.mese:02d}"
    if not _giorno_nel_testo(letto.giorno, corpus):
        return f"{letto.anno:04d}-{letto.mese:02d}"
    data = f"{letto.anno:04d}-{letto.mese:02d}-{letto.giorno:02d}"
    if letto.precisione == "giorno" or not _ORARIO_RE.search(corpus):
        return data
    if letto.precisione == "ora":
        return f"{data}T{letto.ora:02d}"
    if letto.precisione == "minuto":
        return f"{data}T{letto.ora:02d}:{letto.minuto:02d}"
    return f"{data}T{letto.ora:02d}:{letto.minuto:02d}:{letto.secondo:02d}"


def _iso_da_tenere(
    valore: object, *, stimato: bool, corpus: str, ancora_al_testo: bool
) -> str | None:
    """``None`` se la data è una stima o non è nel documento."""
    if stimato:
        return None
    if not ancora_al_testo:
        return valore if tempo_iso.analizza(valore) else None
    return giustifica_iso(valore, corpus)


def _dedup(ids: list[str]) -> list[str]:
    visti: set[str] = set()
    fuori: list[str] = []
    for eid in ids:
        if eid not in visti:
            visti.add(eid)
            fuori.append(eid)
    return fuori


def _sanitize_segnale(
    segnale: SegnaleTemporaleEvento,
    eid: str,
    known: set[str],
    *,
    corpus: str,
    ancora_al_testo: bool,
) -> SegnaleTemporaleEvento:
    """Ricopia il segnale, toglie le date inventate, deduce la granularità."""
    contemporaneo = [
        other
        for other in (segnale.contemporaneo_a or [])
        if other in known and other != eid
    ]
    precede = [
        other
        for other in (segnale.precede or [])
        if other in known and other != eid
    ]
    tempo = _iso_da_tenere(
        segnale.tempo_assoluto,
        stimato=bool(segnale.stimato),
        corpus=corpus,
        ancora_al_testo=ancora_al_testo,
    )
    granularita = (
        (segnale.granularita or tempo_iso.precisione(tempo)) if tempo else None
    )
    return segnale.model_copy(
        update={
            "evento_id": eid,
            "tempo_assoluto": tempo,
            "contemporaneo_a": _dedup(contemporaneo),
            "precede": _dedup(precede),
            "granularita": granularita,
        }
    )


def _sanitize_cluster(
    proposed: ClusterTemporaleProposto,
    etichetta: str,
    membri: list[str],
    padre: str | None,
) -> ClusterTemporaleProposto:
    """Ricopia il cluster conservando i campi nuovi, validando la collocazione.

    Un ``inizio`` (o ``fine``) che ``tempo_iso.analizza`` non riconosce non fa
    cadere il cluster — etichetta, descrizione, tipo e membri restano — ma viene
    azzerato: un "24 dicembre" senza anno o un "circa 1840" che arrivasse fino a
    ``chiave_ordine`` sarebbe una data falsa, e una data falsa ordina l'asse
    peggio di una data assente. La stringa resta com'è stata scritta: la sua
    forma canonica vive solo dentro la chiave di identità di
    ``foresta_temporale.chiave_collocazione``.
    """
    inizio = proposed.inizio if tempo_iso.analizza(proposed.inizio) else None
    fine = proposed.fine if tempo_iso.analizza(proposed.fine) else None
    granularita = proposed.granularita or tempo_iso.precisione(inizio)
    tipo = proposed.tipo
    if inizio is None and tipo == "data_esplicita":
        tipo = "relativo"
    return proposed.model_copy(
        update={
            "etichetta": etichetta,
            "eventi": membri,
            "inizio": inizio,
            "fine": fine,
            "granularita": granularita,
            "padre": padre,
            "tipo": tipo,
        }
    )


def _ancorare_cluster(
    cluster: ClusterTemporaleProposto, corpus: str, ancora_al_testo: bool
) -> ClusterTemporaleProposto:
    inizio = _iso_da_tenere(
        cluster.inizio,
        stimato=bool(cluster.stimato),
        corpus=corpus,
        ancora_al_testo=ancora_al_testo,
    )
    fine = _iso_da_tenere(
        cluster.fine,
        stimato=bool(cluster.stimato),
        corpus=corpus,
        ancora_al_testo=ancora_al_testo,
    )
    return cluster.model_copy(update={"inizio": inizio, "fine": fine})


def _cluster_sanificati(
    proposti: list[ClusterTemporaleProposto],
    known: set[str],
    *,
    corpus: str = "",
    ancora_al_testo: bool = False,
) -> list[ClusterTemporaleProposto]:
    """Fusione per collocazione, gate di confidenza, membri noti, padri risolvibili.

    Un cluster sopravvive se ha almeno un membro, oppure se è il ``padre`` di un
    cluster che sopravvive: un contenitore puro non ha eventi propri e sparirebbe
    portandosi via l'annidamento. Il gate toglie le appartenenze, non i
    contenitori — un padre poco confidente che regge un figlio confidente resta,
    svuotato dei suoi eventi diretti.

    Da MT4 l'identità del cluster è la sua collocazione, non la sua etichetta:
    ``foresta_temporale.fondi_per_collocazione`` — idempotente, quindi innocua
    anche quando ``_merge_results`` l'ha già applicata — riduce la lista a un
    cluster per collocazione e restituisce il vocabolario con cui risolvere i
    ``padre``, che il modello scrive come etichette e che la fusione può avere
    rinominato.
    """
    proposti = [_ancorare_cluster(c, corpus, ancora_al_testo) for c in proposti]
    riconciliazione = foresta_temporale.fondi_per_collocazione(proposti)
    ordine: list[foresta_temporale.Chiave] = []
    per_chiave: dict[foresta_temporale.Chiave, ClusterTemporaleProposto] = {}
    membri_di: dict[foresta_temporale.Chiave, list[str]] = {}
    for cluster in riconciliazione.cluster:
        if not (cluster.etichetta or "").strip():
            continue
        chiave = foresta_temporale.chiave_collocazione(cluster)
        if chiave is None or chiave in per_chiave:
            continue
        ordine.append(chiave)
        per_chiave[chiave] = cluster
        membri_di[chiave] = (
            []
            if cluster.confidenza < SOGLIA_CLUSTER
            else _dedup([eid for eid in (cluster.eventi or []) if eid in known])
        )

    def _padre_di(chiave: foresta_temporale.Chiave) -> foresta_temporale.Chiave | None:
        riferimento = foresta_temporale.etichetta_normalizzata(
            per_chiave[chiave].padre
        )
        if not riferimento:
            return None
        padre = riconciliazione.etichette.get(riferimento)
        if padre is None or padre == chiave or padre not in per_chiave:
            return None
        return padre

    vivi = {chiave for chiave in ordine if membri_di[chiave]}
    while True:
        aggiunti = {
            padre
            for chiave in vivi
            if (padre := _padre_di(chiave)) is not None and padre not in vivi
        }
        if not aggiunti:
            break
        vivi |= aggiunti

    fuori: list[ClusterTemporaleProposto] = []
    for chiave in ordine:
        if chiave not in vivi:
            continue
        cluster = per_chiave[chiave]
        padre = _padre_di(chiave)
        etichetta_padre = (
            (per_chiave[padre].etichetta or "").strip() if padre in vivi else ""
        )
        fuori.append(
            _sanitize_cluster(
                cluster,
                (cluster.etichetta or "").strip(),
                membri_di[chiave],
                etichetta_padre or None,
            )
        )
    return fuori


def _finestra_segnale(
    segnale: SegnaleTemporaleEvento,
) -> tuple[int, int] | None:
    return tempo_iso.bounds(segnale.tempo_assoluto, segnale.granularita)


def _ordine_da_resto(
    primo: SegnaleTemporaleEvento,
    secondo: SegnaleTemporaleEvento,
) -> str:
    """Cosa dicono date e granularità sulla coppia, senza guardare gli archi.

    ``a_prima`` / ``b_prima``: le finestre non si toccano. ``sovrapposto``:
    si accavallano o coincidono. ``sconosciuto``: almeno una non è leggibile.
    """
    prima = _finestra_segnale(primo)
    seconda = _finestra_segnale(secondo)
    if prima is None or seconda is None:
        chiave_a = tempo_iso.chiave_ordine(primo.tempo_assoluto, primo.granularita)
        chiave_b = tempo_iso.chiave_ordine(secondo.tempo_assoluto, secondo.granularita)
        if chiave_a is None or chiave_b is None:
            return "sconosciuto"
        if chiave_a < chiave_b:
            return "a_prima"
        if chiave_b < chiave_a:
            return "b_prima"
        return "sovrapposto"
    if prima[1] <= seconda[0]:
        return "a_prima"
    if seconda[1] <= prima[0]:
        return "b_prima"
    return "sovrapposto"


def coppie_precede_da_archi(archi: object) -> set[frozenset[str]]:
    """Coppie che hanno già un PRECEDE vivo, da non raddoppiare con CONTEMPORANEO."""
    fuori: set[frozenset[str]] = set()
    try:
        items = list(archi or [])
    except Exception:
        return fuori
    for arco in items:
        try:
            if str(getattr(arco, "tipo", "") or "") != "PRECEDE":
                continue
            props = getattr(arco, "props", None) or {}
            if props.get("superato_da"):
                continue
            da_id = str(getattr(arco, "da_id", "") or "").strip()
            a_id = str(getattr(arco, "a_id", "") or "").strip()
        except Exception:
            continue
        if da_id and a_id and da_id != a_id:
            fuori.add(frozenset((da_id, a_id)))
    return fuori


def _escludi_precede_contemporaneo(
    segnali: list[SegnaleTemporaleEvento],
    eventi: list[EventoRisolto],
    coppie_precede: set[frozenset[str]] | None = None,
) -> list[SegnaleTemporaleEvento]:
    """Una sola relazione per coppia: o precede o contemporaneo, mai entrambe.

    Il modello deve aver già scelto; qui si applica lo stesso criterio se
    ha emesso entrambe o se il resto dei segnali (finestre, poi posizione
    nel documento) contraddice la scelta. Un PRECEDE già presente nel grafo
    vince sul contemporaneo della stessa coppia. In dubbio, si tiene
    precede: contemporaneo è l'affermazione più forte e quella che il
    modello ha sovra-emesso.
    """
    by_id = {segnale.evento_id: segnale for segnale in segnali if segnale.evento_id}
    posizione = {
        evento.id: evento.posizione_doc
        for evento in eventi
        if evento.id
    }
    esistenti = set(coppie_precede or ())
    precede_dir: dict[frozenset[str], tuple[str, str]] = {}
    contemporanee: set[frozenset[str]] = set()

    for segnale in segnali:
        eid = segnale.evento_id
        for other in segnale.precede or []:
            if other not in by_id or other == eid:
                continue
            coppia = frozenset((eid, other))
            precedente = precede_dir.get(coppia)
            if precedente is None or precedente == (eid, other):
                precede_dir[coppia] = (eid, other)
            elif precedente == (other, eid):
                ordine = _ordine_da_resto(by_id[eid], by_id[other])
                if ordine == "a_prima":
                    precede_dir[coppia] = (eid, other)
                elif ordine == "b_prima":
                    precede_dir[coppia] = (other, eid)
                else:
                    precede_dir.pop(coppia, None)
        for other in segnale.contemporaneo_a or []:
            if other not in by_id or other == eid:
                continue
            contemporanee.add(frozenset((eid, other)))

    for coppia in esistenti:
        contemporanee.discard(coppia)

    for coppia in list(contemporanee):
        primo, secondo = tuple(coppia)
        ordine = _ordine_da_resto(by_id[primo], by_id[secondo])
        if ordine == "a_prima":
            contemporanee.discard(coppia)
            precede_dir.setdefault(coppia, (primo, secondo))
        elif ordine == "b_prima":
            contemporanee.discard(coppia)
            precede_dir.setdefault(coppia, (secondo, primo))

    for coppia in list(precede_dir):
        if coppia in esistenti:
            contemporanee.discard(coppia)
            continue
        primo, secondo = precede_dir[coppia]
        ordine = _ordine_da_resto(by_id[primo], by_id[secondo])
        if ordine == "sovrapposto":
            precede_dir.pop(coppia, None)
            contemporanee.add(coppia)
        elif ordine == "b_prima":
            precede_dir[coppia] = (secondo, primo)

    for coppia in list(contemporanee & precede_dir.keys()):
        if coppia in esistenti:
            contemporanee.discard(coppia)
            continue
        primo, secondo = tuple(coppia)
        pos_a = posizione.get(primo)
        pos_b = posizione.get(secondo)
        if pos_a is not None and pos_b is not None and pos_a != pos_b:
            earlier, later = (primo, secondo) if pos_a < pos_b else (secondo, primo)
            contemporanee.discard(coppia)
            precede_dir[coppia] = (earlier, later)
        else:
            contemporanee.discard(coppia)

    # Niente riduzione transitiva qui: ogni coppia in precede_dir è
    # un'asserzione diretta del modello (o il suo tie-break di conflitto
    # riga sopra), non un confronto di date inventate — cade solo ciò che
    # sanitize() ha già escluso a monte. Buttare A→C perché A→B→C esiste
    # cancellerebbe un'affermazione vera solo perché è anche implicita.
    nuovi: list[SegnaleTemporaleEvento] = []
    for segnale in segnali:
        eid = segnale.evento_id
        contemp = [
            next(iter(coppia - {eid}))
            for coppia in contemporanee
            if eid in coppia
        ]
        pred = [later for da, later in precede_dir.values() if da == eid]
        nuovi.append(
            segnale.model_copy(
                update={
                    "contemporaneo_a": _dedup(sorted(contemp)),
                    "precede": _dedup(sorted(pred)),
                }
            )
        )
    return nuovi


def _sanitize(
    result: LivelloTemporaleResult,
    eventi: list[EventoRisolto],
    coppie_precede: set[frozenset[str]] | None = None,
    zone: list[Zona] | None = None,
) -> LivelloTemporaleResult:
    """Segnali sugli eventi noti, cluster fusi e ridotti a una foresta valida.

    L'ordine dei tre passaggi sui cluster non è scambiabile: il gate deve
    vedere i cluster già fusi (la ``confidenza`` di un cluster fuso è il minimo
    fra le finestre), la foresta deve vedere ``inizio``/``granularita`` già
    validati da ``_sanitize_cluster``, e la potatura dei contenitori sterili
    deve vedere gli archi che la foresta ha davvero tenuto.
    """
    known = _known_ids(eventi)
    corpus, ancora_al_testo = _corpus_ancoraggio(eventi, zone)
    segnali: list[SegnaleTemporaleEvento] = []
    for segnale in result.segnali:
        eid = (segnale.evento_id or "").strip()
        if eid not in known:
            continue
        segnali.append(
            _sanitize_segnale(
                segnale,
                eid,
                known,
                corpus=corpus,
                ancora_al_testo=ancora_al_testo,
            )
        )
    segnali = _escludi_precede_contemporaneo(segnali, eventi, coppie_precede)
    cluster = foresta_temporale.scarta_contenitori_sterili(
        foresta_temporale.costruisci_foresta(
            _cluster_sanificati(
                list(result.cluster),
                known,
                corpus=corpus,
                ancora_al_testo=ancora_al_testo,
            )
        )
    )
    return LivelloTemporaleResult(segnali=segnali, cluster=cluster)


def _merge_results(parts: list[LivelloTemporaleResult]) -> LivelloTemporaleResult:
    """Unisce le finestre: segnali concatenati, cluster fusi per collocazione.

    La fusione non confronta più le etichette (MT3) ma
    ``(inizio, granularita)`` normalizzati: due finestre che etichettano lo
    stesso momento in modi diversi producono un cluster solo, e due momenti
    diversi che il modello ha etichettato allo stesso modo restano due.
    """
    segnali: list[SegnaleTemporaleEvento] = []
    cluster: list[ClusterTemporaleProposto] = []
    for part in parts:
        segnali.extend(part.segnali)
        cluster.extend(part.cluster)
    return LivelloTemporaleResult(
        segnali=segnali,
        cluster=foresta_temporale.fondi_per_collocazione(cluster).cluster,
    )


def applica_segnali_temporali(
    eventi: list[EventoRisolto],
    result: LivelloTemporaleResult,
) -> None:
    """In-memory append-only write of tempo_assoluto (B3). No CONTEMPORANEO arcs."""
    by_id = {evento.id: evento for evento in eventi if evento.id}
    for segnale in result.segnali:
        if segnale.stimato:
            continue
        raw = segnale.tempo_assoluto
        if raw is None:
            continue
        if isinstance(raw, str) and not raw.strip():
            continue
        evento = by_id.get(segnale.evento_id)
        if evento is None:
            continue
        _apply_tempo(evento, raw)


def eventi_senza_collocazione(
    eventi: list[EventoRisolto],
    result: LivelloTemporaleResult | None,
) -> list[str]:
    """Gli id rimasti senza posizione nel tempo, in ordine di esposizione.

    La misura del criterio di accettazione "zero eventi senza posizione". Un
    evento è collocato se il suo segnale porta un ``tempo_assoluto`` o
    un'``espressione_relativa``, oppure se appartiene a un cluster con un
    ``inizio`` valido — **o a un cluster i cui antenati ne hanno uno**: il
    piano ricava i cluster più grossi risalendo ``CONTIENE``, quindi un evento
    in "sette anni prima" dentro "1843" è collocato al 1843. La confidenza non
    conta: una stima incerta è comunque una posizione.
    """
    ordered = sorted(list(eventi or []), key=_evento_pos_key)
    if result is None:
        return [evento.id for evento in ordered if evento.id]
    collocati: set[str] = set()
    for segnale in result.segnali:
        testo = (segnale.tempo_assoluto or "").strip() or (
            segnale.espressione_relativa or ""
        ).strip()
        if testo:
            collocati.add(segnale.evento_id)
    per_etichetta = {
        foresta_temporale.etichetta_normalizzata(proposto.etichetta): proposto
        for proposto in result.cluster
        if (proposto.etichetta or "").strip()
    }
    for proposto in result.cluster:
        if not proposto.eventi:
            continue
        corrente: ClusterTemporaleProposto | None = proposto
        visti: set[str] = set()
        while corrente is not None:
            if corrente.inizio:
                collocati.update(proposto.eventi)
                break
            etichetta = foresta_temporale.etichetta_normalizzata(corrente.etichetta)
            if etichetta in visti:
                break
            visti.add(etichetta)
            padre = foresta_temporale.etichetta_normalizzata(corrente.padre)
            corrente = per_etichetta.get(padre) if padre else None
    return [
        evento.id for evento in ordered if evento.id and evento.id not in collocati
    ]


async def _chiama_finestra(
    window: list[EventoRisolto],
    zone: list[Zona] | None,
    *,
    job_id: str | None,
) -> LivelloTemporaleResult | None:
    try:
        parsed = await call_structured(
            SYSTEM_LIVELLO_TEMPORALE,
            user_livello_temporale(window, zone),
            LivelloTemporaleResult,
            temperature=0,
            job_id=job_id,
        )
    except Exception:
        return None
    return _as_livello(parsed)


async def estrai_livello_temporale(
    eventi: list[EventoRisolto],
    zone: list[Zona] | None = None,
    *,
    job_id: str | None = None,
    coppie_precede: set[frozenset[str]] | None = None,
) -> LivelloTemporaleResult | None:
    """Best-effort, one call up to LIVELLO_MAX_EVENTI_PER_CHIAMATA events.

    Beyond the cap, consecutive exposition-order windows are merged. LLM
    failure on a window contributes nothing; if every window fails, return
    None. Never raises.
    """
    try:
        items = list(eventi or [])
    except Exception:
        return None
    if not items:
        return LivelloTemporaleResult()
    try:
        contesto = _zone_valide(zone)
        ordered = sorted(items, key=_evento_pos_key)
        cap = LIVELLO_MAX_EVENTI_PER_CHIAMATA
        windows = [ordered[i : i + cap] for i in range(0, len(ordered), cap)]
        parts: list[LivelloTemporaleResult] = []
        for window in windows:
            part = await _chiama_finestra(window, contesto, job_id=job_id)
            if part is None:
                continue
            parts.append(part)
        if not parts:
            return None
        merged = _merge_results(parts)
        sanitized = _sanitize(merged, items, coppie_precede, zone=contesto)
        try:
            applica_segnali_temporali(items, sanitized)
        except Exception:
            pass
        return sanitized
    except Exception:
        return None


__all__ = [
    "LIVELLO_MAX_CHAR_ANCORE",
    "LIVELLO_MAX_CHAR_RIASSUNTO",
    "LIVELLO_MAX_EVENTI_PER_CHIAMATA",
    "SOGLIA_CLUSTER",
    "SYSTEM_LIVELLO_TEMPORALE",
    "applica_segnali_temporali",
    "coppie_precede_da_archi",
    "estrai_livello_temporale",
    "giustifica_iso",
    "eventi_senza_collocazione",
    "user_livello_temporale",
]
