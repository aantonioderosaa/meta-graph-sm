/** Isolated types for the event-graph frontend (piano 15.1 / M20). */

export type PianoNarrativo = "PRIMO_PIANO" | "SFONDO" | "FUORI_LINEA";

export type Fattualita = "FATTUALE" | "NON_FATTUALE" | "IPOTETICO";

export type EventGraphNodeTipo =
  | "Evento"
  | "Menzione"
  | "Quarantena"
  | "Zona"
  | "AncoraTemporale"
  | "ClusterTemporale";

export type PipelineStage =
  | "estrazione"
  | "regole_chunk"
  | "riconciliazione"
  | "collocazione_temporale"
  | "done"
  | "failed";

export type EventGraphNodeData = {
  id: string;
  label: string;
  tipo: EventGraphNodeTipo | string;
  piano?: PianoNarrativo | string | null;
  fattualita?: Fattualita | string | null;
  documento?: string | null;
  tempo?: string | null;
  polarita?: string | null;
  modalizzato?: boolean | string | null;
  iterativita?: boolean | string | null;
  fonte?: string | null;
  parent?: string | null;
  /** Client-only: zona membership after stripping Cytoscape compound `parent`. */
  zona_id?: string | null;
  ordinale?: number | null;
  riassunto?: string | null;
  evento_centrale?: string | null;
  etichetta?: string | null;
  tipo_cluster?: string | null;
  natura?: string | null;
  chiave_ordine?: number | null;
  posizione_doc_min?: number | null;
  ordine_vista?: number | null;
  posizione_doc?: number | null;
  posizione_chunk?: number | null;
  offset_inizio?: number | null;
  descrizione?: string | null;
  granularita?: string | null;
  inizio?: string | null;
  fine?: string | null;
  stimato?: boolean | string | null;
  confidenza?: number | null;
};

export type EventGraphEdgeData = {
  id: string;
  source: string;
  target: string;
  tipo: string;
  base?: string | null;
  segnale?: string | null;
  superato_da?: string | null;
  conflitto?: boolean | null;
  confidenza?: number | null;
  riassunto_transizione?: string | null;
  spiegazione?: string | null;
  livello?: string | null;
};

export type EventGraphNodeElement = { data: EventGraphNodeData };
export type EventGraphEdgeElement = { data: EventGraphEdgeData };

export type EventGraphElements = {
  nodes: EventGraphNodeElement[];
  edges: EventGraphEdgeElement[];
};

export type EventGraphResponse = {
  elements: EventGraphElements;
};

export type EventGraphJobResponse = {
  job_id: string;
};

export type EventGraphHealthResponse = {
  status: string;
};

export type GraphFilters = {
  documento?: string;
  piano?: string;
  lemma?: string;
  vista?: "tutto" | "ordine" | "temporale" | "relazioni";
};

export type TempoVerbale =
  | "presente"
  | "imperfetto"
  | "passato"
  | "futuro"
  | "non_finito";

export type TraversalKind =
  | "catena_di"
  | "spina_dorsale_di"
  | "prima_di"
  | "dopo_di"
  | "vicinato_temporale";

export type TipoRelazione =
  | "SOGG"
  | "OGG"
  | "OBL"
  | "TEMPO"
  | "LUOGO"
  | "MODO"
  | "CAUSA"
  | "LIMITE"
  | "CONDIZIONE"
  | "SCOPO"
  | "CONCESSIONE"
  | "CONTRASTO"
  | "SEQUENZA"
  | "CONTENUTO"
  | "COLLEGATO"
  | "SATELLITE_DI";

export type CatenaRuolo = "STESSO_EVENTO" | "AGGIORNA" | "CONTRADDICE";

export type CatenaOccorrenza = {
  id: string;
  ruolo: CatenaRuolo | string | null;
  divergenze: string[];
  posizione_doc?: number | null;
  posizione_chunk?: number | null;
  ancora?: string | null;
  tempo?: string | null;
  fattualita?: string | null;
  polarita?: string | null;
  documento?: string | null;
  sogg_forma?: string | null;
};

/** Live chain dashboard from GET /event-graph/nodo/{id}. */
export type CatenaNodo = {
  catena_id: string;
  occorrenze: CatenaOccorrenza[];
};

export type FinestraTempoAssoluto = {
  da?: string;
  a?: string;
};

export type EventQuerySpec = {
  lemma?: string;
  piano?: PianoNarrativo;
  fattualita?: Fattualita;
  tempo?: TempoVerbale;
  fonte?: string;
  tipo_relazione?: TipoRelazione | string;
  finestra_tempo_assoluto?: FinestraTempoAssoluto;
  documento?: string;
  traversal?: TraversalKind;
  traversal_target?: string;
};

export type EventQueryEvento = {
  id: string;
  lemma?: string | null;
  piano?: string | null;
  fattualita?: string | null;
  tempo?: string | null;
  documento?: string | null;
};

export type EventQueryArco = {
  id?: string;
  source?: string;
  target?: string;
  tipo?: string;
};

export type EventQueryRisultato = {
  eventi: EventQueryEvento[];
  archi: EventQueryArco[];
};

export type EventStructuredQueryResponse = {
  id: string;
  modo: "structured";
  spec: EventQuerySpec;
  risultato: EventQueryRisultato;
};

export type EventNlQueryResponse = {
  id: string;
  modo: "nl";
  spec_generata: EventQuerySpec;
  risultato: EventQueryRisultato;
};

export type EventGraphPipelineEvent = {
  ts?: string;
  job_id?: string;
  stage: PipelineStage | string;
  event?: string;
  payload?: Record<string, unknown>;
};

export const PIPELINE_STAGES: PipelineStage[] = [
  "estrazione",
  "regole_chunk",
  "riconciliazione",
  "collocazione_temporale",
  "done",
  "failed",
];

export type CatalogNode = {
  id: string;
  label: string;
  shape: string;
};

export type CatalogArc = {
  tipo: string;
  famiglia: string;
  direzione: string;
  significato: string;
};

export type CatalogArcFamiglia =
  | "argomentali"
  | "dizionario"
  | "temporale"
  | "placeholder"
  | "struttura";

/** Node trait in catalog "Nodi e tratti" — not an arc family. */
export type CatalogCatenaTrait = {
  ruoli: CatenaRuolo[] | string[];
  significato: Record<string, string>;
};

export type CatalogTraitValue = string | boolean | CatalogCatenaTrait;

export type CatalogVista = {
  nodi: string[];
  archi: string[];
  significato: string;
};

export type EventGraphCatalog = {
  nodes: CatalogNode[];
  traits: Record<string, CatalogTraitValue[] | CatalogCatenaTrait>;
  arches: Record<string, CatalogArc[]>;
  viste?: Record<string, CatalogVista>;
};

export type EventGraphStats = {
  nodi: Record<string, number>;
  archi: Record<string, number>;
  tratti: Record<string, Record<string, number>>;
};

/** Selection dashboard: one node or one relationship, all its raw properties. */
export type ElementSelection =
  | { kind: "nodo"; id: string }
  | { kind: "arco"; id: string };

export type NodoDettaglio = {
  id: string;
  labels: string[];
  proprieta: Record<string, unknown>;
  catena?: CatenaNodo;
};

export type ArcoEndpoint = {
  id: string | null;
  labels: string[];
  label: string | null;
};

export type ArcoDettaglio = {
  id: string;
  tipo: string;
  proprieta: Record<string, unknown>;
  source: ArcoEndpoint;
  target: ArcoEndpoint;
};
