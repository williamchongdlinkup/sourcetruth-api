# -*- coding: utf-8 -*-
"""
Tanakh ingestion: JPS 1917 English translation (Public Domain).

Source: Sefaria-Export GCS bucket (storage.googleapis.com/sefaria-export/)
        Version: "The Holy Scriptures A New Translation JPS 1917"
        License: Public Domain (verified via Sefaria-Export metadata 2026-08-06)
        Origin: Jewish Publication Society 1917 → Open Siddur Project → Sefaria

Corpus: Full Tanakh — Torah (5 books), Prophets/Nevi'im, Writings/Ketuvim = 39 books
Chunking: One chapter = one chunk. Chapters with < MIN_VERSES merged into next.
          Chapters with > MAX_VERSES split at CHUNK_TARGET_VERSES boundaries.
Reference: "Genesis 1" (single chapter) or "Genesis 1:1-25" (sub-chunk)
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import numpy as np
import psycopg2
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one
from embed import embed_documents

GCS_BASE = "https://storage.googleapis.com/sefaria-export"
TANAKH_CATEGORIES = ["Torah", "Prophets", "Writings"]

# Primary version — Public Domain confirmed
VERSION_TITLE  = "The Holy Scriptures A New Translation JPS 1917"
# Fallback if a book is missing the JPS 1917 file
FALLBACK_TITLE = "Sefaria Community Translation"

ALLOWED_LICENCES = {"Public Domain", "CC0", "CC-BY", "CC-BY-SA"}

CORPUS_CODE = "tanakh-jps1917"
CORPUS_NAME = "Tanakh (JPS 1917)"
TRADITION   = "judaism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.sefaria.org"

# Chunking thresholds (in verses)
MIN_VERSES    = 6    # chapters shorter than this are merged with the next
CHUNK_TARGET  = 22  # target verses per sub-chunk when splitting long chapters
MAX_VERSES    = 45  # chapters longer than this are split

EMBED_BATCH = 40    # chunks per Voyage call group
MAX_RETRIES = 5
RETRY_DELAY = 30


# ── Helpers ────────────────────────────────────────────────────────────────────

_HTML_TAG = re.compile(r'<[^>]+>')
_WHITESPACE = re.compile(r'\s+')

def _strip_html(text: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    text = _HTML_TAG.sub(' ', text or '')
    return _WHITESPACE.sub(' ', text).strip()


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _list_gcs_prefixes(prefix: str) -> list[str]:
    """Return subdirectory names immediately under a GCS prefix."""
    url = f"{GCS_BASE}/?prefix={quote(prefix, safe='/')}&delimiter=/"
    try:
        resp = httpx.get(url, timeout=30.0)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] GCS listing failed for {prefix}: {e}")
        return []
    raw = re.findall(r'<Prefix>(.*?)</Prefix>', resp.text)
    names = []
    for p in raw:
        if p.endswith('/') and p != prefix:
            name = p[len(prefix):].rstrip('/')
            if name:
                names.append(name)
    return sorted(names)


def _download_version(category: str, book: str, version: str) -> dict | None:
    """Download a specific text version from the GCS bucket. Returns None on 404."""
    filename = f"{version}.json"
    url = (f"{GCS_BASE}/json/Tanakh/{quote(category, safe='')}"
           f"/{quote(book, safe='')}/English/{quote(filename, safe='')}")
    try:
        resp = httpx.get(url, timeout=60.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [WARN] Download failed ({url[:80]}): {e}")
        return None


def _make_chunks(book_name: str, text_array: list, collection: str) -> list[dict]:
    """
    Convert a Sefaria text array (list of chapters, each a list of verse strings)
    into chunk dicts ready for ingestion.
    """
    chunks: list[dict] = []
    buffer_verses: list[str] = []
    buffer_start_ch = 1

    def _flush(verses: list[str], start_ch: int, end_ch: int, start_v: int = 1) -> None:
        text = ' '.join(verses)
        if not text:
            return
        if start_ch == end_ch and start_v == 1:
            ref = f"{book_name} {start_ch}"
        else:
            ref = f"{book_name} {start_ch}:{start_v}"
        chunks.append({
            'text':        text,
            'reference':   ref,
            'chapter':     str(start_ch),
            'section':     None,
            'word_count':  len(text.split()),
            'token_count': _approx_tokens(text),
            'collection':  collection,
        })

    for ch_idx, verse_list in enumerate(text_array):
        ch_num = ch_idx + 1
        if not verse_list:
            continue
        clean = [_strip_html(v) for v in verse_list if isinstance(v, str) and v.strip()]
        if not clean:
            continue

        # Long chapter: split into sub-chunks, flush any pending buffer first
        if len(clean) > MAX_VERSES:
            if buffer_verses:
                _flush(buffer_verses, buffer_start_ch, ch_num - 1)
                buffer_verses = []
            for i in range(0, len(clean), CHUNK_TARGET):
                sub = clean[i:i + CHUNK_TARGET]
                start_v = i + 1
                _flush(sub, ch_num, ch_num, start_v)
            buffer_start_ch = ch_num + 1
            continue

        # Short chapter: accumulate into buffer
        if len(clean) < MIN_VERSES:
            if not buffer_verses:
                buffer_start_ch = ch_num
            buffer_verses.extend(clean)
            continue

        # Normal chapter: flush buffer if exists, then flush this chapter
        if buffer_verses:
            buffer_verses.extend(clean)
            _flush(buffer_verses, buffer_start_ch, ch_num)
            buffer_verses = []
            buffer_start_ch = ch_num + 1
        else:
            _flush(clean, ch_num, ch_num)
            buffer_start_ch = ch_num + 1

    if buffer_verses:
        _flush(buffer_verses, buffer_start_ch, buffer_start_ch)

    return chunks


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _upsert_corpus(conn) -> int:
    row = execute_one(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (CORPUS_CODE, CORPUS_NAME, TRADITION, LANGUAGE, LICENSE, BASE_URL))
    conn.commit()
    return row['id']


def _upsert_text(conn, corpus_id: int, book: str, collection: str) -> int:
    external_id = f"tanakh-{book.lower().replace(' ', '-')}"
    url = f"https://www.sefaria.org/{quote(book)}"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language,
             collection, url)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (corpus_id, external_id)
        DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, book, TRADITION, LANGUAGE, collection, url))
    conn.commit()
    return row['id']


# ── Main ───────────────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks  = 0
    total_books   = 0
    skipped_books = 0

    for category in TANAKH_CATEGORIES:
        prefix = f"json/Tanakh/{category}/"
        books = _list_gcs_prefixes(prefix)
        print(f"\n{'='*60}")
        print(f"{category} — {len(books)} books found")
        print(f"{'='*60}")

        for book in books:
            print(f"\n  [{category}] {book}", end=' ... ', flush=True)

            # Download primary version, fall back to community translation
            data = _download_version(category, book, VERSION_TITLE)
            used_version = VERSION_TITLE
            if data is None:
                data = _download_version(category, book, FALLBACK_TITLE)
                used_version = FALLBACK_TITLE
            if data is None:
                print("NOT FOUND — skip")
                skipped_books += 1
                continue

            lic = data.get('license', '')
            if lic not in ALLOWED_LICENCES:
                print(f"BLOCKED ({lic!r}) — skip")
                skipped_books += 1
                continue

            text_array = data.get('text') or []
            if not text_array:
                print("empty text — skip")
                skipped_books += 1
                continue

            text_id = _upsert_text(conn, corpus_id, book, category)

            # Skip if already ingested (unless --force)
            if not force:
                existing = execute_one(conn,
                    "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s",
                    (text_id,))
                if existing and existing['n'] > 0:
                    n = existing['n']
                    print(f"already ingested ({n} chunks) — skip")
                    total_chunks += n
                    total_books  += 1
                    continue

            if force:
                execute(conn,
                    "DELETE FROM chunk_embeddings WHERE chunk_id IN "
                    "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                execute(conn, "DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
                conn.commit()

            chunks = _make_chunks(book, text_array, category)
            if not chunks:
                print("no chunks produced — skip")
                skipped_books += 1
                continue

            print(f"{len(chunks)} chunks ({used_version[:12]}..)" if len(used_version) > 12 else f"{len(chunks)} chunks ({used_version})")

            # Embed and store
            texts = [c['text'] for c in chunks]
            written = 0

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    embeddings = embed_documents(texts)
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        print(f"  [WARN] Voyage attempt {attempt}/{MAX_RETRIES}: {e}. Retry in {RETRY_DELAY}s...")
                        time.sleep(RETRY_DELAY)
                    else:
                        print(f"  [ERROR] Voyage failed after {MAX_RETRIES} attempts: {e}")
                        embeddings = None
                        break

            if embeddings is None:
                continue

            try:
                for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
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
                            chunk['chapter'], chunk['section'],
                            chunk['word_count'], chunk['token_count'],
                            None,           # entity_ids
                            LANGUAGE, TRADITION, CORPUS_CODE,
                            chunk['collection'], True,  # is_verse=True for Tanakh
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
                total_books  += 1
                print(f"  ✓ {written} chunks committed")
            except Exception as e:
                print(f"  [ERROR] write failed for {book}: {e}")
                try:
                    conn.rollback()
                except Exception:
                    conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Tanakh ingestion complete.")
    print(f"  Books ingested : {total_books}")
    print(f"  Books skipped  : {skipped_books}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Tanakh — JPS 1917 (Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
