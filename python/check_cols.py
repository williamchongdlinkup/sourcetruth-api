import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import psycopg2, psycopg2.extras

DATABASE_URL = os.environ['DATABASE_URL']
conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

# Check columns
with conn.cursor() as cur:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='document_chunks' ORDER BY ordinal_position")
    cols = [r['column_name'] for r in cur.fetchall()]
print('document_chunks columns:', cols)

# Apply ALTER TABLE if new columns missing
needed = {'language', 'tradition', 'corpus_code', 'collection', 'is_verse', 'token_count'}
missing = needed - set(cols)
if missing:
    print('Missing columns, applying ALTER TABLE...')
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE document_chunks
                ADD COLUMN IF NOT EXISTS language    TEXT,
                ADD COLUMN IF NOT EXISTS tradition   TEXT,
                ADD COLUMN IF NOT EXISTS corpus_code TEXT,
                ADD COLUMN IF NOT EXISTS collection  TEXT,
                ADD COLUMN IF NOT EXISTS is_verse    BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS token_count INT
        """)
    print('ALTER TABLE done.')
    with conn.cursor() as cur:
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS chunk_lang_idx        ON document_chunks (language)",
            "CREATE INDEX IF NOT EXISTS chunk_tradition_idx   ON document_chunks (tradition)",
            "CREATE INDEX IF NOT EXISTS chunk_corpus_idx      ON document_chunks (corpus_code)",
            "CREATE INDEX IF NOT EXISTS chunk_collection_idx  ON document_chunks (collection)",
            "CREATE INDEX IF NOT EXISTS chunk_verse_idx       ON document_chunks (is_verse)",
            "CREATE INDEX IF NOT EXISTS chunk_lang_trad_idx   ON document_chunks (language, tradition)",
            "CREATE INDEX IF NOT EXISTS chunk_corpus_coll_idx ON document_chunks (corpus_code, collection)",
        ]:
            cur.execute(idx_sql)
            print('  INDEX:', idx_sql[:60])
    print('Indexes done.')
else:
    print('All metadata columns already present.')

conn.close()
