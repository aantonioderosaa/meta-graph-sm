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
