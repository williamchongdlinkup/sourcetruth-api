# -*- coding: utf-8 -*-
"""Rebuild IVFFlat index after bulk ingestion. Run: python rebuild_ivfflat.py"""
import math, os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_pooler = os.environ.get("POOLER_DATABASE_URL")
if _pooler:
    os.environ["DATABASE_URL"] = _pooler

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn

conn = get_conn()
with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) AS n FROM document_chunks")
    n = cur.fetchone()["n"]
lists = max(100, int(math.sqrt(n)))
print(f"Total chunks: {n:,}  =>  lists={lists}")

try:
    with conn.cursor() as cur:
        cur.execute("SET maintenance_work_mem = '256MB'")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS chunk_embeddings_idx")
    conn.commit()
    print("Dropped old index.")
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE INDEX chunk_embeddings_idx ON chunk_embeddings "
            f"USING ivfflat (embedding vector_cosine_ops) WITH (lists={lists})"
        )
    conn.commit()
    print(f"IVFFlat rebuilt successfully: lists={lists}")
except Exception as e:
    print(f"[ERROR] IVFFlat rebuild failed: {e}")
    conn.rollback()
finally:
    conn.close()