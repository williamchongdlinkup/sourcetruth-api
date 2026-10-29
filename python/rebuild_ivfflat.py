# -*- coding: utf-8 -*-
"""Rebuild IVFFlat index after bulk ingestion. Run: python rebuild_ivfflat.py"""
import math, os, sys, psycopg2, psycopg2.extras
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_pooler = os.environ.get("POOLER_DATABASE_URL")
url = _pooler or os.environ["DATABASE_URL"]

conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
conn.autocommit = False

with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS n FROM document_chunks")
    n = cur.fetchone()["n"]
conn.rollback()

lists = max(100, int(math.sqrt(n)))
print(f"Total chunks: {n:,}  =>  lists={lists}", flush=True)

# Single transaction: SET LOCAL keeps settings for the transaction duration,
# no commit boundary between DROP and CREATE avoids PgBouncer session reset.
try:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        cur.execute("SET LOCAL maintenance_work_mem = '256MB'")
        print("Dropping old index...", flush=True)
        cur.execute("DROP INDEX IF EXISTS chunk_embeddings_idx")
        print(f"Building IVFFlat index (lists={lists}) — may take several minutes...", flush=True)
        cur.execute(
            f"CREATE INDEX chunk_embeddings_idx ON chunk_embeddings "
            f"USING ivfflat (embedding vector_cosine_ops) WITH (lists={lists})"
        )
    conn.commit()
    print(f"IVFFlat rebuilt successfully: lists={lists}", flush=True)
except Exception as e:
    print(f"[ERROR] IVFFlat rebuild failed: {e}", flush=True)
    conn.rollback()
finally:
    conn.close()
