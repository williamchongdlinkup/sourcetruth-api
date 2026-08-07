# -*- coding: utf-8 -*-
"""
Tibetan Buddhist Texts ingestion (Public Domain).

Texts included:
  - The Tibetan Book of the Dead (Bardo Thodol)
    Trans. Lama Kazi Dawa-Samdup (1927), ed. W. Y. Evans-Wentz
    Internet Archive: the-tibetan-book-of-the-dead_202401 (DjVu OCR)
    Published 1927 by Oxford University Press — PD in USA (published before 1928)

  - Tibet's Great Yogi Milarepa
    Trans./ed. W. Y. Evans-Wentz (1928)
    Internet Archive: tibetsgreatyo00evan
    Published 1928 — PD in USA

Corpus code : tibetan-buddhist
Tradition   : vajrayana
Language    : en
"""
from __future__ import annotations

import os
import re
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

CORPUS_CODE = "tibetan-buddhist"
CORPUS_NAME = "Tibetan Buddhist Texts (Evans-Wentz, 1927-28)"
TRADITION   = "vajrayana"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://archive.org"

IA_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}

TEXTS = [
    {
        "external_id":  "bardo-thodol-evans-wentz",
        "title":        "The Tibetan Book of the Dead (Bardo Thodol)",
        "author":       "Lama Kazi Dawa-Samdup (trans.), W. Y. Evans-Wentz (ed.)",
        "translator":   "Kazi Dawa-Samdup / Evans-Wentz (1927)",
        "ia_url":       "https://archive.org/download/the-tibetan-book-of-the-dead_202401/the%20tibetan%20book%20of%20the%20dead_djvu.txt",
        "parse_mode":   "sections",
        "collection":   "Bardo Thodol",
    },
    {
        "external_id":  "milarepa-evans-wentz",
        "title":        "Tibet's Great Yogi Milarepa",
        "author":       "Jetsün Milarepa",
        "translator":   "W. Y. Evans-Wentz (1928)",
        "ia_url":       "https://archive.org/download/tibetsgreatyo00evan/tibetsgreatyo00evan_djvu.txt",
        "parse_mode":   "chapters",
        "collection":   "Milarepa",
    },
]

TARGET_WORDS = 400
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_WHITESPACE  = re.compile(r'\s+')
_MULTI_SPACE = re.compile(r'[ \t]{2,}')


def _clean_djvu(text: str) -> str:
    """Remove Internet Archive DjVu OCR artefacts."""
    # Remove page markers
    text = re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=re.MULTILINE)
    # Fix spaced-out letter sequences
    text = re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                  lambda m: m.group(0).replace(' ', ''), text)
    # Remove Google Books/IA boilerplate in first 5KB
    for marker in ['This is a digital copy', 'google.com/books',
                   'Oxford University Press\nAmen House', 'OXFORD UNIVERSITY']:
        idx = text.find(marker)
        if 0 <= idx < 8000:
            # Find first substantive content marker
            for content_marker in ['PREFACE', 'FOREWORD', 'INTRODUCTION',
                                    'CHAPTER I', 'PART I', 'SECTION I', 'BOOK I']:
                cm_idx = text.find(content_marker, idx)
                if cm_idx > 0:
                    text = text[cm_idx:]
                    break
    # Fix hyphenation
    text = re.sub(r'-\n\s+', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _clean(s: str) -> str:
    return _WHITESPACE.sub(' ', (s or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


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
            chunks.append({'text': txt, 'reference': f"{ref_prefix} — §{chunk_num}",
                           'chapter': ref_prefix,
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            chunk_num += 1
            buffer = [para]
            buf_words = len(words)
        else:
            buffer.append(para)
            buf_words += len(words)

    if buffer:
        txt = ' '.join(buffer)
        chunks.append({'text': txt, 'reference': f"{ref_prefix} — §{chunk_num}",
                       'chapter': ref_prefix,
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
    return chunks


def _parse_sections(text: str) -> list[dict]:
    """
    Tibetan Book of the Dead: split at chapter/section/part headers.
    """
    section_re = re.compile(
        r'(?:^|\n)((?:PART|CHAPTER|SECTION|BOOK|THE\s+\w+\s+DAY|INTRODUCTION|PREFACE|'
        r'APPENDIX|INVOCATION)\s*[IVXLC\d]*\.?\s*[\w\s,\-]*?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(section_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 10]
        return _chunk_paragraphs(paras, "Tibetan Book of the Dead")

    chunks = []
    for si, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[si+1].start() if si+1 < len(matches) else len(text)
        body  = text[start:end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 10]
        sub   = _chunk_paragraphs(paras, f"Bardo Thodol — {label}")
        chunks.extend(sub)

    return chunks


def _parse_chapters(text: str) -> list[dict]:
    """
    Milarepa: split at chapter headers.
    """
    chap_re = re.compile(
        r'(?:^|\n)(CHAPTER\s+[IVXLC\d]+\.?\s*[\w\s,\-]*?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(chap_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 10]
        return _chunk_paragraphs(paras, "Milarepa")

    chunks = []
    for ci, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[ci+1].start() if ci+1 < len(matches) else len(text)
        body  = text[start:end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 10]
        sub   = _chunk_paragraphs(paras, f"Milarepa — {label}")
        chunks.extend(sub)

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


def _upsert_text(conn, corpus_id: int, text_def: dict) -> int:
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url, translator)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, text_def['external_id'],
          f"{text_def['title']} — {text_def['author']} (trans. {text_def['translator']})",
          TRADITION, LANGUAGE, text_def['collection'], text_def['ia_url'],
          text_def['translator']))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for text_def in TEXTS:
        print(f"\n{'='*60}")
        print(f"Ingesting: {text_def['title']}")
        print(f"  Source: Internet Archive — {text_def['ia_url'].split('/')[-1]}")

        raw = None
        for attempt in range(3):
            try:
                resp = httpx.get(text_def['ia_url'], timeout=90.0, follow_redirects=True,
                                 headers=IA_HEADERS)
                if resp.status_code == 404:
                    print(f"  404 — skipping.")
                    break
                resp.raise_for_status()
                raw = resp.content.decode('utf-8', errors='replace')
                print(f"  Downloaded {len(raw)//1024}KB")
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  Retry {attempt+1}: {e}")
                    time.sleep(10)
                else:
                    print(f"  [ERROR] Failed: {e}")

        if raw is None:
            continue

        raw = _clean_djvu(raw).replace('\r\n', '\n').replace('\r', '\n')
        mode = text_def['parse_mode']

        if mode == 'sections':
            chunks = _parse_sections(raw)
        else:
            chunks = _parse_chapters(raw)

        print(f"  Parsed {len(chunks)} chunks (mode={mode})")
        if not chunks:
            print("  No chunks produced — skip")
            continue

        text_id = _upsert_text(conn, corpus_id, text_def)

        if not force:
            existing = execute_one(conn,
                "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
            if existing and existing['n'] > 0:
                n = existing['n']
                print(f"  Already ingested ({n} chunks) — skip")
                total_chunks += n
                total_texts  += 1
                continue

        if force:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN "
                            "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
            conn.commit()

        texts_to_embed = [c['text'] for c in chunks]
        embeddings = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = embed_documents(texts_to_embed)
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    print(f"  [WARN] Voyage attempt {attempt}/{MAX_RETRIES}: {e}. Retry {RETRY_DELAY}s...")
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
                    """, (text_id, idx, chunk['text'], chunk['reference'],
                          chunk.get('chapter'), None,
                          chunk['word_count'], chunk['token_count'],
                          None, LANGUAGE, TRADITION, CORPUS_CODE,
                          text_def['collection'], False))
                    chunk_id = cur.fetchone()['id']
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                                (chunk_id, emb_np))
                written += 1
            conn.commit()
            total_chunks += written
            total_texts  += 1
            print(f"  ✓ {written} chunks committed")
        except Exception as e:
            print(f"  [ERROR] write failed: {e}")
            try: conn.rollback()
            except Exception: conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Tibetan Buddhist ingestion complete.")
    print(f"  Texts ingested : {total_texts}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Tibetan Buddhist Texts (Evans-Wentz 1927-28, PD)')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    run(force=args.force)
