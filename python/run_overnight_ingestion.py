"""
SourceTruth Buddhist Corpus -- Overnight Ingestion Orchestrator
Runs all four corpus pipelines sequentially, then rebuilds the IVFFlat index.

Corpus sequence:
  1. SuttaCentral  -- Pali + English (MN DN SN AN KN)     ~30k chunks
  2. 84000         -- English translations from Tibetan    ~8k chunks
  3. CBETA T01-T09 -- Classical Chinese (all texts)        ~15k chunks
  4. GRETIL        -- Buddhist Sanskrit (~220 texts)        ~6k chunks

Licensing note:
  SuttaCentral (CC0/CC BY) and GRETIL (Open) are cleared for commercial use.
  84000 and CBETA are CC BY-NC -- resolve licensing before public launch.

Usage:
  python run_overnight_ingestion.py              # full run
  python run_overnight_ingestion.py --force      # re-embed all
  python run_overnight_ingestion.py --skip-clone # skip git/download steps
  python run_overnight_ingestion.py --only suttacentral 84000   # subset
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

# ── Logging: file + stdout ────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent.parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f'ingest_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── Pipeline runners ──────────────────────────────────────────────────────────

def run_suttacentral(force: bool, skip_clone: bool) -> str:
    from ingestion.suttacentral import run as sc_run, run_lzh as sc_run_lzh
    nikayas = ['mn', 'dn', 'sn', 'an', 'kn']
    log.info(f'  Pali nikayas: {nikayas}')
    sc_run(nikayas=nikayas, skip_clone=skip_clone, force=force)
    log.info('  lzh agamas: sa ma ea')
    sc_run_lzh(agamas=['sa', 'ma', 'ea'], skip_clone=True, force=force)  # repo already cloned
    return 'OK'


def run_sc_data_lzh(force: bool, skip_clone: bool) -> str:
    from ingestion.sc_data_lzh import run as lzh_run
    log.info('  collections: da ma sa ea + variants + lzh-dhp')
    lzh_run(force=force, skip_clone=skip_clone)
    return 'OK'


def run_84000(force: bool, skip_clone: bool) -> str:
    from ingestion.eighty4000 import run as e84_run
    e84_run(force=force, skip_clone=skip_clone)
    return 'OK'


def run_cbeta(force: bool, skip_clone: bool) -> str:
    from ingestion.cbeta import run as cbeta_run
    log.info('  scope: all T01-T09 texts')
    cbeta_run(force=force, skip_clone=skip_clone, all_texts=True)
    return 'OK'


def run_gretil(force: bool, skip_clone: bool) -> str:
    from ingestion.gretil import run as gretil_run
    gretil_run(force=force, skip_dl=skip_clone)
    return 'OK'


def run_quran(force: bool, skip_clone: bool) -> str:
    from ingestion.quran import run as quran_run
    quran_run(force=force, skip_dl=skip_clone)
    return 'OK'


PIPELINES = [
    ('suttacentral',  'Pali/English + lzh bilara',      run_suttacentral),
    ('sc-data-lzh',   'Classical Chinese (CC0 legacy)',  run_sc_data_lzh),
    ('84000',         'English from Tibetan',            run_84000),
    ('gretil',        'Sanskrit',                        run_gretil),
    ('quran',         'Arabic Quran (Tanzil CC BY 3.0)', run_quran),
]


# ── IVFFlat rebuild ───────────────────────────────────────────────────────────

def rebuild_ivfflat_index() -> None:
    import os
    import psycopg2, psycopg2.extras
    log.info('Rebuilding IVFFlat index...')
    url = os.environ['DATABASE_URL']

    # Count on a normal connection (get_conn starts a transaction via _register_vector,
    # so autocommit cannot be set on it — use a separate fresh connection for DDL).
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n FROM chunk_embeddings')
        n = cur.fetchone()['n']
    conn.close()

    lists = max(10, math.isqrt(n))
    log.info(f'  {n:,} vectors  lists={lists}')

    conn2 = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn2.autocommit = True
    with conn2.cursor() as cur:
        cur.execute('SET statement_timeout = 0')
        cur.execute('DROP INDEX IF EXISTS chunk_vec_idx')
        cur.execute(
            f'CREATE INDEX chunk_vec_idx ON chunk_embeddings '
            f'USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})'
        )
    conn2.close()
    log.info('  IVFFlat index rebuilt.')


# ── Final summary ─────────────────────────────────────────────────────────────

def print_chunk_summary() -> None:
    from db import get_conn, execute
    conn = get_conn()
    rows = execute(conn, """
        SELECT sc.code, dc.language, dc.tradition,
               COUNT(dc.id)                                  AS chunks,
               SUM(dc.word_count)                            AS words,
               SUM(CASE WHEN dc.is_verse THEN 1 ELSE 0 END) AS verse_chunks
        FROM document_chunks dc
        JOIN canon_texts ct ON dc.text_id = ct.id
        JOIN source_corpora sc ON ct.corpus_id = sc.id
        GROUP BY sc.code, dc.language, dc.tradition
        ORDER BY sc.code, dc.language
    """)
    conn.close()
    log.info('\n  Corpus          Language  Tradition        Chunks    Words    Verse')
    log.info('  ' + '-' * 72)
    total_chunks = 0
    for r in rows:
        log.info(
            f"  {r['code']:<15} {r['language'] or '-':<9} {r['tradition'] or '-':<16}"
            f" {r['chunks']:>7,}  {(r['words'] or 0):>8,}  {r['verse_chunks']:>6,}"
        )
        total_chunks += r['chunks']
    log.info('  ' + '-' * 72)
    log.info(f'  TOTAL: {total_chunks:,} chunks')


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='SourceTruth overnight ingestion')
    parser.add_argument('--force',      action='store_true', help='Re-embed already-ingested texts')
    parser.add_argument('--skip-clone', action='store_true', help='Skip git clone/download steps')
    parser.add_argument('--only',       nargs='+',           help='Run only these corpora (e.g. suttacentral 84000)')
    args = parser.parse_args()

    wall_start = time.time()
    log.info('=' * 72)
    log.info('SourceTruth Buddhist Corpus -- Overnight Ingestion')
    log.info(f'Log: {LOG_FILE}')
    log.info(f'force={args.force}  skip_clone={args.skip_clone}')
    log.info('=' * 72)

    results: dict[str, str] = {}

    for i, (code, label, runner) in enumerate(PIPELINES, 1):
        if args.only and code not in args.only:
            log.info(f'\n[{i}/{len(PIPELINES)}] {code} ({label}) -- SKIPPED (--only filter)')
            continue

        log.info(f'\n[{i}/{len(PIPELINES)}] {code.upper()} -- {label}')
        t0 = time.time()
        try:
            status = runner(args.force, args.skip_clone)
            elapsed = time.time() - t0
            results[code] = f'OK  ({elapsed/60:.1f} min)'
            log.info(f'  --> {code} done in {elapsed/60:.1f} min')
        except Exception as exc:
            elapsed = time.time() - t0
            results[code] = f'FAILED: {exc}'
            log.error(f'  --> {code} FAILED after {elapsed/60:.1f} min: {exc}', exc_info=True)

    # Rebuild vector index with tuned lists parameter
    log.info('\n' + '=' * 72)
    try:
        rebuild_ivfflat_index()
    except Exception as exc:
        log.error(f'IVFFlat rebuild failed: {exc}', exc_info=True)

    # Final summary
    log.info('\nChunk counts by corpus, language, tradition:')
    try:
        print_chunk_summary()
    except Exception as exc:
        log.error(f'Summary query failed: {exc}')

    wall_elapsed = time.time() - wall_start
    log.info(f'\nTotal wall time: {wall_elapsed/3600:.2f} h')
    log.info('\nPipeline results:')
    for code, status in results.items():
        log.info(f'  {code:<15} {status}')
    log.info('=' * 72)
    log.info(f'Log saved to: {LOG_FILE}')


if __name__ == '__main__':
    main()
