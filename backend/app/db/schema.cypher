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

// :IdentityNode properties: uri, canonical_summary
CREATE CONSTRAINT identity_node_uri IF NOT EXISTS FOR (i:IdentityNode) REQUIRE i.uri IS UNIQUE;

// Famiglia B dedicated relationship types (Neo4j has no CREATE TYPE):
//   SAME_AS, POSSIBLY_SAME_AS, CONTRADICTS, SUPERSEDES, UPDATED_BY, EQUIVALENT_TO, DERIVED_FROM
// DERIVED_FROM already exists in the pipeline toward :Chunk; generalized: also allowed
// as assertion-provenance toward :Node / :Relation (pipeline Cypher unchanged in this phase).
// Identity blocking (Fase 8): NOT_SAME_AS between :Node pairs (judge-declassified
// omonimia). Not a Famiglia B kernel member; dedicated rel type so blocking can skip
// the pair without a property on POSSIBLY_SAME_AS.

// :Relation additive properties (do not drop relation, normalized_relation, is_latest, embedding):
//   witnesses_a, witnesses_b, provenance, valid_time, system_time

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
// identity, missed_contradictions, temporal, generic_instances (Fase 23).
// No uniqueness constraint required; pipeline MERGEs on id = job_id.
//
// Fase 23: generic subdomain instances are ordinary :Node rows (code MERGE,
// like :JudgeRun — no extra uniqueness constraint). Properties: is_generic
// (bool), generic_observation_count (int). Redirected singletons keep
// merged_into set so existing `merged_into IS NULL` filters hide them.

// :AgentSearchRun (Fase 22) — structured log of one ReAct verification pass
// per promoted :PendingHypothesis (including fallback). Same pattern as
// :JudgeRun: pipeline MERGEs on id; no uniqueness constraint required.
// Properties: id, hypothesis_id, steps (JSON: tool + reasoning), verdict,
// turns_used, timestamp.

// :PendingHypothesis (Fase 20) — open context hypotheses. Never an S0 fact.
// Properties: id, claim_target, evidence_span, witness_fragments, evidence_gap,
// confidence (low|medium|high), status (open|confirmed|dismissed),
// created_at, updated_at. Extra ingest fields (kind, marker_category,
// origin_doc_id, origin_doc_count, listen_count, promoted) are optional.
CREATE CONSTRAINT pending_hypothesis_id IF NOT EXISTS
FOR (h:PendingHypothesis) REQUIRE h.id IS UNIQUE;
