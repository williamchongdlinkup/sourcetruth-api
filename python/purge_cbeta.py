import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import psycopg2, psycopg2.extras

conn = psycopg2.connect(os.environ['DATABASE_URL'], cursor_factory=psycopg2.extras.RealDictCursor)

with conn.cursor() as cur:
    cur.execute("SELECT id FROM source_corpora WHERE code = 'cbeta'")
    row = cur.fetchone()
    if not row:
        print('cbeta corpus not found — nothing to purge')
        conn.close()
        sys.exit(0)
    corpus_id = row['id']

with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS n FROM canon_texts WHERE corpus_id = %s", (corpus_id,))
    texts = cur.fetchone()['n']
with conn.cursor() as cur:
    cur.execute("""
        SELECT COUNT(*) AS n FROM document_chunks dc
        JOIN canon_texts ct ON dc.text_id = ct.id
        WHERE ct.corpus_id = %s
    """, (corpus_id,))
    chunks = cur.fetchone()['n']

print(f'About to delete: {texts} texts, {chunks:,} chunks (+ embeddings via CASCADE)')
print('Proceeding...')

with conn.cursor() as cur:
    cur.execute("""
        DELETE FROM document_chunks
        WHERE text_id IN (SELECT id FROM canon_texts WHERE corpus_id = %s)
    """, (corpus_id,))
    deleted_chunks = cur.rowcount

with conn.cursor() as cur:
    cur.execute("DELETE FROM canon_texts WHERE corpus_id = %s", (corpus_id,))
    deleted_texts = cur.rowcount

conn.commit()
print(f'Deleted {deleted_texts} texts, {deleted_chunks:,} chunks.')

with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS n FROM chunk_embeddings")
    remaining_emb = cur.fetchone()['n']
print(f'Remaining embeddings in DB: {remaining_emb:,}')
conn.close()
