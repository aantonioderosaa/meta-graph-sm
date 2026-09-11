"""Parte B / MT2 — ISO 8601 a precisione variabile, funzioni pure.

Il livello temporale (`PIANO-LIVELLO-TEMPORALE-V2.md`) posiziona ogni evento su
un asse che va dai secondi ai secoli. Questo modulo è l'unico posto dove si
decide *cosa significa* una collocazione temporale: parsing e validazione delle
sei precisioni ISO ammesse, la `chiave_ordine` monotona su cui il frontend
ordina l'asse, e i `bounds` interi che alimentano la rete di Allen.

Nessun I/O, nessun LLM, nessun Neo4j. Isolamento D6: si importa solo
`app.models.event_graph`, mai `app.core`.

Il livello temporale è best-effort: **nessuna funzione qui solleva eccezioni**.
Un input che non è una collocazione temporale vale `None`, e sta al chiamante
decidere se degradare o ignorare.

Convenzioni, in un posto solo perché MT3+ ci si appoggia:

* **Scala.** Tutti gli interi restituiti da `chiave_ordine` e da `bounds` sono
  sedicesimi di secondo trascorsi dall'epoca `0001-01-01T00:00:00` del
  calendario gregoriano prolettico. Il fattore `SCALA_CHIAVE = 16` non serve a
  rappresentare frazioni di secondo (non ne rappresentiamo): serve a lasciare
  nei bit bassi lo spazio per il tie-break di granularità, in modo che chiave e
  bounds restino confrontabili fra loro. Vale sempre
  `bounds()[0] <= chiave_ordine() < bounds()[1]`.
* **Tie-break.** A pari istante d'inizio, la granularità più grossa ordina
  prima: un cluster "anno 1843" precede il suo "24 dicembre 1843", cioè il
  potenziale padre viene prima del potenziale figlio. Il rango è impacchettato
  nei bit bassi (0 per `secolo`, 9 per `secondo`), quindi l'ordine è totale e
  deterministico senza chiavi secondarie.
* **Stringa contro granularità dichiarata.** L'inizio non si inventa e
  l'ampiezza non si restringe: l'istante d'inizio viene *sempre* dalla stringa
  (`"1843"` inizia il 1° gennaio), mentre l'ampiezza dell'intervallo viene
  dalla **più grossa** fra la precisione della stringa e la `granularita`
  dichiarata. Così `("1843", "giorno")` resta larga un anno — il giorno non lo
  sappiamo — e `("1843-12-24T18:30", "anno")` si legge come "circa un anno a
  partire da quel minuto", che è l'unica lettura che non sposta l'inizio.
* **Fuori scala.** Anni ammessi 0001–9999; `"0000"` e le date fuori calendario
  (`"1843-02-30"`) non sono collocazioni. La fine di un intervallo che
  sfonderebbe il 9999 viene tagliata all'ultimo istante rappresentabile.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Final

from app.models.event_graph import GRANULARITA_TEMPORALI, GranularitaTemporale

SCALA_CHIAVE: Final[int] = 16
"""Sedicesimi di secondo per secondo: i bit bassi ospitano il tie-break."""

PRECISIONI_ISO: Final[tuple[GranularitaTemporale, ...]] = (
    "anno",
    "mese",
    "giorno",
    "ora",
    "minuto",
    "secondo",
)
"""Le sei precisioni che una stringa ISO può dichiarare, dalla più grossa."""

_SECONDI_AL_GIORNO: Final[int] = 86_400

# La stringa ISO a precisione variabile, con le tolleranze che un modello
# locale si prende: separatore 't' minuscolo o spazio, frazioni di secondo,
# suffisso di fuso. Frazioni e fuso vengono accettati e *ignorati* (vedi
# `analizza`), non rifiutati: scartare l'intera collocazione per un 'Z' di
# troppo sarebbe il contrario di best-effort.
_ISO_VARIABILE: Final[re.Pattern[str]] = re.compile(
    r"""^
    (?P<anno>\d{4})
    (?:
        -(?P<mese>\d{2})
        (?:
            -(?P<giorno>\d{2})
            (?:
                [Tt\ ](?P<ora>\d{2})
                (?:
                    :(?P<minuto>\d{2})
                    (?:
                        :(?P<secondo>\d{2})
                        (?:\.\d+)?
                    )?
                )?
                (?:[Zz]|[+-]\d{2}:?\d{2})?
            )?
        )?
    )?
    $""",
    re.VERBOSE,
)


@dataclass(frozen=True)
class TempoISO:
    """Una stringa ISO valida, scomposta e già collocata sull'asse.

    `secondi` è l'inizio dell'unità denotata dalla stringa, in secondi interi
    dall'epoca: `"1843"` vale il 1° gennaio 1843 a mezzanotte, non "un momento
    imprecisato del 1843". `canonico` è la stessa collocazione riscritta nella
    forma normale della sua precisione, ed è la metà testuale della chiave di
    identità che `normalizza` restituisce.
    """

    anno: int
    mese: int
    giorno: int
    ora: int
    minuto: int
    secondo: int
    precisione: GranularitaTemporale
    canonico: str
    secondi: int


def _secondi_da_epoca(
    anno: int, mese: int, giorno: int, ora: int, minuto: int, secondo: int
) -> int | None:
    """Secondi interi dall'epoca `0001-01-01T00:00:00`, o None se fuori calendario."""
    if not 1 <= anno <= 9999 or not 1 <= mese <= 12:
        return None
    if not 1 <= giorno <= calendar.monthrange(anno, mese)[1]:
        return None
    if not (0 <= ora <= 23 and 0 <= minuto <= 59 and 0 <= secondo <= 59):
        return None
    giorni = date(anno, mese, giorno).toordinal() - 1
    return giorni * _SECONDI_AL_GIORNO + ora * 3600 + minuto * 60 + secondo


_SECONDI_MAX: Final[int] = (
    date(9999, 12, 31).toordinal() * _SECONDI_AL_GIORNO
)
"""Fine esclusiva dell'asse rappresentabile: mezzanotte dopo il 9999-12-31."""


def analizza(valore: object) -> TempoISO | None:
    """Legge una collocazione ISO a precisione variabile.

    Restituisce `None` — mai un'eccezione — per tutto ciò che non è una data:
    `None`, stringa vuota, `"ieri"`, `"18:30"`, `"1843-13-45"`, un intero. Le
    frazioni di secondo e il suffisso di fuso vengono accettati e scartati: il
    tempo del livello temporale è tempo narrativo da orologio a muro, non un
    istante fisico da normalizzare a UTC.
    """
    if not isinstance(valore, str):
        return None
    match = _ISO_VARIABILE.match(valore.strip())
    if match is None:
        return None

    parti = match.groupdict()
    trovata: GranularitaTemporale = "anno"
    for chiave in ("mese", "giorno", "ora", "minuto", "secondo"):
        if parti[chiave] is None:
            break
        trovata = chiave  # type: ignore[assignment]

    anno = int(parti["anno"])
    mese = int(parti["mese"] or 1)
    giorno = int(parti["giorno"] or 1)
    ora = int(parti["ora"] or 0)
    minuto = int(parti["minuto"] or 0)
    secondo = int(parti["secondo"] or 0)

    secondi = _secondi_da_epoca(anno, mese, giorno, ora, minuto, secondo)
    if secondi is None:
        return None

    return TempoISO(
        anno=anno,
        mese=mese,
        giorno=giorno,
        ora=ora,
        minuto=minuto,
        secondo=secondo,
        precisione=trovata,
        canonico=_canonico(anno, mese, giorno, ora, minuto, secondo, trovata),
        secondi=secondi,
    )


def precisione(valore: object) -> GranularitaTemporale | None:
    """La granularità che la stringa dichiara da sé, o None se non è una data.

    Serve a MT3/MT4 per dedurre `granularita` quando l'LLM non la emette: una
    stringa `"1843-12-24T18:30"` vale `minuto` anche senza dichiarazione.
    """
    tempo = analizza(valore)
    return None if tempo is None else tempo.precisione


def rango_granularita(granularita: object) -> int | None:
    """Posizione in `GRANULARITA_TEMPORALI`: 0 = `secondo`, 9 = `secolo`.

    Confronto grezzo fra granularità: più alto è più grosso. I sinonimi
    ("years", "giorni", "date") li normalizzano già i validator dei modelli;
    qui si accetta solo un membro esatto dell'enum, a meno di spazi e
    maiuscole, per non tenere due vocabolari da riallineare.
    """
    normalizzata = _granularita_valida(granularita)
    if normalizzata is None:
        return None
    return GRANULARITA_TEMPORALI.index(normalizzata)


def piu_grossa(prima: object, seconda: object) -> GranularitaTemporale | None:
    """La più grossa fra due granularità, ignorando quella che non è valida.

    MT4 la usa per il vincolo della foresta (il padre è sempre più grosso del
    figlio); qui serve a scegliere l'ampiezza di `bounds`.
    """
    rango_prima = rango_granularita(prima)
    rango_seconda = rango_granularita(seconda)
    if rango_prima is None:
        return _granularita_valida(seconda)
    if rango_seconda is None:
        return _granularita_valida(prima)
    return GRANULARITA_TEMPORALI[max(rango_prima, rango_seconda)]


def chiave_ordine(inizio: object, granularita: object = None) -> int | None:
    """Intero monotono su cui ordinare l'asse temporale, o None se non collocabile.

    Ordinare per `chiave_ordine` dà lo stesso ordine che ordinare per istante
    d'inizio; a pari istante ordina prima la granularità più grossa, così un
    cluster contenitore precede il suo contenuto. Non dipende dall'ampiezza:
    `"1843"` viene prima di `"1843-12-24T18:30"` perché inizia prima.
    """
    tempo = analizza(inizio)
    if tempo is None:
        return None
    effettiva = _granularita_valida(granularita) or tempo.precisione
    return tempo.secondi * SCALA_CHIAVE + _tiebreak(effettiva)


def bounds(inizio: object, granularita: object = None) -> tuple[int, int] | None:
    """Intervallo `[inizio, fine)` sulla scala di `chiave_ordine`, per Allen.

    Semi-aperto di proposito: due unità adiacenti soddisfano `a_fine ==
    b_inizio`, che è esattamente il `meets` di Allen. (`temporal_placement.
    _expand_bounds` resta invece su bound chiusi con la fine a `:59`, per
    retrocompatibilità: le due convenzioni non vanno mescolate.) L'ampiezza è
    quella della granularità più grossa fra la stringa e la dichiarazione.
    """
    tempo = analizza(inizio)
    if tempo is None:
        return None
    effettiva = piu_grossa(tempo.precisione, granularita) or tempo.precisione
    fine = min(_fine_esclusiva(tempo, effettiva), _SECONDI_MAX)
    if fine <= tempo.secondi:
        fine = min(tempo.secondi + 1, _SECONDI_MAX)
    return tempo.secondi * SCALA_CHIAVE, fine * SCALA_CHIAVE


def normalizza(
    inizio: object, granularita: object = None
) -> tuple[str, GranularitaTemporale] | None:
    """Chiave di identità stabile `(canonico, granularità effettiva)`, o None.

    È la chiave con cui MT4 fonde i cluster fra finestre invece di confrontare
    le etichette: due proposte della stessa collocazione producono la stessa
    coppia anche se una scrive `"1843-12-24 18:30"` e l'altra
    `" 1843-12-24T18:30Z"`. Non tronca alla granularità dichiarata, quindi
    fonde solo riformulazioni della stessa stringa, non collocazioni diverse
    che capitano nella stessa unità.
    """
    tempo = analizza(inizio)
    if tempo is None:
        return None
    effettiva = piu_grossa(tempo.precisione, granularita) or tempo.precisione
    return tempo.canonico, effettiva


def _granularita_valida(valore: object) -> GranularitaTemporale | None:
    if not isinstance(valore, str):
        return None
    token = valore.strip().casefold()
    if token in GRANULARITA_TEMPORALI:
        return token  # type: ignore[return-value]
    return None


def _tiebreak(granularita: GranularitaTemporale) -> int:
    """0 per `secolo`, 9 per `secondo`: il più grosso ordina prima."""
    return len(GRANULARITA_TEMPORALI) - 1 - GRANULARITA_TEMPORALI.index(granularita)


def _canonico(
    anno: int,
    mese: int,
    giorno: int,
    ora: int,
    minuto: int,
    secondo: int,
    precisione_: GranularitaTemporale,
) -> str:
    testo = f"{anno:04d}"
    if precisione_ == "anno":
        return testo
    testo += f"-{mese:02d}"
    if precisione_ == "mese":
        return testo
    testo += f"-{giorno:02d}"
    if precisione_ == "giorno":
        return testo
    testo += f"T{ora:02d}"
    if precisione_ == "ora":
        return testo
    testo += f":{minuto:02d}"
    if precisione_ == "minuto":
        return testo
    return testo + f":{secondo:02d}"


def _fine_esclusiva(tempo: TempoISO, granularita: GranularitaTemporale) -> int:
    """Fine esclusiva, in secondi dall'epoca, di un'unità che parte da `tempo`.

    `stagione` non ha una rappresentazione ISO: la trattiamo come un trimestre
    che parte dall'inizio dichiarato, non come una stagione meteorologica a
    date fisse. Ancorare all'inizio funziona in entrambi gli emisferi e non
    sposta la collocazione che il modello ha dichiarato; chi vuole l'inverno
    boreale scrive `inizio="1843-12"` con `granularita="stagione"` e ottiene
    dicembre–febbraio.
    """
    if granularita == "secondo":
        return tempo.secondi + 1
    if granularita == "minuto":
        return tempo.secondi + 60
    if granularita == "ora":
        return tempo.secondi + 3600
    if granularita == "giorno":
        return tempo.secondi + _SECONDI_AL_GIORNO
    if granularita == "settimana":
        return tempo.secondi + 7 * _SECONDI_AL_GIORNO
    mesi = {"mese": 1, "stagione": 3, "anno": 12, "decennio": 120, "secolo": 1200}
    return _piu_mesi(tempo, mesi[granularita])


def _piu_mesi(tempo: TempoISO, quanti: int) -> int:
    """`tempo` spostato di `quanti` mesi, con il giorno tagliato al mese corto."""
    totale = tempo.anno * 12 + (tempo.mese - 1) + quanti
    anno, indice = divmod(totale, 12)
    mese = indice + 1
    if anno > 9999:
        return _SECONDI_MAX
    giorno = min(tempo.giorno, calendar.monthrange(anno, mese)[1])
    spostato = _secondi_da_epoca(
        anno, mese, giorno, tempo.ora, tempo.minuto, tempo.secondo
    )
    return _SECONDI_MAX if spostato is None else spostato


__all__ = [
    "PRECISIONI_ISO",
    "SCALA_CHIAVE",
    "TempoISO",
    "analizza",
    "bounds",
    "chiave_ordine",
    "normalizza",
    "piu_grossa",
    "precisione",
    "rango_granularita",
]
