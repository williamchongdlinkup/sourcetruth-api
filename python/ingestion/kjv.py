# -*- coding: utf-8 -*-
"""
KJV Bible ingestion: King James Version 1769 (Public Domain).

Source : thiagobodruk/bible GitHub repo (en_kjv.json)
         License: KJV 1769 text is Public Domain in the USA and globally.
         Format: JSON array of books, each {name, abbrev, chapters: [[verse_strings]]}

Corpus : Full Bible — Old Testament (39 books) + New Testament (27 books)
Chunking: Chapter-level. Short chapters merged into next; long chapters split.
Reference: "Genesis 1" (chapter) or "Genesis 1:1-25" (sub-chunk within chapter)
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()
_pooler = os.environ.get("POOLER_DATABASE_URL")
if _pooler:
    os.environ["DATABASE_URL"] = _pooler

import json
import re
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, execute, execute_one
from embed import embed_documents

JSON_URL = "https://raw.githubusercontent.com/thiagobodruk/bible/master/json/en_kjv.json"

# Fallback ordered book names if JSON lacks "name" field
BOOK_NAMES_ORDERED = [
    "Genesis","Exodus","Leviticus","Numbers","Deuteronomy",
    "Joshua","Judges","Ruth","1 Samuel","2 Samuel",
    "1 Kings","2 Kings","1 Chronicles","2 Chronicles","Ezra",
    "Nehemiah","Esther","Job","Psalms","Proverbs",
    "Ecclesiastes","Song of Solomon","Isaiah","Jeremiah","Lamentations",
    "Ezekiel","Daniel","Hosea","Joel","Amos",
    "Obadiah","Jonah","Micah","Nahum","Habakkuk",
    "Zephaniah","Haggai","Zechariah","Malachi",
    "Matthew","Mark","Luke","John","Acts",
    "Romans","1 Corinthians","2 Corinthians","Galatians","Ephesians",
    "Philippians","Colossians","1 Thessalonians","2 Thessalonians","1 Timothy",
    "2 Timothy","Titus","Philemon","Hebrews","James",
    "1 Peter","2 Peter","1 John","2 John","3 John",
    "Jude","Revelation",
]

NT_BOOKS = {
    "Matthew","Mark","Luke","John","Acts","Romans","1 Corinthians","2 Corinthians",
    "Galatians","Ephesians","Philippians","Colossians","1 Thessalonians","2 Thessalonians",
    "1 Timothy","2 Timothy","Titus","Philemon","Hebrews","James","1 Peter","2 Peter",
    "1 John","2 John","3 John","Jude","Revelation",
}

CORPUS_CODE = "kjv"
CORPUS_NAME = "Holy Bible (King James Version)"
TRADITION   = "christianity"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.biblegateway.com/versions/King-James-Version-KJV-Bible/"

MIN_VERSES   = 5
CHUNK_TARGET = 22
MAX_VERSES   = 45
EMBED_BATCH  = 40
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_WHITESPACE = re.compile(r'\s+')

def _clean(text: str) -> str:
    return _WHITESPACE.sub(' ', (text or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _make_chunks(book_name: str, chapters: list[list[str]]) -> list[dict]:
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
            'word_count':  len(text.split()),
            'token_count': _approx_tokens(text),
        })

    for ch_idx, verse_list in enumerate(chapters):
        ch_num = ch_idx + 1
        if not verse_list:
            continue
        clean = [_clean(v) for v in verse_list if isinstance(v, str) and v.strip()]
        if not clean:
            continue

        if len(clean) > MAX_VERSES:
            if buffer_verses:
                _flush(buffer_verses, buffer_start_ch, ch_num - 1)
                buffer_verses = []
            for i in range(0, len(clean), CHUNK_TARGET):
                sub = clean[i:i + CHUNK_TARGET]
                _flush(sub, ch_num, ch_num, i + 1)
            buffer_start_ch = ch_num + 1
            continue

        if len(clean) < MIN_VERSES:
            if not buffer_verses:
                buffer_start_ch = ch_num
            buffer_verses.extend(clean)
            continue

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


def _upsert_corpus(conn) -> int:
    row = execute_one(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (CORPUS_CODE, CORPUS_NAME, TRADITION, LANGUAGE, LICENSE, BASE_URL))
    conn.commit()
    return row['id']


def _upsert_text(conn, corpus_id: int, book_name: str, testament: str) -> int:
    external_id = f"kjv-{book_name.lower().replace(' ', '-')}"
    url = f"https://www.biblegateway.com/passage/?search={book_name.replace(' ', '+')}&version=KJV"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (corpus_id, external_id)
        DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, book_name, TRADITION, LANGUAGE, testament, url))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    print("Downloading KJV JSON ...")
    for attempt in range(3):
        try:
            resp = httpx.get(JSON_URL, timeout=120.0, follow_redirects=True)
            resp.raise_for_status()
            books_data = resp.json()
            break
        except Exception as e:
            if attempt < 2:
                print(f"  Retry {attempt+1}: {e}")
                time.sleep(5)
            else:
                raise
    print(f"  Downloaded {len(books_data)} books.")

    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_books  = 0
    skipped      = 0

    for book_idx, book_obj in enumerate(books_data):
        # Handle varying JSON structures
        if isinstance(book_obj, dict):
            book_name = (book_obj.get('name') or book_obj.get('abbrev') or
                         (BOOK_NAMES_ORDERED[book_idx] if book_idx < len(BOOK_NAMES_ORDERED) else f"Book {book_idx+1}"))
            chapters_raw = book_obj.get('chapters') or []
        else:
            book_name = BOOK_NAMES_ORDERED[book_idx] if book_idx < len(BOOK_NAMES_ORDERED) else f"Book {book_idx+1}"
            chapters_raw = book_obj if isinstance(book_obj, list) else []

        # Ensure book_name matches exactly our NT lookup
        testament = "New Testament" if book_name in NT_BOOKS else "Old Testament"
        print(f"\n  [{book_idx+1:02d}] {book_name} ({testament})", end=' ... ', flush=True)

        if not chapters_raw:
            print("no chapters — skip")
            skipped += 1
            continue

        text_id = _upsert_text(conn, corpus_id, book_name, testament)

        if not force:
            existing = execute_one(conn,
                "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
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

        # Normalise chapter format: each chapter should be a list of verse strings
        chapters_clean = []
        for ch in chapters_raw:
            if isinstance(ch, list):
                chapters_clean.append(ch)
            elif isinstance(ch, dict):
                # Some formats: {"chapter": N, "verses": ["..."]}
                vv = ch.get('verses') or []
                chapters_clean.append([v if isinstance(v, str) else str(v) for v in vv])
            else:
                chapters_clean.append([str(ch)])

        chunks = _make_chunks(book_name, chapters_clean)
        if not chunks:
            print("no chunks — skip")
            skipped += 1
            continue

        print(f"{len(chunks)} chunks")

        texts = [c['text'] for c in chunks]
        embeddings = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = embed_documents(texts)
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    print(f"  [WARN] Voyage attempt {attempt}/{MAX_RETRIES}: {e}. Retry in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"  [ERROR] Voyage failed: {e}")

        if embeddings is None:
            continue

        try:
            written = 0
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
                        chunk['chapter'], None,
                        chunk['word_count'], chunk['token_count'],
                        None, LANGUAGE, TRADITION, CORPUS_CODE,
                        testament, True,
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
            print(f"    ✓ {written} chunks committed")
        except Exception as e:
            print(f"  [ERROR] write failed for {book_name}: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"KJV ingestion complete.")
    print(f"  Books ingested : {total_books}")
    print(f"  Books skipped  : {skipped}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest KJV Bible (Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
