"""Pydantic contracts for the isolated event-graph pipeline (piano sez. 7, 15.4).

This module must not import ``app.core.*`` or ``app.pipeline.*``.
``RULESET_VERSION`` lives only in ``app.pipeline.event_graph``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

_T = TypeVar("_T")

RuoloArgomentale = Literal["SOGG", "OGG", "OBL", "TEMPO", "LUOGO", "MODO"]
NumeroMenzione = Literal["sing", "plur", "ignoto"]
GenereMenzione = Literal["masc", "femm", "ignoto"]
TipoSuperficiale = Literal["nome_proprio", "pronome", "sn_comune", "sogg_nullo"]
TempoVerbale = Literal[
    "presente", "imperfetto", "passato", "futuro", "non_finito", "trapassato"
]
BasePrecede = Literal[
    "connettivo", "dato_esplicito", "riferimento_testuale", "trapassato"
]
Modalita = Literal["fattuale", "ipotetico", "volitivo", "deontico"]
Segmentazione = Literal[
    "principale_finita",
    "subordinata_finita",
    "coordinata_finita",
    "infinitiva",
    "gerundiva",
    "participiale",
    "nominale",
    "implicita",
]
RuoloSe = Literal["nessuno", "antecedente", "conseguente"]
ClasseVerboReggente = Literal["nessuna", "fattivo", "non_fattivo"]
FraseTipo = Literal["dichiarativa", "interrogativa", "imperativa"]
SoggSpeciale = Literal["nessuno", "IGNOTO", "NON_APPLICABILE"]
RelazioneSegnale = Literal[
    "causa_esplicita",
    "consecuzione",
    "posteriorita",
    "anteriorita",
    "limite",
    "condizione",
    "scopo",
    "concessione",
    "contrasto",
    "asindeto_sequenziale",
    "temporale_ambiguo",
    "gerundio",
    "participio_assoluto",
    "apposizione_relativa",
    "due_punti_esplicativo",
    "nessuno",
]
OrientamentoArco = Literal[
    "subordinata_principale",
    "principale_subordinata",
    "coordinata",
]
Fattualita = Literal["FATTUALE", "NON_FATTUALE", "IPOTETICO"]
PianoNarrativo = Literal["PRIMO_PIANO", "SFONDO", "FUORI_LINEA"]
TipoRelazione = Literal[
    "SOGG",
    "OGG",
    "OBL",
    "TEMPO",
    "LUOGO",
    "MODO",
    "CAUSA",
    "PRECEDE",
    "LIMITE",
    "CONDIZIONE",
    "SCOPO",
    "CONCESSIONE",
    "CONTRASTO",
    "SEQUENZA",
    "CONTENUTO",
    "COLLEGATO",
    "SATELLITE_DI",
]
CatenaTipo = Literal["STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"]
TraversalKind = Literal[
    "catena_di",
    "spina_dorsale_di",
    "prima_di",
    "dopo_di",
    "vicinato_temporale",
]


class ArgomentoGrezzo(BaseModel):
    ruolo: RuoloArgomentale
    preposizione: str | None = None
    forma: str
    numero: NumeroMenzione = "ignoto"
    genere: GenereMenzione = "ignoto"
    tipo_superficiale: TipoSuperficiale
    span: str


class EventoGrezzo(BaseModel):
    indice: int
    lemma: str
    span: str
    tempo: TempoVerbale
    segmentazione: Segmentazione
    polarita_negata: bool
    modalizzato: bool
    modalizzato_forma: str | None = None
    iterativo: bool
    ruolo_se: RuoloSe
    completiva_di: int | None = None
    classe_verbo_reggente: ClasseVerboReggente
    finale: bool
    frase_tipo: FraseTipo
    marca_dialogo: bool
    frase_indice: int
    avverbio_temporale_esplicito: bool
    connettivo_sequenziale_esplicito: bool
    tempo_assoluto_grezzo: str | None = None
    argomenti: list[ArgomentoGrezzo]
    sogg_speciale: SoggSpeciale = "nessuno"
    e_testa: bool = False
    modalita: Modalita = "fattuale"
    offset_inizio: int | None = None
    offset_fine: int | None = None


class ArcoEventoGrezzo(BaseModel):
    da_indice: int
    a_indice: int
    segnale_testuale: str
    relazione_segnale: RelazioneSegnale
    orientamento: OrientamentoArco


class PredicatoNonFinito(BaseModel):
    """Free non-finite predicate: never an :Evento node (Parte A / A3–A4)."""

    lemma: str
    span: str
    forma_verbale: Literal["infinito", "gerundio", "participio"]
    relazione_segnale: RelazioneSegnale
    governo_indice: int | None = None
    argomento_condiviso: str | None = None


class FrammentoQuarantena(BaseModel):
    frammento: str
    motivo: str
    span: str


class EventEntityParticipation(BaseModel):
    """One independent-clause event and the entities that participated in it.

    Shape of the legacy ``node_extraction.extract_event_entities`` output
    (Addendum 4): no grammatical decomposition, just event-as-sentence +
    participant names.
    """

    event: str
    entities: list[str] = Field(default_factory=list)


class EventEntityExtractionResult(BaseModel):
    participations: list[EventEntityParticipation] = Field(default_factory=list)


class ChunkFactsheet(BaseModel):
    eventi: list[EventoGrezzo]
    archi: list[ArcoEventoGrezzo]
    quarantena: list[FrammentoQuarantena]
    predicati_non_finiti: list[PredicatoNonFinito] = Field(default_factory=list)


class FraseFactsheet(BaseModel):
    """One-sentence MICRO stage-1 factsheet (Addendum 2). Intra-sentence only."""

    eventi: list[EventoGrezzo] = Field(default_factory=list)
    archi: list[ArcoEventoGrezzo] = Field(default_factory=list)
    quarantena: list[FrammentoQuarantena] = Field(default_factory=list)
    predicati_non_finiti: list[PredicatoNonFinito] = Field(default_factory=list)


class FinestraTempoAssoluto(BaseModel):
    da: str | None = None
    a: str | None = None


class RispostaSintetizzata(BaseModel):
    """Answer synthesized from retrieved events only (Addendum 5)."""

    risposta: str
    eventi_citati: list[str] = Field(default_factory=list)


class EventQuerySpec(BaseModel):
    lemma: str | None = None
    testo: str | None = None
    piano: PianoNarrativo | None = None
    fattualita: Fattualita | None = None
    tempo: TempoVerbale | None = None
    fonte: str | None = None
    tipo_relazione: TipoRelazione | None = None
    finestra_tempo_assoluto: FinestraTempoAssoluto | None = None
    documento: str | None = None
    traversal: TraversalKind | None = None
    traversal_target: str | None = None


class ArgomentoRisolto(BaseModel):
    ruolo: RuoloArgomentale
    menzione_id: str | None = None
    preposizione: str | None = None


class EventoRisolto(BaseModel):
    """Resolved :Evento fields known at write time; later MTs fill the rest."""

    id: str = ""
    lemma: str = ""
    tempo: TempoVerbale | None = None
    polarita: str | None = None
    polarita_negata: bool = False
    modalizzato: bool = False
    modalizzato_forma: str | None = None
    modalita: Modalita = "fattuale"
    e_testa: bool = False
    offset_inizio: int | None = None
    offset_fine: int | None = None
    iterativita: bool = False
    iterativo: bool = False
    fattualita: Fattualita | None = None
    piano: PianoNarrativo | None = None
    fonte: str | None = None
    documento: str | None = None
    chunk_id: str | None = None
    indice_chunk: int | None = None
    posizione_doc: int | None = None
    posizione_chunk: int | None = None
    ancora: str | None = None
    segmentazione: Segmentazione | None = None
    contenuto_di: str | None = None
    sogg_speciale: SoggSpeciale = "nessuno"
    fuso_in: str | None = None
    catena_id: str | None = None
    catena_ruolo: CatenaTipo | None = None
    catena_precedente_id: str | None = None
    catena_divergenze: list[str] = Field(default_factory=list)
    tempo_assoluto: str | dict[str, Any] | None = None
    tempo_assoluto_revisioni: list[Any] = Field(default_factory=list)
    tempo_assoluto_grezzo: str | None = None
    regola: str | None = None
    versione_regole: str | None = None
    created_at: str | None = None
    argomenti: list[ArgomentoRisolto] = Field(default_factory=list)
    ruolo_se: RuoloSe = "nessuno"
    completiva_di: int | None = None
    indice_grezzo: int | None = None
    classe_verbo_reggente: ClasseVerboReggente = "nessuna"
    finale: bool = False
    frase_tipo: FraseTipo = "dichiarativa"
    marca_dialogo: bool = False
    frase_indice: int | None = None
    avverbio_temporale_esplicito: bool = False
    connettivo_sequenziale_esplicito: bool = False
    span: str | None = None


class MenzioneRisolta(BaseModel):
    id: str = ""
    forma: str = ""
    forma_canonica: str = ""
    numero: NumeroMenzione = "ignoto"
    genere: GenereMenzione = "ignoto"
    tipo_superficiale: TipoSuperficiale | None = None
    non_risolto: bool = False
    documento: str | None = None
    chunk_id: str | None = None
    regola: str | None = None
    versione_regole: str | None = None


class ArcoEvento(BaseModel):
    tipo: TipoRelazione | str
    da_id: str
    a_id: str
    props: dict[str, Any] = Field(default_factory=dict)


class QuarantenaItem(BaseModel):
    id: str = ""
    frammento: str = ""
    motivo: str = ""
    ancora_doc: str | None = None
    ancora_chunk: str | None = None
    ancora_span: str | None = None
    versione_regole: str | None = None
    created_at: str | None = None


class ZonaSummary(BaseModel):
    """M1 structured summary of one lexical zone. No inference beyond the text."""

    riassunto: str = Field(
        default="",
        description=(
            "2–4 sentences / 2–4 frasi stating only what the zone text says. "
            "No inference beyond the zone."
        ),
    )
    entita_principali: list[str] = Field(
        default_factory=list,
        description=(
            "Referential names exactly as they appear in the zone text "
            "(persone, luoghi, organizzazioni). Do not invent."
        ),
    )
    ancore_temporali: list[str] = Field(
        default_factory=list,
        description=(
            "Dates or temporal expressions as they appear (ieri, nel 1994, alle tre). "
            "May be empty."
        ),
    )
    evento_centrale: str | None = Field(
        default=None,
        description="Lemma or short span of the central event, or null if none.",
    )


class ZonaEdgeDecision(BaseModel):
    """M2 structured decision for one pair of zone summaries (not raw text)."""

    relazione_segnale: RelazioneSegnale = Field(
        description=(
            "Closed-vocab signal, same inventory as micro event-event edges. "
            "Do not invent a parallel type system."
        ),
    )
    orientamento: OrientamentoArco = Field(
        default="coordinata",
        description=(
            "Arc orientation for verso mapping (same as ArcoEventoGrezzo). "
            "Pair order is document order: left zone then right zone."
        ),
    )
    confidenza: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Mandatory confidence in [0, 1] for the implicit-classifier path. "
            "Also recorded when a boundary connective is disambiguated."
        ),
    )


class PairEdgeDecision(ZonaEdgeDecision):
    """M-pair decision for one adjacent sentence pair.

    Same fields as ZonaEdgeDecision (closed vocab, no parallel type system).
    Classifier input is the raw text of the two units, not extracted nodes
    and not zone summaries.
    """


@dataclass
class RunState:
    tempo_base_precedente: TempoVerbale | None = None
    backbone_tail: str | None = None


def _as_items(value: Sequence[_T] | _T | None) -> list[_T]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]  # type: ignore[list-item]


@dataclass
class SottoGrafo:
    """In-memory document graph. M2+ will grow query helpers."""

    eventi: list[EventoRisolto] = field(default_factory=list)
    menzioni: dict[str, MenzioneRisolta] = field(default_factory=dict)
    archi: list[ArcoEvento] = field(default_factory=list)
    quarantena: list[QuarantenaItem] = field(default_factory=list)

    def aggiungi(
        self,
        eventi: Sequence[EventoRisolto] | EventoRisolto | None = None,
        archi: Sequence[ArcoEvento] | ArcoEvento | None = None,
        quarantena: Sequence[QuarantenaItem] | QuarantenaItem | None = None,
        menzioni: Sequence[MenzioneRisolta] | MenzioneRisolta | None = None,
    ) -> None:
        self.eventi.extend(_as_items(eventi))
        self.archi.extend(_as_items(archi))
        self.quarantena.extend(_as_items(quarantena))
        for menzione in _as_items(menzioni):
            key = menzione.id or f"anon:{len(self.menzioni)}"
            self.menzioni[key] = menzione

    def eventi_per_posizione(self) -> list[EventoRisolto]:
        return sorted(self.eventi, key=_evento_pos_key)

    def candidati(self, evento: EventoRisolto) -> list[EventoRisolto]:
        """Events with identical lemma and overlapping SOGG/OGG mention ids."""
        return _filtra_candidati_evento(evento, self.eventi)


def _evento_pos_key(evento: EventoRisolto) -> tuple[int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
        evento.id or "",
    )


def _lemma_norm(lemma: str) -> str:
    return (lemma or "").strip().casefold()


def _sogg_ogg_ids(evento: EventoRisolto) -> set[str]:
    return {
        arg.menzione_id
        for arg in evento.argomenti
        if arg.ruolo in ("SOGG", "OGG") and arg.menzione_id
    }


def _filtra_candidati_evento(
    evento: EventoRisolto,
    pool: Sequence[EventoRisolto],
) -> list[EventoRisolto]:
    from app.pipeline.event_graph.candidati_entita import mention_ids_sogg_ogg

    lemma = _lemma_norm(evento.lemma)
    if not lemma:
        return []
    own_ids = mention_ids_sogg_ogg(evento)
    if not own_ids:
        return []
    ev_pos = (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
    )
    matches: list[EventoRisolto] = []
    for other in pool:
        if other is evento:
            continue
        if evento.id and other.id == evento.id:
            continue
        if other.fuso_in:
            continue
        if _lemma_norm(other.lemma) != lemma:
            continue
        if own_ids & mention_ids_sogg_ogg(other):
            matches.append(other)

    def _pos(item: EventoRisolto) -> tuple[int, int]:
        return (
            item.posizione_doc if item.posizione_doc is not None else 0,
            item.posizione_chunk if item.posizione_chunk is not None else 0,
        )

    previous = [item for item in matches if _pos(item) < ev_pos]
    rest = [item for item in matches if _pos(item) >= ev_pos]
    previous.sort(key=_pos, reverse=True)
    rest.sort(key=_pos)
    return previous + rest


__all__ = [
    "ArgomentoGrezzo",
    "ArgomentoRisolto",
    "ArcoEvento",
    "ArcoEventoGrezzo",
    "BasePrecede",
    "ChunkFactsheet",
    "ClasseVerboReggente",
    "EventEntityExtractionResult",
    "EventEntityParticipation",
    "EventQuerySpec",
    "EventoGrezzo",
    "EventoRisolto",
    "Fattualita",
    "FinestraTempoAssoluto",
    "FrammentoQuarantena",
    "FraseFactsheet",
    "FraseTipo",
    "Modalita",
    "GenereMenzione",
    "MenzioneRisolta",
    "NumeroMenzione",
    "OrientamentoArco",
    "PairEdgeDecision",
    "PianoNarrativo",
    "PredicatoNonFinito",
    "RispostaSintetizzata",
    "QuarantenaItem",
    "RelazioneSegnale",
    "RuoloArgomentale",
    "RuoloSe",
    "RunState",
    "Segmentazione",
    "SoggSpeciale",
    "SottoGrafo",
    "TempoVerbale",
    "TipoRelazione",
    "TipoSuperficiale",
    "TraversalKind",
    "ZonaEdgeDecision",
    "ZonaSummary",
]
