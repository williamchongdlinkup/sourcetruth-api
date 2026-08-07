# -*- coding: utf-8 -*-
"""
Yoga Sutras of Patanjali ingestion (Public Domain).

Source: Project Gutenberg #2526
  "The Yoga Sutras of Patanjali: The Book of the Spiritual Man"
  Translated with commentary by Charles Johnston (1912).
  Johnston died 1943; translation published 1912 — Public Domain in USA.

Chunking: one sutra + commentary block per chunk (~200-400 words).
          Falls back to paragraph grouping if sutra headers not detected.

Corpus code: yoga-sutras
Tradition  : hinduism
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv

load_dotenv()
_pooler = os.environ.get("POOLER_DATABASE_URL")
if _pooler:
    os.environ["DATABASE_URL"] = _pooler

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, execute_one
from embed import embed_documents

GUTENBERG_ID = 2526
URL          = f"https://www.gutenberg.org/cache/epub/{GUTENBERG_ID}/pg{GUTENBERG_ID}.txt"

CORPUS_CODE = "yoga-sutras"
CORPUS_NAME = "Yoga Sutras of Patanjali (Johnston, 1912)"
TRADITION   = "hinduism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = f"https://www.gutenberg.org/ebooks/{GUTENBERG_ID}"

TARGET_WORDS = 250
MIN_WORDS    = 40
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_WHITESPACE = re.compile(r'\s+')


def _clean(s: str) -> str:
    return _WHITESPACE.sub(' ', (s or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _strip_gutenberg(text: str) -> str:
    for marker in ["*** START OF THE PROJECT GUTENBERG EBOOK",
                   "*** START OF THIS PROJECT GUTENBERG EBOOK"]:
        idx = text.find(marker)
        if idx >= 0:
            after = text[idx + len(marker):]
            nl = after.find('\n')
            text = after[nl + 1:] if nl >= 0 else after
            break
    for marker in ["*** END OF THE PROJECT GUTENBERG EBOOK",
                   "*** END OF THIS PROJECT GUTENBERG EBOOK",
                   "End of the Project Gutenberg",
                   "End of Project Gutenberg"]:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
            break
    return text.strip()


def _chunk_paragraphs(paras: list[str], ref_prefix: str) -> list[dict]:
    chunks = []
    buffer: list[str] = []
    buf_words = 0
    chunk_num = 1
    for para in paras:
        words = para.split()
        if not words:
            continue
        if buffer and buf_words + len(words) > TARGET_WORDS and buf_words >= MIN_WORDS:
            txt = ' '.join(buffer)
            chunks.append({'text': txt,
                           'reference': f"{ref_prefix} §{chunk_num}",
                           'chapter': ref_prefix,
                           'word_count': len(txt.split()),
                           'token_count': _approx_tokens(txt)})
            chunk_num += 1
            buffer = [para]
            buf_words = len(words)
        else:
            buffer.append(para)
            buf_words += len(words)
    if buffer:
        txt = ' '.join(buffer)
        chunks.append({'text': txt,
                       'reference': f"{ref_prefix} §{chunk_num}",
                       'chapter': ref_prefix,
                       'word_count': len(txt.split()),
                       'token_count': _approx_tokens(txt)})
    return chunks


def _parse_yoga_sutras(text: str) -> list[dict]:
    """
    The Johnston text is divided into 4 Books (Pada):
      Book I  — Concentration (Samadhi Pada)
      Book II — Means (Sadhana Pada)
      Book III— Powers (Vibhuti Pada)
      Book IV — Liberation (Kaivalya Pada)
    Each sutra has a heading like "1." or "1. SUTRA" followed by commentary.
    Strategy: find Book headers, then chunk by sutra number within each book.
    """
    # Match Book headers
    book_re = re.compile(
        r'(?:^|\n)(BOOK\s+(?:I{1,3}V?|V?I{0,3}|THE\s+\w+|FIRST|SECOND|THIRD|FOURTH)[\s\S]*?PADA)',
        re.IGNORECASE | re.MULTILINE
    )
    books = list(book_re.finditer(text))

    # Fallback: simpler "BOOK I" pattern
    if not books:
        book_re = re.compile(
            r'(?:^|\n)(BOOK\s+(?:I{1,4}V?|V?I{0,4}))\s*\n',
            re.MULTILINE
        )
        books = list(book_re.finditer(text))

    if not books:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Yoga Sutras")

    chunks = []
    for bi, match in enumerate(books):
        book_label = _clean(match.group(1))
        bk_start   = match.start()
        bk_end     = books[bi + 1].start() if bi + 1 < len(books) else len(text)
        book_body  = text[bk_start:bk_end]

        # Try to split by sutra numbers (lines like "1." at start)
        sutra_re = re.compile(r'(?:^|\n)\s*(\d{1,3})\.\s+', re.MULTILINE)
        sutras   = list(sutra_re.finditer(book_body))

        if len(sutras) < 3:
            paras = [_clean(p) for p in re.split(r'\n{2,}', book_body) if p.strip() and len(p.split()) > 5]
            sub   = _chunk_paragraphs(paras, book_label)
            chunks.extend(sub)
            continue

        # Group sutras into chunks targeting TARGET_WORDS
        buffer_sutras: list[tuple[str, str]] = []  # (num, text)
        buf_words = 0

        def _flush(buf: list[tuple[str, str]]) -> None:
            if not buf:
                return
            txt = ' '.join(_clean(s[1]) for s in buf)
            first, last = buf[0][0], buf[-1][0]
            ref = (f"{book_label} Sutra {first}" if first == last
                   else f"{book_label} Sutras {first}–{last}")
            chunks.append({'text': txt, 'reference': ref, 'chapter': book_label,
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})

        for si, sm in enumerate(sutras):
            s_start = sm.start()
            s_end   = sutras[si + 1].start() if si + 1 < len(sutras) else len(book_body)
            s_num   = sm.group(1)
            s_text  = book_body[s_start:s_end]
            s_words = len(s_text.split())

            if buffer_sutras and buf_words + s_words > TARGET_WORDS and buf_words >= MIN_WORDS:
                _flush(buffer_sutras)
                buffer_sutras = [(s_num, s_text)]
                buf_words     = s_words
            else:
                buffer_sutras.append((s_num, s_text))
                buf_words += s_words

        _flush(buffer_sutras)

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


def _upsert_text(conn, corpus_id: int) -> int:
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, "yoga-sutras-johnston",
          "The Yoga Sutras of Patanjali — Charles Johnston (1912)",
          TRADITION, LANGUAGE, "Yoga Texts", BASE_URL))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    print(f"\nDownloading Gutenberg #{GUTENBERG_ID} ...")
    raw = None
    for attempt in range(3):
        try:
            resp = httpx.get(URL, timeout=120.0, follow_redirects=True)
            if resp.status_code == 404:
                print("  404 — not found on Gutenberg.")
                conn.close()
                return
            resp.raise_for_status()
            raw = resp.text
            break
        except Exception as e:
            if attempt < 2:
                print(f"  Retry {attempt + 1}: {e}")
                time.sleep(5)
            else:
                print(f"  [ERROR] Download failed: {e}")

    if raw is None:
        conn.close()
        return

    text   = _strip_gutenberg(raw).replace('\r\n', '\n').replace('\r', '\n')
    chunks = _parse_yoga_sutras(text)
    print(f"  Parsed {len(chunks)} chunks")

    text_id = _upsert_text(conn, corpus_id)

    if not force:
        existing = execute_one(conn,
            "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
        if existing and existing['n'] > 0:
            print(f"  Already ingested ({existing['n']} chunks) — skip (use --force to re-embed)")
            conn.close()
            return

    if force:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN "
                        "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
            cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
        conn.commit()

    if not chunks:
        print("  No chunks — skip")
        conn.close()
        return

    embeddings = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            embeddings = embed_documents([c['text'] for c in chunks])
            break
        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"  [WARN] Voyage attempt {attempt}: {e}. Retry in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  [ERROR] Voyage failed: {e}")

    if embeddings is None:
        conn.close()
        return

    written = 0
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
                """, (text_id, idx, chunk['text'], chunk['reference'],
                      chunk['chapter'], None,
                      chunk['word_count'], chunk['token_count'],
                      None, LANGUAGE, TRADITION, CORPUS_CODE, "Yoga Texts", False))
                chunk_id = cur.fetchone()['id']
            with conn.cursor() as cur:
                cur.execute("INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                            (chunk_id, emb_np))
            written += 1
        conn.commit()
        print(f"  ✓ {written} chunks committed")
    except Exception as e:
        print(f"  [ERROR] Write failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass

    conn.close()
    print(f"\n{'='*60}")
    print(f"Yoga Sutras ingestion complete. {written} chunks written.")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Yoga Sutras of Patanjali (Johnston, PD)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
