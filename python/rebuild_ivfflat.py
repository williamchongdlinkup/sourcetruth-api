"""
Rebuild IVFFlat index on chunk_embeddings.

Run after any significant corpus expansion (>10% chunk count change).
Formula: lists = round(sqrt(total_chunks))

Usage:
  python rebuild_ivfflat.py [--dry-run]
"""
import math
import os
import sys

from dotenv import load_dotenv
load_dotenv()

_pooler = os.environ.get("POOLER_DATABASE_URL")
if _pooler:
    os.environ["DATABASE_URL"] = _pooler

import psycopg2

DRY_RUN = '--dry-run' in sys.argv


def main():
    # Use two connections: one for counting, one autocommit for CONCURRENTLY
    conn_count = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn_count.cursor()

    cur.execute("SELECT COUNT(*) FROM chunk_embeddings")
    n = cur.fetchone()[0]
    lists = max(100, round(math.sqrt(n)))
    print(f"Total embeddings: {n:,}")
    print(f"Computed lists:   {lists}")

    cur.execute("SELECT COUNT(*) FROM pg_indexes WHERE indexname = 'idx_chunk_embeddings_ivfflat'")
    has_index = cur.fetchone()[0] > 0
    conn_count.close()

    if DRY_RUN:
        print("[DRY RUN] Would rebuild IVFFlat with lists=%d" % lists)
        return

    # CONCURRENTLY requires autocommit — open a fresh connection with autocommit=True
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    cur = conn.cursor()

    if has_index:
        print("Dropping existing IVFFlat index ...")
        cur.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_chunk_embeddings_ivfflat")
        print("Dropped.")

    # Raise maintenance_work_mem for this session so large index creation doesn't OOM
    cur.execute("SET maintenance_work_mem = '256MB'")
    print(f"Creating IVFFlat index (lists={lists}) — this may take several minutes ...")
    cur.execute(f"""
        CREATE INDEX CONCURRENTLY idx_chunk_embeddings_ivfflat
        ON chunk_embeddings
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = {lists})
    """)
    print(f"IVFFlat index rebuilt with lists={lists}.")
    conn.close()


if __name__ == "__main__":
    main()
