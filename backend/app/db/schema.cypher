// Identità
CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE;

// Filtri frequenti
CREATE INDEX fact_is_latest IF NOT EXISTS FOR (f:Fact) ON (f.is_latest);
CREATE INDEX fact_type IF NOT EXISTS FOR (f:Fact) ON (f.type);
CREATE INDEX fact_doc IF NOT EXISTS FOR (f:Fact) ON (f.source_doc_id);
CREATE INDEX chunk_doc IF NOT EXISTS FOR (c:Chunk) ON (c.doc_id);

// Vector index — 768 dim (bge-base-en-v1.5), similarità coseno
CREATE VECTOR INDEX fact_embedding IF NOT EXISTS
FOR (f:Fact) ON (f.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};
