"""Parte B / MT4 — riconciliazione dei cluster temporali e foresta `CONTIENE`.

Funzioni pure. Nessun I/O, nessun LLM, nessun Neo4j: qui si decide *quali
cluster temporali sono lo stesso cluster* e *quale annidamento è vero*, e nulla
più. La persistenza (`CONTIENE`, `chiave_ordine` sul nodo) è MT5.

Il modulo sta fuori da `livello_temporale.py` per tre motivi: il file
dell'estrazione è già a ~600 righe e mescolava prompt, chiamata LLM e
riconciliazione; le funzioni qui dentro non toccano l'LLM e vanno testate senza
stub; MT5 e MT6 hanno bisogno di questa riconciliazione (archi e chiave
d'ordine) senza importare il modulo che parla col modello.

Due nozioni di identità, e sono diverse fra loro:

* **La collocazione** — `(canonico, granularita)` di `tempo_iso.normalizza` — è
  l'identità di un cluster che dichiara un `inizio` leggibile. Due finestre che
  descrivono lo stesso momento producono la stessa chiave anche se lo scrivono
  in modi diversi (`"1843-12-24 18:30"` e `"1843-12-24T18:30Z"`) e anche se lo
  etichettano in modi diversi. È la chiave che il piano chiede per la fusione.
* **L'etichetta** — normalizzata a spazi e maiuscole — è l'unica identità
  disponibile per i cluster `relativo`/`simbolico` che non hanno un `inizio`
  ("sette anni prima"), ed è anche il vocabolario con cui il modello scrive i
  riferimenti `padre`. Serve quindi sia come chiave di ripiego sia come mappa
  verso la collocazione.

Le due si saldano: un cluster senza `inizio` viene assorbito dal cluster con
`inizio` che porta la sua stessa etichetta, quando ce n'è **esattamente uno**.
È il caso reale di due finestre che vedono lo stesso momento e solo una delle
due riesce a datarlo.

Best-effort come tutto il livello temporale: **nessuna funzione qui solleva**.
Input malformato vale lista vuota o campo azzerato, mai un'eccezione che
interrompa l'ingestione (decisione D6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from app.models.event_graph import ClusterTemporaleProposto
from app.pipeline.event_graph import tempo_iso

ETICHETTA_MAX_CHAR: Final[int] = 40
"""Limite di lunghezza dell'etichetta chiesto dal piano ("24 dic, sera").

Non viene imposto tagliando le stringhe del modello: serve a *scegliere* fra
due etichette dello stesso cluster fuso, e a mandare in `descrizione` quella
scartata quando è prosa e non una collocazione.
"""

CHIAVE_ISO: Final[str] = "iso"
CHIAVE_ETICHETTA: Final[str] = "etichetta"

Chiave = tuple[str, str, str]
"""Identità di un cluster fra finestre.

`(CHIAVE_ISO, canonico, granularita_effettiva)` quando la collocazione è
leggibile, `(CHIAVE_ETICHETTA, etichetta_normalizzata, "")` altrimenti. Tuple di
stringhe di proposito: è hashabile, ordinabile e stampabile in un messaggio di
violazione senza conversioni.
"""


@dataclass(frozen=True)
class Riconciliazione:
    """Cluster fusi più il vocabolario delle etichette che li nominano.

    `etichette` mappa **ogni** etichetta vista — quella canonica del cluster
    fuso e gli alias scartati durante la fusione — sulla `Chiave` del cluster
    che la porta, e vale `None` quando la stessa etichetta nomina cluster
    diversi. È ciò che rende risolvibili i `padre` scritti dall'LLM dopo che la
    fusione per collocazione ha cambiato l'etichetta canonica.
    """

    cluster: list[ClusterTemporaleProposto] = field(default_factory=list)
    etichette: dict[str, Chiave | None] = field(default_factory=dict)


def etichetta_normalizzata(valore: object) -> str:
    """Etichetta confrontabile: spazi collassati, maiuscole appiattite.

    `"  24 Dic,  Sera "` e `"24 dic, sera"` sono la stessa etichetta: un modello
    locale non ricopia la stringa carattere per carattere quando scrive `padre`.
    """
    if not isinstance(valore, str):
        return ""
    return " ".join(valore.split()).casefold()


def _testo(valore: object) -> str:
    return valore.strip() if isinstance(valore, str) else ""


def _cluster_validi(clusters: object) -> list[ClusterTemporaleProposto]:
    """I soli `ClusterTemporaleProposto` di un iterabile qualsiasi."""
    try:
        candidati = list(clusters or [])
    except TypeError:
        return []
    return [c for c in candidati if isinstance(c, ClusterTemporaleProposto)]


def chiave_iso(cluster: ClusterTemporaleProposto) -> Chiave | None:
    """La chiave di collocazione, o None se il cluster non è datato.

    Delega a `tempo_iso.normalizza`: un `inizio` non parsabile non è una
    collocazione, e un `inizio` valido con una `granularita` più grossa della
    stringa si legge alla granularità dichiarata (`("1843-12", "anno")`).
    """
    normalizzata = tempo_iso.normalizza(cluster.inizio, cluster.granularita)
    if normalizzata is None:
        return None
    canonico, effettiva = normalizzata
    return (CHIAVE_ISO, canonico, effettiva)


def granularita_effettiva(cluster: ClusterTemporaleProposto) -> str | None:
    """La granularità con cui il cluster occupa l'asse, non quella dichiarata.

    È la **più grossa** fra la precisione di `inizio` e la `granularita`
    dichiarata, cioè la stessa che `tempo_iso.bounds` usa per l'ampiezza: un
    cluster `inizio="1843"` con `granularita="secondo"` occupa un anno, perché
    il secondo non lo sappiamo. Confrontare le granularità dichiarate e le
    finestre calcolate su due letture diverse darebbe verdetti incoerenti sullo
    stesso arco. Vale anche quando `granularita` non è stata dichiarata: la
    stringa la implica.
    """
    if not isinstance(cluster, ClusterTemporaleProposto):
        return None
    normalizzata = tempo_iso.normalizza(cluster.inizio, cluster.granularita)
    if normalizzata is not None:
        return normalizzata[1]
    if tempo_iso.rango_granularita(cluster.granularita) is None:
        return None
    return cluster.granularita


def chiave_collocazione(cluster: ClusterTemporaleProposto) -> Chiave | None:
    """L'identità del cluster: collocazione se c'è, etichetta altrimenti.

    None solo per un cluster senza `inizio` leggibile **e** senza etichetta,
    che non è identificabile in nessun modo e va scartato dal chiamante.
    """
    if not isinstance(cluster, ClusterTemporaleProposto):
        return None
    iso = chiave_iso(cluster)
    if iso is not None:
        return iso
    etichetta = etichetta_normalizzata(cluster.etichetta)
    if not etichetta:
        return None
    return (CHIAVE_ETICHETTA, etichetta, "")


def _rango_etichetta(etichetta: str) -> tuple[int, int, str]:
    """Ordine di preferenza fra etichette dello stesso cluster.

    Vince chi rispetta il limite del piano; a pari rispetto vince la **più
    informativa** (la più lunga entro i 40 caratteri), perché la prosa in
    eccesso finisce in `descrizione` e non serve accorciare oltre. Fra due
    etichette fuori limite vince la più corta, che è la più vicina al limite.
    L'ultimo criterio è alfabetico, così la scelta non dipende dall'ordine
    delle finestre.
    """
    lunghezza = len(etichetta)
    if lunghezza <= ETICHETTA_MAX_CHAR:
        return (0, -lunghezza, etichetta.casefold())
    return (1, lunghezza, etichetta.casefold())


def _etichette_fuse(prima: object, seconda: object) -> tuple[str, str | None]:
    """(etichetta canonica, etichetta scartata da promuovere a descrizione).

    La scartata viene restituita solo se è prosa — più lunga del limite del
    piano — perché è il caso misurato su `sole-e-vento`, dove il modello scrive
    "Prova del Vento: soffio violento e resistenza dell'uomo" come etichetta:
    buttarla perderebbe l'unica descrizione della scena, tenerla come etichetta
    romperebbe l'asse. Un sinonimo breve viene invece dimenticato: resta
    comunque nel vocabolario di `Riconciliazione.etichette`.
    """
    candidate = [testo for testo in (_testo(prima), _testo(seconda)) if testo]
    if not candidate:
        return "", None
    canonica = min(candidate, key=_rango_etichetta)
    scartate = [
        testo
        for testo in candidate
        if testo != canonica and len(testo) > ETICHETTA_MAX_CHAR
    ]
    return canonica, (min(scartate, key=_rango_etichetta) if scartate else None)


def fondi_cluster(
    existing: ClusterTemporaleProposto, proposed: ClusterTemporaleProposto
) -> ClusterTemporaleProposto:
    """Unione dei membri; per gli altri campi vince chi ha già un valore.

    Semantica di MT3, spostata qui perché è parte della riconciliazione: la
    prima finestra che dichiara `inizio`/`granularita`/`fine` fissa il valore e
    la seconda può solo riempire un buco; `stimato` cade a false appena una
    finestra ha visto la collocazione dichiarata nel testo; `confidenza` prende
    il **minimo**, perché raggruppare richiede confidenza e un dubbio in una
    finestra resta un dubbio.

    Le due aggiunte di MT4: l'etichetta canonica è scelta da `_etichette_fuse`
    invece di essere quella della prima finestra, e `padre` non viene deciso qui
    (`fondi_per_collocazione` lo sceglie fra tutti i candidati del gruppo, che è
    l'unico posto che li vede tutti).
    """
    etichetta, scartata = _etichette_fuse(existing.etichetta, proposed.etichetta)
    membri = list(existing.eventi or []) + list(proposed.eventi or [])
    visti: set[str] = set()
    eventi: list[str] = []
    for eid in membri:
        if isinstance(eid, str) and eid not in visti:
            visti.add(eid)
            eventi.append(eid)
    return existing.model_copy(
        update={
            "etichetta": etichetta or existing.etichetta,
            "eventi": eventi,
            "descrizione": existing.descrizione or proposed.descrizione or scartata,
            "granularita": existing.granularita or proposed.granularita,
            "inizio": existing.inizio or proposed.inizio,
            "fine": existing.fine or proposed.fine,
            "padre": existing.padre or proposed.padre,
            "stimato": existing.stimato and proposed.stimato,
            "confidenza": min(existing.confidenza, proposed.confidenza),
        }
    )


@dataclass(frozen=True)
class _Gruppo:
    """Un cluster in costruzione: il fuso, quando è apparso, come si chiama.

    `etichette` accumula tutti i nomi visti — canonici e alias scartati — perché
    sono il vocabolario con cui i `padre` delle altre finestre lo cercano.
    `padri` accumula tutti i padri dichiarati: la scelta fra loro è un problema
    di riconciliazione, non di fusione campo per campo.
    """

    cluster: ClusterTemporaleProposto
    posizione: int
    etichette: list[str] = field(default_factory=list)
    padri: list[str] = field(default_factory=list)


def _senza_doppioni(nomi: list[str]) -> list[str]:
    fuori: list[str] = []
    visti: set[str] = set()
    for nome in nomi:
        chiave = etichetta_normalizzata(nome)
        if not chiave or chiave in visti:
            continue
        visti.add(chiave)
        fuori.append(nome)
    return fuori


def _gruppo(proposed: ClusterTemporaleProposto, posizione: int) -> _Gruppo:
    return _Gruppo(
        cluster=proposed,
        posizione=posizione,
        etichette=_senza_doppioni([etichetta_normalizzata(proposed.etichetta)]),
        padri=_senza_doppioni([_testo(proposed.padre)]),
    )


def _unisci(primo: _Gruppo, secondo: _Gruppo) -> _Gruppo:
    """Fonde due gruppi rispettando l'ordine di apparizione.

    Chi è apparso prima fa da `existing` in `fondi_cluster`, così la regola di
    MT3 "vince chi ha già un valore" continua a significare "vince la finestra
    che l'ha visto per prima" anche quando la fusione salda un gruppo datato con
    uno che non lo era.
    """
    prima, seconda = (
        (primo, secondo) if primo.posizione <= secondo.posizione else (secondo, primo)
    )
    cluster = fondi_cluster(prima.cluster, seconda.cluster)
    return _Gruppo(
        cluster=cluster,
        posizione=prima.posizione,
        etichette=_senza_doppioni(
            prima.etichette
            + seconda.etichette
            + [etichetta_normalizzata(cluster.etichetta)]
        ),
        padri=_senza_doppioni(prima.padri + seconda.padri),
    )


def _fondi_per_iso(
    proposti: list[ClusterTemporaleProposto],
) -> tuple[list[_Gruppo], list[_Gruppo]]:
    """Gruppi datati (per collocazione) e gruppi non datati (per etichetta)."""
    datati: dict[Chiave, _Gruppo] = {}
    sciolti: dict[str, _Gruppo] = {}
    for posizione, proposed in enumerate(proposti):
        nuovo = _gruppo(proposed, posizione)
        iso = chiave_iso(proposed)
        if iso is not None:
            precedente = datati.get(iso)
            datati[iso] = nuovo if precedente is None else _unisci(precedente, nuovo)
            continue
        etichetta = etichetta_normalizzata(proposed.etichetta)
        if not etichetta:
            continue
        precedente = sciolti.get(etichetta)
        sciolti[etichetta] = (
            nuovo if precedente is None else _unisci(precedente, nuovo)
        )
    return list(datati.values()), list(sciolti.values())


def _salda_sciolti(datati: list[_Gruppo], sciolti: list[_Gruppo]) -> list[_Gruppo]:
    """Assorbe ogni gruppo non datato nel gruppo datato che ne porta l'etichetta.

    Solo quando il gruppo datato è **uno**: se due momenti diversi portano la
    stessa etichetta, un cluster senza `inizio` con quell'etichetta non dice a
    quale dei due appartiene, e inventare l'appartenenza è peggio che lasciarlo
    a sé. È la saldatura che conserva il comportamento utile della fusione per
    etichetta di MT3: una finestra dichiara "24 dic, sera" senza data, un'altra
    la stessa etichetta con `inizio`, e sono lo stesso cluster.
    """
    fuori = list(datati)
    for gruppo in sciolti:
        etichetta = gruppo.etichette[0] if gruppo.etichette else ""
        indici = [
            indice
            for indice, ospite in enumerate(fuori)
            if etichetta and etichetta in ospite.etichette
        ]
        if len(indici) != 1:
            fuori.append(gruppo)
            continue
        fuori[indici[0]] = _unisci(fuori[indici[0]], gruppo)
    return fuori


def _vocabolario(gruppi: list[_Gruppo]) -> dict[str, Chiave | None]:
    """Etichette e alias verso la chiave del gruppo, None se ambigue."""
    fuori: dict[str, Chiave | None] = {}
    for gruppo in gruppi:
        chiave = chiave_collocazione(gruppo.cluster)
        if chiave is None:
            continue
        for etichetta in gruppo.etichette:
            if etichetta in fuori and fuori[etichetta] != chiave:
                fuori[etichetta] = None
                continue
            fuori[etichetta] = chiave
    return fuori


def _rango_padre(
    candidato: str,
    figlio: ClusterTemporaleProposto,
    vocabolario: dict[str, Chiave | None],
    per_chiave: dict[Chiave, ClusterTemporaleProposto],
) -> tuple[int, int, str]:
    """Ordine di preferenza fra i padri dichiarati per uno stesso cluster fuso.

    Prima chi è davvero più grosso del figlio, poi chi non è verificabile, per
    ultimo chi è palesemente sbagliato; a pari verificabilità vince l'antenato
    **più prossimo** (la granularità più fine), perché gli antenati più grossi
    si ricavano risalendo `CONTIENE` dal più prossimo. L'ultimo criterio è
    alfabetico: la scelta non dipende dall'ordine delle finestre.
    """
    chiave = vocabolario.get(etichetta_normalizzata(candidato))
    padre = per_chiave.get(chiave) if chiave is not None else None
    rango_padre = (
        tempo_iso.rango_granularita(granularita_effettiva(padre))
        if padre is not None
        else None
    )
    rango_figlio = tempo_iso.rango_granularita(granularita_effettiva(figlio))
    if rango_padre is None or rango_figlio is None:
        verificabile = 1
    elif rango_padre > rango_figlio:
        verificabile = 0
    else:
        verificabile = 2
    return (
        verificabile,
        rango_padre if rango_padre is not None else len(tempo_iso.PRECISIONI_ISO) + 10,
        etichetta_normalizzata(candidato),
    )


def fondi_per_collocazione(proposti: object) -> Riconciliazione:
    """Fonde i cluster di più finestre per collocazione, non per etichetta.

    Tre passaggi: i cluster datati si fondono per `(inizio, granularita)`
    normalizzati; quelli non datati si fondono fra loro per etichetta; poi ogni
    gruppo non datato viene assorbito dal gruppo datato che porta la sua
    etichetta, se ce n'è esattamente uno. Il risultato ha **un cluster per
    collocazione**, con l'etichetta canonica scelta fra quelle viste e un solo
    `padre` scelto fra tutti quelli dichiarati.

    Idempotente: rifondere un risultato già fuso non cambia nulla. L'ordine di
    uscita è quello di prima apparizione, come in MT3; la forma della foresta
    non ne dipende (`costruisci_foresta` riordina).
    """
    datati, sciolti = _fondi_per_iso(_cluster_validi(proposti))
    gruppi = sorted(_salda_sciolti(datati, sciolti), key=lambda g: g.posizione)
    vocabolario = _vocabolario(gruppi)
    per_chiave: dict[Chiave, ClusterTemporaleProposto] = {}
    for gruppo in gruppi:
        chiave = chiave_collocazione(gruppo.cluster)
        if chiave is not None:
            per_chiave[chiave] = gruppo.cluster

    fuori: list[ClusterTemporaleProposto] = []
    for gruppo in gruppi:
        cluster = gruppo.cluster
        padre: str | None = None
        if len(gruppo.padri) == 1:
            padre = gruppo.padri[0]
        elif gruppo.padri:
            padre = min(
                gruppo.padri,
                key=lambda candidato: _rango_padre(
                    candidato, cluster, vocabolario, per_chiave
                ),
            )
        if padre != cluster.padre:
            cluster = cluster.model_copy(update={"padre": padre})
        fuori.append(cluster)
    return Riconciliazione(cluster=fuori, etichette=vocabolario)


def mappa_etichette(clusters: object) -> dict[str, Chiave | None]:
    """Etichetta normalizzata → chiave del cluster, None se ambigua.

    Versione per chi ha in mano una lista già fusa e non la `Riconciliazione`
    (i controlli di validità, MT5). Non conosce gli alias scartati.
    """
    fuori: dict[str, Chiave | None] = {}
    for cluster in _cluster_validi(clusters):
        etichetta = etichetta_normalizzata(cluster.etichetta)
        chiave = chiave_collocazione(cluster)
        if not etichetta or chiave is None:
            continue
        if etichetta in fuori and fuori[etichetta] != chiave:
            fuori[etichetta] = None
            continue
        fuori[etichetta] = chiave
    return fuori


def chiave_ordine_cluster(cluster: ClusterTemporaleProposto) -> int | None:
    """`chiave_ordine` del cluster, o None se non è collocato nel tempo.

    Derivata, non dichiarata: il modello non emette questo intero (MT1 ha
    tenuto il campo fuori dallo schema) e MT5 lo scrive sul nodo chiamando qui.
    None significa "nessuna collocazione": il fallback è la `posizione_doc`
    minima degli eventi contenuti, che questo modulo non vede (è MT5/MT8).
    """
    if not isinstance(cluster, ClusterTemporaleProposto):
        return None
    return tempo_iso.chiave_ordine(cluster.inizio, cluster.granularita)


def chiavi_ordine(clusters: object) -> dict[str, int | None]:
    """Etichetta → `chiave_ordine`, pronta da persistere in MT5."""
    return {
        _testo(cluster.etichetta): chiave_ordine_cluster(cluster)
        for cluster in _cluster_validi(clusters)
        if _testo(cluster.etichetta)
    }


def _ordinamento(cluster: ClusterTemporaleProposto) -> tuple[int, int, str, Chiave]:
    """Ordine canonico: prima i collocati per `chiave_ordine`, poi gli altri.

    A pari istante `tempo_iso.chiave_ordine` mette avanti la granularità più
    grossa, quindi un potenziale padre precede il suo potenziale figlio. È
    l'ordine su cui si costruisce la foresta, e non dipende dall'ordine delle
    finestre: è ciò che rende deterministica la rottura dei cicli.
    """
    ordine = chiave_ordine_cluster(cluster)
    chiave = chiave_collocazione(cluster) or ("", "", "")
    etichetta = etichetta_normalizzata(cluster.etichetta)
    if ordine is None:
        return (1, 0, etichetta, chiave)
    return (0, ordine, etichetta, chiave)


def _granularita_ammessa(
    padre: ClusterTemporaleProposto, figlio: ClusterTemporaleProposto
) -> bool:
    """Il padre è più grosso del figlio, o almeno non si può dire il contrario.

    Granularità uguali: arco scartato, il piano chiede granularità
    *strettamente* decrescente scendendo, e due cluster della stessa ampiezza
    sono fratelli o duplicati, non genitore e figlio. Granularità mancante da
    una delle due parti: arco accettato. È il caso dei cluster
    `relativo`/`simbolico`, che non hanno `inizio` da cui dedurla e sono
    proprio quelli che senza un contenitore restano illeggibili ("sette anni
    prima" di cosa?); scartarli azzererebbe l'annidamento che il piano chiede.
    Ciò che non si può verificare qui lo verifica la rottura dei cicli, che è
    l'unico danno che un arco non verificabile può fare.

    Il confronto è sulla granularità **effettiva** (`granularita_effettiva`),
    non su quella dichiarata, così concorda con il confronto fra le finestre.
    """
    rango_padre = tempo_iso.rango_granularita(granularita_effettiva(padre))
    rango_figlio = tempo_iso.rango_granularita(granularita_effettiva(figlio))
    if rango_padre is None or rango_figlio is None:
        return True
    return rango_padre > rango_figlio


def _finestra(cluster: ClusterTemporaleProposto) -> tuple[int, int] | None:
    """Intervallo occupato dal cluster, `fine` inclusa quando c'è.

    Un cluster di tipo `intervallo` porta l'ampiezza in `fine`, non nella
    granularità: senza tenerne conto un padre "1843-01 → 1843-12" con
    granularità `mese` sarebbe larga un mese e non conterrebbe nessuno dei suoi
    figli.
    """
    inizio = tempo_iso.bounds(cluster.inizio, cluster.granularita)
    if inizio is None:
        return None
    fine = tempo_iso.bounds(cluster.fine, cluster.granularita)
    return inizio[0], inizio[1] if fine is None else max(inizio[1], fine[1])


def _finestre_annidate(
    padre: ClusterTemporaleProposto, figlio: ClusterTemporaleProposto
) -> bool:
    """La finestra del figlio sta dentro quella del padre, se sono note entrambe.

    Un padre `1850` con figlio `1843-12` è un annidamento sbagliato anche se la
    granularità è più grossa, e con la granularità sola non lo si vede. Il
    controllo scatta **solo** quando entrambi i cluster hanno una collocazione
    leggibile: i cluster senza `inizio` non hanno finestra e restano annidabili,
    che è ciò che tiene `CONTIENE > 0` anche su un documento senza date. Il
    rischio di potare troppo è limitato dal fatto che la granularità del padre è
    già stata richiesta strettamente più grossa, quindi la sua finestra è larga
    almeno un'unità in più di quella del figlio.
    """
    finestra_padre = _finestra(padre)
    finestra_figlio = _finestra(figlio)
    if finestra_padre is None or finestra_figlio is None:
        return True
    return (
        finestra_padre[0] <= finestra_figlio[0]
        and finestra_figlio[1] <= finestra_padre[1]
    )


def _chiude_ciclo(
    padre_di: dict[Chiave, Chiave], figlio: Chiave, padre: Chiave
) -> bool:
    """True se appendere `figlio` sotto `padre` creerebbe un ciclo.

    Risale la foresta costruita finora: se da `padre` si arriva a `figlio`,
    l'arco chiuderebbe l'anello. Il conteggio dei passi è un'ancora di
    sicurezza, non la condizione d'uscita: `padre_di` è già aciclico per
    costruzione, quindi la risalita finisce sempre su una radice.
    """
    corrente: Chiave | None = padre
    for _ in range(len(padre_di) + 2):
        if corrente is None:
            return False
        if corrente == figlio:
            return True
        corrente = padre_di.get(corrente)
    return True


def _etichette_univoche(ordinati: list[ClusterTemporaleProposto]) -> dict[Chiave, str]:
    """Un'etichetta distinta per cluster, disambiguando i doppioni.

    Due momenti diversi possono portare la stessa etichetta ("sera" in due
    giorni diversi): sono cluster distinti e la fusione per collocazione li
    tiene giustamente separati, ma l'etichetta è il solo appiglio con cui MT5
    costruisce l'id del nodo e con cui questo modulo scrive `padre`. Il primo in
    ordine canonico tiene l'etichetta nuda, gli altri si portano dietro la
    collocazione (`"sera (1844-12-24T18)"`) o, se non ne hanno una, un ordinale.
    Il limite dei 40 caratteri cede: meglio un'etichetta lunga che due cluster
    che MT5 fonderebbe in un nodo solo.
    """
    nomi: dict[Chiave, str] = {}
    prese: set[str] = set()
    for posizione, cluster in enumerate(ordinati, start=1):
        chiave = chiave_collocazione(cluster)
        if chiave is None:
            continue
        base = _testo(cluster.etichetta)
        candidato = base
        if etichetta_normalizzata(candidato) in prese:
            coda = chiave[1] if chiave[0] == CHIAVE_ISO else str(posizione)
            candidato = f"{base} ({coda})"
        contatore = 2
        while etichetta_normalizzata(candidato) in prese:
            candidato = f"{base} ({contatore})"
            contatore += 1
        prese.add(etichetta_normalizzata(candidato))
        nomi[chiave] = candidato
    return nomi


def costruisci_foresta(clusters: object) -> list[ClusterTemporaleProposto]:
    """Fonde per collocazione e riduce i `padre` dichiarati a una foresta.

    In uscita: un cluster per collocazione, etichette distinte, e il campo
    `padre` che vale un arco `CONTIENE` valido — al più un padre per cluster
    (il campo è singolo), nessun ciclo, granularità del padre strettamente più
    grossa dove entrambe sono note, finestra del padre che contiene quella del
    figlio dove entrambe sono leggibili. I riferimenti `padre` che non
    sopravvivono a questi controlli vengono azzerati: **il cluster resta**, come
    radice, perché un annidamento sbagliato non rende falso il cluster.

    L'ordine di uscita è quello canonico di `chiave_ordine` (padri prima dei
    figli), quindi non dipende dall'ordine delle finestre; la forma della
    foresta nemmeno.
    """
    riconciliazione = fondi_per_collocazione(clusters)
    validi = [
        cluster
        for cluster in riconciliazione.cluster
        if _testo(cluster.etichetta) and chiave_collocazione(cluster) is not None
    ]
    if not validi:
        return []
    per_chiave = {chiave_collocazione(c): c for c in validi}
    ordinati = sorted(validi, key=_ordinamento)
    nomi = _etichette_univoche(ordinati)

    padre_di: dict[Chiave, Chiave] = {}
    for cluster in ordinati:
        chiave = chiave_collocazione(cluster)
        riferimento = etichetta_normalizzata(cluster.padre)
        if chiave is None or not riferimento:
            continue
        padre_chiave = riconciliazione.etichette.get(riferimento)
        if padre_chiave is None or padre_chiave == chiave:
            continue
        padre = per_chiave.get(padre_chiave)
        if padre is None:
            continue
        if not _granularita_ammessa(padre, cluster):
            continue
        if not _finestre_annidate(padre, cluster):
            continue
        if _chiude_ciclo(padre_di, chiave, padre_chiave):
            continue
        padre_di[chiave] = padre_chiave

    fuori: list[ClusterTemporaleProposto] = []
    for cluster in ordinati:
        chiave = chiave_collocazione(cluster)
        if chiave is None:
            continue
        padre_chiave = padre_di.get(chiave)
        fuori.append(
            cluster.model_copy(
                update={
                    "etichetta": nomi.get(chiave, _testo(cluster.etichetta)),
                    "padre": None if padre_chiave is None else nomi.get(padre_chiave),
                }
            )
        )
    return fuori


def scarta_contenitori_sterili(clusters: object) -> list[ClusterTemporaleProposto]:
    """Toglie i cluster senza eventi propri che non contengono più nulla.

    Il gate di confidenza di MT3 tiene in vita un contenitore puro perché è il
    `padre` di un cluster vivo. Se poi la foresta scarta quell'arco — padre di
    granularità sbagliata, finestra incompatibile, ciclo — il contenitore resta
    senza eventi e senza figli: un box vuoto nella vista, non un fatto. Qui
    sparisce, a punto fisso, perché un contenitore può reggerne un altro.

    Non risuscita nulla: un cluster con eventi propri passa sempre, un cluster
    già svuotato dal gate passa solo se un discendente vivo lo tiene su. È lo
    stesso invariante di `_cluster_sanificati`, ricalcolato sugli archi veri.
    """
    validi = [c for c in _cluster_validi(clusters) if _testo(c.etichetta)]
    per_etichetta = {etichetta_normalizzata(c.etichetta): c for c in validi}
    vivi = {
        etichetta_normalizzata(c.etichetta)
        for c in validi
        if [eid for eid in (c.eventi or []) if isinstance(eid, str) and eid]
    }
    while True:
        aggiunti = {
            padre
            for etichetta in vivi
            if (padre := etichetta_normalizzata(per_etichetta[etichetta].padre))
            and padre in per_etichetta
            and padre not in vivi
        }
        if not aggiunti:
            break
        vivi |= aggiunti

    fuori: list[ClusterTemporaleProposto] = []
    for cluster in validi:
        if etichetta_normalizzata(cluster.etichetta) not in vivi:
            continue
        padre = etichetta_normalizzata(cluster.padre)
        if padre and padre not in vivi:
            cluster = cluster.model_copy(update={"padre": None})
        fuori.append(cluster)
    return fuori


def archi_contiene(clusters: object) -> list[tuple[str, str]]:
    """Gli archi `(padre, figlio)` da persistere, per etichetta.

    L'input atteso è l'uscita di `costruisci_foresta`: qui non si valida più
    nulla, si legge il campo `padre` e si scartano solo i riferimenti che non
    puntano a un cluster presente. MT5 traduce le etichette in id.
    """
    validi = [c for c in _cluster_validi(clusters) if _testo(c.etichetta)]
    presenti = {
        etichetta_normalizzata(c.etichetta): _testo(c.etichetta) for c in validi
    }
    fuori: list[tuple[str, str]] = []
    for cluster in sorted(validi, key=_ordinamento):
        padre = etichetta_normalizzata(cluster.padre)
        if not padre or padre not in presenti:
            continue
        fuori.append((presenti[padre], _testo(cluster.etichetta)))
    return fuori


def violazioni_foresta(clusters: object) -> list[str]:
    """Le violazioni della foresta, vuota se l'insieme è una foresta valida.

    Serve ai test e al criterio di accettazione di MT10 ("nessun ciclo, ogni
    cluster con ≤ 1 padre, granularità decrescente scendendo"): è un controllo
    indipendente dalla costruzione, quindi non ripete gli stessi errori. Il
    vincolo "≤ 1 padre" non ha una riga sua perché lo garantisce lo schema:
    `padre` è un campo singolo; quello che va verificato è che sia risolvibile,
    e questa funzione lo verifica.
    """
    validi = [c for c in _cluster_validi(clusters) if _testo(c.etichetta)]
    fuori: list[str] = []
    if len(_cluster_validi(clusters)) != len(validi):
        fuori.append("cluster senza etichetta")
    per_etichetta: dict[str, ClusterTemporaleProposto] = {}
    for cluster in validi:
        etichetta = etichetta_normalizzata(cluster.etichetta)
        if etichetta in per_etichetta:
            fuori.append(f"etichetta duplicata: {etichetta}")
            continue
        per_etichetta[etichetta] = cluster

    for etichetta, cluster in per_etichetta.items():
        padre_nome = etichetta_normalizzata(cluster.padre)
        if not padre_nome:
            continue
        if padre_nome == etichetta:
            fuori.append(f"padre di se stesso: {etichetta}")
            continue
        padre = per_etichetta.get(padre_nome)
        if padre is None:
            fuori.append(f"padre irrisolvibile: {etichetta} -> {padre_nome}")
            continue
        if not _granularita_ammessa(padre, cluster):
            fuori.append(f"granularita non decrescente: {padre_nome} -> {etichetta}")
        if not _finestre_annidate(padre, cluster):
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


__all__ = [
    "CHIAVE_ETICHETTA",
    "CHIAVE_ISO",
    "ETICHETTA_MAX_CHAR",
    "Chiave",
    "Riconciliazione",
    "archi_contiene",
    "chiave_collocazione",
    "chiave_iso",
    "chiave_ordine_cluster",
    "chiavi_ordine",
    "costruisci_foresta",
    "etichetta_normalizzata",
    "granularita_effettiva",
    "fondi_cluster",
    "fondi_per_collocazione",
    "mappa_etichette",
    "scarta_contenitori_sterili",
    "violazioni_foresta",
]
