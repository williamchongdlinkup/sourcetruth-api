import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import psycopg2, psycopg2.extras

conn = psycopg2.connect(os.environ['DATABASE_URL'], cursor_factory=psycopg2.extras.RealDictCursor)

with conn.cursor() as cur:
    cur.execute("""
        SELECT corpus_code, language, COUNT(*) as chunks
        FROM document_chunks
        GROUP BY corpus_code, language
        ORDER BY corpus_code, language
    """)
    rows = cur.fetchall()

with conn.cursor() as cur:
    cur.execute('SELECT COUNT(*) AS n FROM chunk_embeddings')
    emb = cur.fetchone()

conn.close()

print('Chunks in DB:')
total = 0
for r in rows:
    code = r['corpus_code'] or 'NULL'
    lang = r['language'] or 'NULL'
    n = r['chunks']
    print(f'  {code:<16} {lang:<6} {n:>8,}')
    total += n
print(f'  TOTAL                        {total:>8,}')
print(f'Embeddings:                    {emb["n"]:>8,}')
