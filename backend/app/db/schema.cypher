// Identità
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE;

// Filtri frequenti
CREATE INDEX chunk_doc IF NOT EXISTS FOR (c:Chunk) ON (c.doc_id);

// Vector index — 768 dim (bge-base-en-v1.5), similarità coseno
CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

// Entità/Eventi/Concetti
CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT concept_id IF NOT EXISTS FOR (c:Concept) REQUIRE c.id IS UNIQUE;

CREATE INDEX node_type IF NOT EXISTS FOR (n:Node) ON (n.type);
CREATE INDEX node_dreamed IF NOT EXISTS FOR (n:Node) ON (n.dreamed);
CREATE INDEX node_merged_into IF NOT EXISTS FOR (n:Node) ON (n.merged_into);
CREATE INDEX relation_is_latest IF NOT EXISTS FOR ()-[r:Relation]-() ON (r.is_latest);
CREATE INDEX relation_normalized IF NOT EXISTS FOR ()-[r:Relation]-() ON (r.normalized_relation);

CREATE VECTOR INDEX node_embedding IF NOT EXISTS
FOR (n:Node) ON (n.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

// Fase 19: summary embedding (query text, not only node name). Backfill of
// existing :Node rows is out of scope — write_node populates new ones.
CREATE VECTOR INDEX node_summary_embedding IF NOT EXISTS
FOR (n:Node) ON (n.summary_embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

// Promoted Concept embeddings must be computed on `definition` (not `name`).
// Re-embedding / backfill of existing :Concept nodes is Fase 13 (Fase 4 writes new ones).
CREATE VECTOR INDEX concept_embedding IF NOT EXISTS
FOR (c:Concept) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX relation_embedding IF NOT EXISTS
FOR ()-[r:Relation]-() ON (r.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

CREATE FULLTEXT INDEX node_concept_fulltext IF NOT EXISTS
FOR (n:Node|Concept) ON EACH [n.name];

CREATE FULLTEXT INDEX relation_fulltext IF NOT EXISTS
FOR ()-[r:Relation]-() ON EACH [r.relation];

// Fase 19: summary + denormalized witness text (Neo4j cannot index string lists).
CREATE FULLTEXT INDEX node_summary_fulltext IF NOT EXISTS
FOR (n:Node) ON EACH [n.summary];

CREATE FULLTEXT INDEX relation_witness_fulltext IF NOT EXISTS
FOR ()-[r:Relation]-() ON EACH [r.witness_text];

// Cronologia query Node/Concept
CREATE CONSTRAINT node_query_log_id IF NOT EXISTS FOR (q:NodeQueryLog) REQUIRE q.id IS UNIQUE;
CREATE INDEX node_query_log_created_at IF NOT EXISTS FOR (q:NodeQueryLog) ON (q.created_at);

// --- Metagraph Fase 2 (additive; no existing names dropped) ---

// :Concept TBox properties (no new :Subdomain label — :Subdomain in doc4 = :Concept):
//   kernel_category, parent_uri, definition, aliases, promoted (bool)
CREATE INDEX concept_kernel_category IF NOT EXISTS FOR (c:Concept) ON (c.kernel_category);
CREATE INDEX concept_parent_uri IF NOT EXISTS FOR (c:Concept) ON (c.parent_uri);

// :Node kernel_category (EntityKernelType string)
CREATE INDEX node_kernel_category IF NOT EXISTS FOR (n:Node) ON (n.kernel_category);

// Famiglia B dedicated relationship types (Neo4j has no CREATE TYPE):
//   SAME_AS, POSSIBLY_SAME_AS, CONTRADICTS, SUPERSEDES, UPDATED_BY, EQUIVALENT_TO, DERIVED_FROM
// DERIVED_FROM already exists in the pipeline toward :Chunk; generalized: also allowed
// as assertion-provenance toward :Node / :Relation (pipeline Cypher unchanged in this phase).
// SAME_AS / POSSIBLY_SAME_AS are closed Famiglia B vocabulary (app/models/kernel.py
// SpecialRelationType — frozen, never removed on its own) but currently unwritten:
// identity_resolution.py (Fase 8, the only writer) was removed with ENABLE_FACET_IDENTITY.

// :Relation additive properties (do not drop relation, normalized_relation, is_latest, embedding):
//   witnesses_a, witnesses_b, provenance, valid_time, system_time
// Event triage (ENABLE_EVENT_TRIAGE) — schema-optional free properties, no
// CREATE CONSTRAINT / INDEX in this phase (YAGNI; Macrotask 7 may add an index
// on caused_by_event_id/verdict if the read endpoint needs it):
//   :Relation.caused_by_event_id, :Relation.run_id (plus existing created_at)
//   :Node.revisions — list of {property, old_value, event_id, run_id, at}
//     (additive, same pattern as origin_fact_ids)

// Backbone relationship types — two spaces must never share one relationship type:
//   IS_A (Concept→Concept type lattice)
//   MEMBER_OF (Node→Concept unique home)
// Distinct from existing HAS_CONCEPT (free thematic bridge; keep it).

// :ConnectivityRule properties:
//   source_category, relation_type, target_category, origin_fact_ids, generalization_level
// Community 5.24: IS UNIQUE (not NODE KEY — Enterprise-only)
CREATE CONSTRAINT connectivity_rule_triple IF NOT EXISTS
FOR (r:ConnectivityRule) REQUIRE (r.source_category, r.relation_type, r.target_category) IS UNIQUE;

// :CorpusContext singleton per KB (id can be a well-known value later).
// Properties: summary_text, embedding, updated_at, document_count
// No new vector index for this embedding in this phase.
CREATE CONSTRAINT corpus_context_id IF NOT EXISTS FOR (c:CorpusContext) REQUIRE c.id IS UNIQUE;

// :JudgeRun (Fase 10) — structured log of each post-batch judge pass.
// Properties: id, batch_id, timestamp, anti_blur, equivalent_to, reraffine,
// temporal. No uniqueness constraint required; pipeline MERGEs on id = job_id.

// :EventTriageRun (ENABLE_EVENT_TRIAGE) — structured log of one event-triage
// pass per :Evento. Same pattern as :JudgeRun: pipeline MERGEs on id =
// event_id; no uniqueness constraint required.
// Properties: id, event_id, verdict (confirmed|waiting|incomplete), run_id,
// timestamp.

// :PendingEventContext — wait-state when triage cannot yet apply a slot.
// Pipeline MERGEs on event_id; the node is left in place after a terminal
// verdict (skip via EventTriageRun.verdict). No constraint/index (YAGNI;
// Macrotask 7 GET /graph/event-incompleteness lists by verdict without one).
// Properties: event_id, missing_context, first_seen_run_id,
// last_checked_run_id, checks_without_progress.
