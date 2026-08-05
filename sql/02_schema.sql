-- ============================================================
-- CanonRAG — PostgreSQL Schema
-- Run: psql $DATABASE_URL -f sql/01_extensions.sql
--      psql $DATABASE_URL -f sql/02_schema.sql
-- ============================================================

-- ── Layer 1: Buddhist Entity Normalisation ────────────────────────────────────

CREATE TABLE IF NOT EXISTS buddhist_entities (
    id                  SERIAL PRIMARY KEY,
    entity_type         TEXT NOT NULL CHECK (entity_type IN (
                            'concept', 'person', 'place', 'text',
                            'school', 'deity', 'practice', 'other'
                        )),
    pali                TEXT,
    sanskrit            TEXT,
    classical_chinese   TEXT,
    tibetan             TEXT,
    english_preferred   TEXT NOT NULL,
    english_alternates  TEXT[]          DEFAULT '{}',
    traditions          TEXT[]          DEFAULT '{}',
    description         TEXT,
    ddb_id              TEXT,           -- Digital Dictionary of Buddhism
    sc_uid              TEXT,           -- SuttaCentral UID
    created_at          TIMESTAMPTZ     DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS entity_pali_idx     ON buddhist_entities (pali);
CREATE INDEX IF NOT EXISTS entity_skt_idx      ON buddhist_entities (sanskrit);
CREATE INDEX IF NOT EXISTS entity_zh_idx       ON buddhist_entities (classical_chinese);
CREATE INDEX IF NOT EXISTS entity_tib_idx      ON buddhist_entities (tibetan);
CREATE INDEX IF NOT EXISTS entity_type_idx     ON buddhist_entities (entity_type);
CREATE INDEX IF NOT EXISTS entity_tradition_idx ON buddhist_entities USING GIN (traditions);

-- All name variants for NER lookup
CREATE TABLE IF NOT EXISTS entity_name_variants (
    id          SERIAL PRIMARY KEY,
    entity_id   INT         NOT NULL REFERENCES buddhist_entities(id) ON DELETE CASCADE,
    name_text   TEXT        NOT NULL,
    language    TEXT        NOT NULL,   -- ISO 639-3: pli, san, lzh, bo, en, zh, ...
    script      TEXT,                   -- Latin, Devanagari, Chinese, Tibetan, ...
    is_primary  BOOLEAN     DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (entity_id, name_text, language)
);

CREATE INDEX IF NOT EXISTS variant_name_idx ON entity_name_variants (name_text);
CREATE INDEX IF NOT EXISTS variant_lang_idx ON entity_name_variants (language);

-- FTS on entity names for fuzzy NER
CREATE INDEX IF NOT EXISTS variant_trgm_idx ON entity_name_variants
    USING GIN (name_text gin_trgm_ops);

-- ── Layer 2: Document Store ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS source_corpora (
    id          SERIAL PRIMARY KEY,
    code        TEXT        UNIQUE NOT NULL,    -- cbeta, suttacentral, bdrc, gretil, 84000
    name        TEXT        NOT NULL,
    tradition   TEXT        NOT NULL,           -- theravada, mahayana, vajrayana, sanskrit, mixed
    language    TEXT        NOT NULL,           -- ISO 639-3 primary language
    license     TEXT,
    base_url    TEXT,
    last_synced TIMESTAMPTZ
);

INSERT INTO source_corpora (code, name, tradition, language, license, base_url) VALUES
    ('suttacentral', 'SuttaCentral bilara-data',    'theravada',    'pli', 'CC0 / CC BY',  'https://github.com/suttacentral/bilara-data'),
    ('cbeta',        'Chinese Buddhist Electronic Text Association', 'mahayana', 'lzh', 'CC BY-NC', 'https://www.cbeta.org'),
    ('bdrc',         'Buddhist Digital Resource Center', 'vajrayana', 'bo',  'Open',        'https://www.bdrc.io'),
    ('84000',        '84000: Translating the Words of the Buddha',  'vajrayana', 'en',  'CC BY-NC', 'https://84000.co'),
    ('gretil',       'Göttingen Register of Electronic Texts in Indian Languages', 'sanskrit', 'san', 'Open', 'https://gretil.sub.uni-goettingen.de')
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS canon_texts (
    id              SERIAL PRIMARY KEY,
    corpus_id       INT         NOT NULL REFERENCES source_corpora(id),
    external_id     TEXT        NOT NULL,       -- SC sutta_uid, T0001, BDRC ID, ...
    title_original  TEXT,
    title_english   TEXT,
    title_pali      TEXT,
    title_sanskrit  TEXT,
    author          TEXT,
    translator      TEXT,
    century         TEXT,                       -- approximate: "5th BCE", "7th CE"
    tradition       TEXT,
    language        TEXT        NOT NULL,       -- ISO 639-3
    collection      TEXT,                       -- nikaya, agama, vinaya, tantra, ...
    sub_collection  TEXT,
    volume          TEXT,
    number          TEXT,                       -- T number, sutta number
    url             TEXT,
    word_count      INT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (corpus_id, external_id)
);

CREATE INDEX IF NOT EXISTS text_corpus_idx      ON canon_texts (corpus_id);
CREATE INDEX IF NOT EXISTS text_tradition_idx   ON canon_texts (tradition);
CREATE INDEX IF NOT EXISTS text_language_idx    ON canon_texts (language);
CREATE INDEX IF NOT EXISTS text_collection_idx  ON canon_texts (collection);
CREATE INDEX IF NOT EXISTS text_external_idx    ON canon_texts (external_id);

CREATE TABLE IF NOT EXISTS document_chunks (
    id                  SERIAL PRIMARY KEY,
    text_id             INT         NOT NULL REFERENCES canon_texts(id) ON DELETE CASCADE,
    chunk_index         INT         NOT NULL,
    chunk_text          TEXT        NOT NULL,
    reference           TEXT,                   -- "MN 1:1.1–1.10", "T0001.1a01–1a15"
    chapter             TEXT,
    section             TEXT,
    word_count          INT,
    token_count         INT,
    entity_ids          INT[]       DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chunk_text_idx       ON document_chunks (text_id);
CREATE INDEX IF NOT EXISTS chunk_entity_idx     ON document_chunks USING GIN (entity_ids);

-- Full-text search — simple tokenizer handles Pali romanisation well;
-- for Chinese we rely more on trigram and vector search
ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS chunk_fts TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('simple', coalesce(chunk_text, ''))) STORED;

CREATE INDEX IF NOT EXISTS chunk_fts_idx ON document_chunks USING GIN (chunk_fts);

-- Trigram index for partial/fuzzy matches
CREATE INDEX IF NOT EXISTS chunk_trgm_idx ON document_chunks
    USING GIN (chunk_text gin_trgm_ops);

-- ── Vector embeddings (pgvector) ──────────────────────────────────────────────
-- voyage-multilingual-2 produces 1024-dimensional vectors

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id    INT PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
    embedding   vector(1024) NOT NULL
);

-- IVFFlat index — tune lists to sqrt(n_chunks) once corpus is loaded
CREATE INDEX IF NOT EXISTS chunk_vec_idx ON chunk_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ── Cross-canon alignments ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS canon_alignments (
    id              SERIAL PRIMARY KEY,
    chunk_id_a      INT         NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
    chunk_id_b      INT         NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
    alignment_type  TEXT        NOT NULL CHECK (alignment_type IN (
                        'parallel', 'translation', 'commentary',
                        'expansion', 'contraction', 'quotation'
                    )),
    confidence      REAL        CHECK (confidence BETWEEN 0.0 AND 1.0),
    verified_by     TEXT        DEFAULT 'llm', -- human | llm | automatic
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (chunk_id_a, chunk_id_b)
);

CREATE INDEX IF NOT EXISTS align_a_idx ON canon_alignments (chunk_id_a);
CREATE INDEX IF NOT EXISTS align_b_idx ON canon_alignments (chunk_id_b);

-- ── API access management ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS api_keys (
    id          SERIAL PRIMARY KEY,
    key_hash    TEXT        UNIQUE NOT NULL,    -- SHA-256 of the raw key
    name        TEXT,
    email       TEXT,
    tier        TEXT        NOT NULL DEFAULT 'free'
                            CHECK (tier IN ('free', 'starter', 'professional')),
    daily_limit INT         NOT NULL DEFAULT 100,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    last_used   TIMESTAMPTZ
);

-- ── Fix 3: atomic daily quota counter ────────────────────────────────────────
-- O(1) indexed lookup replaces the COUNT(*) view scan in _check_api_key.
-- Incremented via ON CONFLICT DO UPDATE in _log_usage (background task).
CREATE TABLE IF NOT EXISTS daily_quota (
    key_id  INT  NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    date    DATE NOT NULL DEFAULT CURRENT_DATE,
    count   INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, date)
);

-- ── Full usage log (analytics, billing audit) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS api_usage (
    id              BIGSERIAL PRIMARY KEY,
    key_id          INT         REFERENCES api_keys(id),
    endpoint        TEXT,
    query_text      TEXT,
    traditions      TEXT[],
    languages       TEXT[],
    results_count   INT,
    latency_ms      INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS usage_key_day_idx ON api_usage (key_id, created_at);

-- Retained for analytics queries; NOT used for quota enforcement
CREATE OR REPLACE VIEW api_usage_today AS
SELECT
    key_id,
    COUNT(*) AS requests_today
FROM api_usage
WHERE created_at >= CURRENT_DATE
GROUP BY key_id;
