-- ── Chunk-level filter metadata (Night 1 migration) ─────────────────────────
-- Denormalised from canon_texts / source_corpora so API search queries can
-- filter without a JOIN.  Safe to re-run: uses IF NOT EXISTS / IF EXISTS.

ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS language    TEXT,      -- ISO 639-3: pli en san lzh
    ADD COLUMN IF NOT EXISTS tradition   TEXT,      -- theravada mahayana vajrayana pre-sectarian
    ADD COLUMN IF NOT EXISTS corpus_code TEXT,      -- suttacentral 84000 cbeta gretil
    ADD COLUMN IF NOT EXISTS collection  TEXT,      -- agama kangyur-tantra mn dn ...
    ADD COLUMN IF NOT EXISTS is_verse    BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS token_count INT;       -- approx: len(text)//4

-- Indexes for common API filter patterns
CREATE INDEX IF NOT EXISTS chunk_lang_idx        ON document_chunks (language);
CREATE INDEX IF NOT EXISTS chunk_tradition_idx   ON document_chunks (tradition);
CREATE INDEX IF NOT EXISTS chunk_corpus_idx      ON document_chunks (corpus_code);
CREATE INDEX IF NOT EXISTS chunk_collection_idx  ON document_chunks (collection);
CREATE INDEX IF NOT EXISTS chunk_verse_idx       ON document_chunks (is_verse);

-- Composite: the most common compound filter (language + tradition)
CREATE INDEX IF NOT EXISTS chunk_lang_trad_idx   ON document_chunks (language, tradition);
-- Composite: corpus + collection for per-vertical listing
CREATE INDEX IF NOT EXISTS chunk_corpus_coll_idx ON document_chunks (corpus_code, collection);
