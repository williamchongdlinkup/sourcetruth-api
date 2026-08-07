# -*- coding: utf-8 -*-
"""
Multi-translation Bible ingestion (Public Domain / CC-0).

Sources via getbible.net API:
  - World English Bible (WEB): 1997/2012, Public Domain (CC-0)
  - American Standard Version (ASV): 1901, Public Domain
  - Young's Literal Translation (YLT): 1862/1898, Public Domain

Chunking: Chapter-level (same as KJV). Short chapters merged to meet MIN_WORDS.
Reference format: "Genesis 1" (ASV), "Matthew 5" (WEB), etc.

Corpus codes: bible-web, bible-asv, bible-ylt
Tradition   : christianity
Language    : en
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()
_pooler = os.environ.get("POOLER_DATABASE_URL")
if _pooler:
    os.environ["DATABASE_URL"] = _pooler

import sys
import time
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, execute_one
from embed import embed_documents

GETBIBLE_BASE = "https://api.getbible.net/v2"

TRANSLATIONS = [
    {
        "code":        "bible-web",
        "name":        "Bible — World English Bible (WEB, 2012)",
        "abbrev":      "web",
        "translator":  "World English Bible (Public Domain, 2012)",
        "license":     "Public Domain",
    },
    {
        "code":        "bible-asv",
        "name":        "Bible — American Standard Version (ASV, 1901)",
        "abbrev":      "asv",
        "translator":  "American Standard Version Committee (1901)",
        "license":     "Public Domain",
    },
    {
        "code":        "bible-ylt",
        "name":        "Bible — Young's Literal Translation (YLT, 1898)",
        "abbrev":      "ylt",
        "translator":  "Robert Young (1898)",
        "license":     "Public Domain",
    },
]

TRADITION   = "christianity"
LANGUAGE    = "en"
BASE_URL    = "https://api.getbible.net"

TARGET_WORDS = 400
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _fetch_json(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = httpx.get(url, timeout=30.0, follow_redirects=True)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                print(f"  [WARN] Retry {attempt+1}: {e}")
                time.sleep(3)
            else:
                print(f"  [ERROR] Failed {url}: {e}")
    return None


def _chunk_book(book_data: dict) -> list[dict]:
    """
    Chunk a Bible book by chapter. Short chapters are merged with the next
    until MIN_WORDS is reached; long chapters are a single chunk.
    """
    chapters = book_data.get('chapters', [])
    book_name = book_data.get('name', 'Unknown')

    chapter_texts: list[tuple[int, str]] = []
    for chap in chapters:
        chap_num = chap.get('chapter', 0)
        verses   = chap.get('verses', [])
        # Concatenate all verses in this chapter
        chap_text = ' '.join(
            v.get('text', '').strip()
            for v in sorted(verses, key=lambda v: v.get('verse', 0))
            if v.get('text', '').strip()
        )
        if chap_text:
            chapter_texts.append((chap_num, chap_text))

    if not chapter_texts:
        return []

    chunks = []
    buffer_chapters: list[tuple[int, str]] = []
    buf_words = 0

    def flush():
        if not buffer_chapters:
            return
        first_num = buffer_chapters[0][0]
        last_num  = buffer_chapters[-1][0]
        txt = ' '.join(t for _, t in buffer_chapters)
        ref = (f"{book_name} {first_num}"
               if first_num == last_num
               else f"{book_name} {first_num}-{last_num}")
        chunks.append({'text': txt, 'reference': ref, 'chapter': str(first_num),
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})

    for chap_num, chap_text in chapter_texts:
        cw = len(chap_text.split())
        if buffer_chapters and buf_words + cw > TARGET_WORDS and buf_words >= MIN_WORDS:
            flush()
            buffer_chapters = [(chap_num, chap_text)]
            buf_words = cw
        else:
            buffer_chapters.append((chap_num, chap_text))
            buf_words += cw

    flush()
    return chunks


def _upsert_corpus(conn, trans: dict) -> int:
    row = execute_one(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (trans['code'], trans['name'], TRADITION, LANGUAGE, trans['license'], BASE_URL))
    conn.commit()
    return row['id']


def _upsert_text(conn, corpus_id: int, book_name: str, book_nr: int, trans: dict) -> int:
    external_id = f"{trans['abbrev']}-book-{book_nr:03d}"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url, translator)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, book_name, TRADITION, LANGUAGE, "Bible",
          f"{GETBIBLE_BASE}/{trans['abbrev']}/{book_nr}.json", trans['translator']))
    conn.commit()
    return row['id']


def run_translation(trans: dict, force: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"Translation: {trans['name']}")
    print(f"  Fetching book list ...")

    books_data = _fetch_json(f"{GETBIBLE_BASE}/{trans['abbrev']}/books.json")
    if not books_data:
        print(f"  [ERROR] Could not fetch books list for {trans['abbrev']}")
        return

    # books.json is a dict: { "1": {"nr": 1, "name": "Genesis", "chapters": 50}, ... }
    book_entries = []
    if isinstance(books_data, dict):
        for key, val in books_data.items():
            if isinstance(val, dict):
                book_entries.append((val.get('nr', int(key)), val.get('name', key)))
            elif isinstance(val, str):
                book_entries.append((int(key), val))
    elif isinstance(books_data, list):
        for item in books_data:
            book_entries.append((item.get('nr', 0), item.get('name', '')))

    book_entries = sorted(book_entries, key=lambda x: x[0])
    print(f"  Found {len(book_entries)} books")

    conn = get_conn()
    corpus_id = _upsert_corpus(conn, trans)
    print(f"  Corpus '{trans['code']}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for book_nr, book_name in book_entries:
        text_id = _upsert_text(conn, corpus_id, book_name, book_nr, trans)

        if not force:
            existing = execute_one(conn,
                "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
            if existing and existing['n'] > 0:
                n = existing['n']
                total_chunks += n
                total_texts  += 1
                continue

        if force:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN "
                            "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
            conn.commit()

        book_data = _fetch_json(f"{GETBIBLE_BASE}/{trans['abbrev']}/{book_nr}.json")
        if not book_data:
            print(f"  [WARN] Skipping {book_name} (fetch failed)")
            continue

        chunks = _chunk_book(book_data)
        if not chunks:
            print(f"  [WARN] {book_name}: no chunks")
            continue

        print(f"  {book_name}: {len(chunks)} chunks ...", end=' ', flush=True)

        texts_to_embed = [c['text'] for c in chunks]
        embeddings = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = embed_documents(texts_to_embed)
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    print(f"\n  [WARN] Voyage attempt {attempt}: {e}. Retry {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"\n  [ERROR] Voyage failed for {book_name}: {e}")

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
                    """, (text_id, idx, chunk['text'], chunk['reference'],
                          chunk.get('chapter'), None,
                          chunk['word_count'], chunk['token_count'],
                          None, LANGUAGE, TRADITION, trans['code'],
                          "Bible", True))
                    chunk_id = cur.fetchone()['id']
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                                (chunk_id, emb_np))
                written += 1
            conn.commit()
            total_chunks += written
            total_texts  += 1
            print(f"✓ {written}")
        except Exception as e:
            print(f"\n  [ERROR] write failed for {book_name}: {e}")
            try: conn.rollback()
            except Exception: conn = get_conn()

    conn.close()
    print(f"\n  {trans['name']}: {total_texts} books, {total_chunks:,} chunks")


def run(translations: list[str] | None = None, force: bool = False) -> None:
    """
    Run multi-translation Bible ingestion.

    Args:
        translations: List of abbreviations to ingest, e.g. ['web', 'asv'].
                      If None, ingests all three (web, asv, ylt).
        force: Re-embed and overwrite existing chunks.
    """
    to_run = TRANSLATIONS
    if translations:
        to_run = [t for t in TRANSLATIONS if t['abbrev'] in translations]

    for trans in to_run:
        run_translation(trans, force=force)

    print(f"\n{'='*60}")
    print("Multi-translation Bible ingestion complete.")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Ingest Bible translations (WEB, ASV, YLT) from getbible.net API')
    parser.add_argument('--translations', nargs='+', choices=['web', 'asv', 'ylt'],
                        default=['web', 'asv', 'ylt'], help='Which translations to ingest')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing')
    args = parser.parse_args()
    run(translations=args.translations, force=args.force)
