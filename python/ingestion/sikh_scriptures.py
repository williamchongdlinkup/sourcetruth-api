# -*- coding: utf-8 -*-
"""
Sikh Scriptures ingestion — Sri Guru Granth Sahib Ji (SGGS).

Source: Shabados Database v4.x (MIT licence; Gurbani text is public domain)
  GitHub: https://github.com/shabados/database
  Release: database.sqlite (~155MB download, cached locally)

Corpus content:
  - SGGS Gurmukhi text (PD, ancient scripture, compiled 1604-1708)
  - English romanized transliteration (not separately copyrightable)
  Chunked by Shabad (hymn) — the natural semantic unit of SGGS.

English translations of SGGS are modern and copyrighted (Khalsa, Manmohan Singh).
This corpus uses Gurmukhi + English transliteration only — both are free of copyright.
Cross-lingual retrieval (English query → Gurmukhi text) via voyage-multilingual-2
is expected to perform similarly to the Quran Arabic corpus (~0.69 nDCG@5).

Corpus code : sggs
Tradition   : sikhism
Language    : pa (Punjabi / Gurmukhi)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
_pooler = os.environ.get("POOLER_DATABASE_URL")
if _pooler:
    os.environ["DATABASE_URL"] = _pooler

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, execute_one
from embed import embed_documents

CORPUS_CODE = "sggs"
CORPUS_NAME = "Sri Guru Granth Sahib Ji (SGGS)"
TRADITION   = "sikhism"
LANGUAGE    = "pa"
LICENSE     = "Public Domain"
BASE_URL    = "https://github.com/shabados/database"

SHABADOS_RELEASE_URL = "https://github.com/shabados/database/releases/download/4.8.7/database.sqlite"
SQLITE_CACHE = Path(__file__).parent.parent.parent / "data" / "shabados_database.sqlite"

MAX_RETRIES = 5
RETRY_DELAY = 30
SGGS_SOURCE_ID = "G"   # SourceID for Sri Guru Granth Sahib Ji in Shabados schema


def _download_sqlite() -> Path:
    """Download the Shabados SQLite database if not already cached."""
    SQLITE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if SQLITE_CACHE.exists():
        size_mb = SQLITE_CACHE.stat().st_size / (1024 * 1024)
        print(f"  Using cached Shabados DB ({size_mb:.1f}MB): {SQLITE_CACHE}")
        return SQLITE_CACHE

    print(f"  Downloading Shabados database (~155MB) ...")
    with httpx.stream("GET", SHABADOS_RELEASE_URL, timeout=300.0, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(SQLITE_CACHE, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if downloaded % (5 * 1024 * 1024) < 65536:
                        print(f"    {pct}% ({downloaded//1024//1024}MB / {total//1024//1024}MB)")
    print(f"  Download complete: {SQLITE_CACHE.stat().st_size//1024//1024}MB")
    return SQLITE_CACHE


def _get_schema(conn: sqlite3.Connection) -> list[str]:
    """Return all table names in the Shabados SQLite database."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [row[0] for row in cur.fetchall()]


def _extract_sggs_shabads(sqlite_path: Path) -> list[dict]:
    """
    Extract SGGS shabads from Shabados SQLite v4.8.7.

    Schema (v4.8.7):
      lines: id, shabad_id, source_page, gurmukhi, type_id, order_id
      shabads: id, source_id (1=SGGS)
      transliterations: line_id, language_id (1=English), transliteration
    """
    db = sqlite3.connect(str(sqlite_path))
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    tables = _get_schema(db)
    print(f"  Tables found: {tables}")

    # v4.8.7 uses lowercase table names
    if 'lines' not in tables:
        print(f"  [ERROR] 'lines' table not found in {tables}")
        db.close()
        return []

    # Count SGGS lines
    cur.execute("""
        SELECT COUNT(*) as n FROM lines l
        JOIN shabads s ON s.id = l.shabad_id
        WHERE s.source_id = 1
    """)
    total_lines = cur.fetchone()['n']
    print(f"  SGGS total lines: {total_lines:,}")

    # Fetch all SGGS lines with English transliteration
    cur.execute("""
        SELECT
            l.shabad_id,
            l.source_page,
            l.gurmukhi,
            l.order_id,
            t.transliteration AS translit_en
        FROM lines l
        JOIN shabads s ON s.id = l.shabad_id
        LEFT JOIN transliterations t ON t.line_id = l.id AND t.language_id = 1
        WHERE s.source_id = 1
        ORDER BY l.shabad_id, l.order_id
    """)
    rows = cur.fetchall()
    print(f"  Fetched {len(rows):,} lines with transliterations")
    db.close()

    # Group by shabad
    from collections import defaultdict
    shabads: dict = defaultdict(lambda: {'gurmukhi_lines': [], 'translit_lines': [], 'page': None})
    for row in rows:
        sid     = row['shabad_id']
        gmukhi  = (row['gurmukhi'] or '').strip()
        translit = (row['translit_en'] or '').strip()
        page    = row['source_page']
        if gmukhi:
            shabads[sid]['gurmukhi_lines'].append(gmukhi)
        if translit:
            shabads[sid]['translit_lines'].append(translit)
        if page and not shabads[sid]['page']:
            shabads[sid]['page'] = page

    # Convert to text chunks
    chunks = []
    for sid, data in shabads.items():
        gurmukhi = ' | '.join(data['gurmukhi_lines'])
        translit = ' '.join(data['translit_lines'])
        page_no  = data['page']

        # Combine Gurmukhi + English transliteration for embedding
        combined = f"{gurmukhi}\n{translit}" if translit else gurmukhi

        if not combined.strip() or len(combined.split()) < 3:
            continue

        ref = f"SGGS Ang {page_no}" if page_no else f"SGGS Shabad {sid}"
        chunks.append({
            'text':        combined,
            'reference':   ref,
            'chapter':     str(page_no) if page_no else str(sid),
            'shabad_id':   sid,
            'word_count':  len(combined.split()),
            'token_count': max(1, len(combined.encode('utf-8')) // 4),
        })

    print(f"  Produced {len(chunks):,} shabad chunks")
    return chunks


def _upsert_corpus(conn) -> int:
    row = execute_one(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (CORPUS_CODE, CORPUS_NAME, TRADITION, LANGUAGE, LICENSE, BASE_URL))
    conn.commit()
    return row['id']


def _upsert_text(conn, corpus_id: int, title: str, page_range: str) -> int:
    external_id = f"sggs-{page_range}"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, title, TRADITION, LANGUAGE,
          "Sri Guru Granth Sahib Ji", BASE_URL))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    print("Sri Guru Granth Sahib Ji ingestion")
    print("  Source: Shabados Database (MIT licence, Gurbani text: Public Domain)")

    sqlite_path = _download_sqlite()
    chunks = _extract_sggs_shabads(sqlite_path)

    if not chunks:
        print("[ERROR] No chunks extracted — check Shabados schema")
        return

    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    # Group chunks into page-range batches of 100 angs each for canon_texts entries
    # (one canon_texts row per 100 angs keeps the texts table manageable)
    BATCH_SIZE = 100

    # Determine page ranges from chunk references
    page_batches: dict[str, list[dict]] = {}
    for chunk in chunks:
        page = chunk.get('chapter', '0')
        try:
            page_num = int(page)
            batch_key = f"{((page_num - 1) // BATCH_SIZE) * BATCH_SIZE + 1}-{((page_num - 1) // BATCH_SIZE + 1) * BATCH_SIZE}"
        except (ValueError, TypeError):
            batch_key = "misc"
        page_batches.setdefault(batch_key, []).append(chunk)

    total_chunks = 0
    total_texts  = 0

    for batch_key, batch_chunks in sorted(page_batches.items()):
        title = f"SGGS Ang {batch_key}"
        text_id = _upsert_text(conn, corpus_id, title, batch_key)

        if not force:
            existing = execute_one(conn,
                "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
            if existing and existing['n'] > 0:
                n = existing['n']
                print(f"  Ang {batch_key}: already ingested ({n} chunks) — skip")
                total_chunks += n
                total_texts  += 1
                continue

        if force:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chunk_embeddings WHERE chunk_id IN "
                    "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
            conn.commit()

        print(f"  Ang {batch_key}: {len(batch_chunks)} shabads ...", end=' ', flush=True)

        texts_to_embed = [c['text'] for c in batch_chunks]
        embeddings = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = embed_documents(texts_to_embed)
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    print(f"\n  [WARN] Voyage attempt {attempt}/{MAX_RETRIES}: {e}. Retry in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"\n  [ERROR] Voyage failed: {e}")

        if embeddings is None:
            continue

        try:
            written = 0
            for idx, (chunk, emb) in enumerate(zip(batch_chunks, embeddings)):
                emb_np = np.array(emb, dtype=np.float32)
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO document_chunks
                            (text_id, chunk_index, chunk_text, reference, chapter,
                             section, word_count, token_count, entity_ids,
                             language, tradition, corpus_code, collection, is_verse)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        RETURNING id
                    """, (
                        text_id, idx, chunk['text'], chunk['reference'],
                        chunk.get('chapter'), None,
                        chunk['word_count'], chunk['token_count'],
                        None, LANGUAGE, TRADITION, CORPUS_CODE,
                        "Sri Guru Granth Sahib Ji", True,
                    ))
                    chunk_id = cur.fetchone()['id']
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                        (chunk_id, emb_np),
                    )
                written += 1
            conn.commit()
            total_chunks += written
            total_texts  += 1
            print(f"✓ {written} chunks")
        except Exception as e:
            print(f"\n  [ERROR] write failed for Ang {batch_key}: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"SGGS ingestion complete.")
    print(f"  Ang batches ingested : {total_texts}")
    print(f"  Total shabads        : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Sri Guru Granth Sahib Ji (Shabados Database)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
