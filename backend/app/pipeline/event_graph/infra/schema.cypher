CREATE CONSTRAINT eg_evento_id            IF NOT EXISTS FOR (e:Evento)           REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT eg_menzione_id          IF NOT EXISTS FOR (m:Menzione)         REQUIRE m.id IS UNIQUE;
CREATE CONSTRAINT eg_quarantena_id        IF NOT EXISTS FOR (q:Quarantena)       REQUIRE q.id IS UNIQUE;
CREATE CONSTRAINT eg_documento_id         IF NOT EXISTS FOR (d:Documento)        REQUIRE d.id IS UNIQUE;
CREATE CONSTRAINT eg_chunk_id             IF NOT EXISTS FOR (c:EgChunk)          REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT eg_zona_id              IF NOT EXISTS FOR (z:Zona)             REQUIRE z.id IS UNIQUE;
CREATE CONSTRAINT eg_run_id               IF NOT EXISTS FOR (r:EventGraphRun)    REQUIRE r.id IS UNIQUE;
CREATE CONSTRAINT eg_cluster_temporale_id IF NOT EXISTS FOR (c:ClusterTemporale) REQUIRE c.id IS UNIQUE;

CREATE INDEX eg_evento_doc            IF NOT EXISTS FOR (e:Evento)           ON (e.documento);
CREATE INDEX eg_evento_lemma          IF NOT EXISTS FOR (e:Evento)           ON (e.lemma);
CREATE INDEX eg_evento_piano          IF NOT EXISTS FOR (e:Evento)           ON (e.piano);
CREATE INDEX eg_evento_posizione      IF NOT EXISTS FOR (e:Evento)           ON (e.posizione_doc, e.posizione_chunk);
CREATE INDEX eg_evento_tempo_abs      IF NOT EXISTS FOR (e:Evento)           ON (e.tempo_assoluto);
CREATE INDEX eg_evento_catena         IF NOT EXISTS FOR (e:Evento)           ON (e.catena_id);
CREATE INDEX eg_menzione_forma        IF NOT EXISTS FOR (m:Menzione)         ON (m.forma_canonica);
CREATE INDEX eg_zona_doc              IF NOT EXISTS FOR (z:Zona)             ON (z.documento);
CREATE INDEX eg_zona_offset           IF NOT EXISTS FOR (z:Zona)             ON (z.offset_inizio, z.offset_fine);
CREATE INDEX eg_cluster_temporale_doc IF NOT EXISTS FOR (c:ClusterTemporale) ON (c.documento);
CREATE INDEX eg_cluster_temporale_ord IF NOT EXISTS FOR (c:ClusterTemporale) ON (c.chiave_ordine);

// Lucene full-text (not embeddings — stays inside the D6 no-ML-search rule).
// lemma/ancora hold the same event-sentence text; both indexed for resilience.
CREATE FULLTEXT INDEX eg_evento_testo IF NOT EXISTS FOR (e:Evento) ON EACH [e.lemma, e.ancora];
