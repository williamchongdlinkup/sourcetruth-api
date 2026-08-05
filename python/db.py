"""
PostgreSQL connection factory with pgvector support.

Two connection strategies:

  Primary pool (get_primary_conn)
    ThreadedConnectionPool shared across FastAPI's sync route threads.
    Used for auth, quota checking, usage logging, and entity lookups.
    Each call checks out a connection, commits or rolls back, then returns it.

  Per-account long-lived connections (get_conn)
    One psycopg2 connection per corpus database account, held for the
    lifetime of the server by MultiAccountSearch. Reconnect-on-error
    logic lives in multi_search.py.
"""

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool
from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DATABASE_URL = os.environ['DATABASE_URL']
SQL_DIR = Path(__file__).parent.parent / 'sql'

_primary_pool: pg_pool.ThreadedConnectionPool | None = None


def init_primary_pool(url: str, minconn: int = 2, maxconn: int = 20) -> None:
    """Initialise the thread-safe primary connection pool. Call once at startup."""
    global _primary_pool
    _primary_pool = pg_pool.ThreadedConnectionPool(
        minconn, maxconn, url,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    # Warm the pool and register pgvector on each pre-created connection
    conns = [_primary_pool.getconn() for _ in range(minconn)]
    for c in conns:
        _register_vector(c)
        _primary_pool.putconn(c)


@contextmanager
def get_primary_conn():
    """
    Context manager: check out a connection from the primary pool.
    Commits on clean exit, rolls back on any exception, always returns
    the connection to the pool.

    Usage:
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """
    if _primary_pool is None:
        raise RuntimeError('Primary connection pool has not been initialised.')
    conn = _primary_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _primary_pool.putconn(conn)


def get_conn(url: str | None = None) -> psycopg2.extensions.connection:
    """Open a single long-lived connection for use by MultiAccountSearch."""
    conn = psycopg2.connect(
        url or DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = False
    _register_vector(conn)
    return conn


def _register_vector(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT oid FROM pg_type WHERE typname = 'vector'")
        row = cur.fetchone()
    if row:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)


def init_schema(url: str | None = None) -> None:
    """Run extension and schema SQL files. Safe to re-run (IF NOT EXISTS throughout)."""
    conn = psycopg2.connect(
        url or DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = True
    _register_vector(conn)
    try:
        with conn.cursor() as cur:
            for fname in ['01_extensions.sql', '02_schema.sql']:
                sql = (SQL_DIR / fname).read_text(encoding='utf-8')
                statements = [s.strip() for s in sql.split(';') if s.strip()]
                for stmt in statements:
                    try:
                        cur.execute(stmt)
                        print(f'  OK: {stmt[:70].replace(chr(10), " ")}')
                    except Exception as e:
                        print(f'  SKIP: {str(e)[:80]}')
        print('\nSchema initialised.')
    finally:
        conn.close()


def execute(conn, sql: str, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute_one(conn, sql: str, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute_many(conn, sql: str, rows: list):
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
