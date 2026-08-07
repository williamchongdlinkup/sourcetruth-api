# -*- coding: utf-8 -*-
"""
Classical Latin Literature ingestion (Public Domain translations from Project Gutenberg).

Texts included:
  - Julius Caesar, De Bello Gallico (Gallic Wars)  — McDevitte/Bohn, 1851  (#10657)
  - Virgil, The Aeneid                              — John Dryden, 1697     (#22456)
  - Lucretius, De Rerum Natura (On the Nature of Things) — William Leonard, 1916 (#785)
  - Tacitus, The Reign of Tiberius (Annals I-VI)   — Church/Brodribb, 1876 (#7959)
  - Cicero, De Officiis (On Duties)                 — Anonymous trans., 1913 (#47001)
  - Cicero, De Amicitia (On Friendship)             — Shuckburgh, 1900      (#7491)

All translators died before 1956 and/or translations published before 1926 — Public Domain.

Corpus code : classical-latin
Tradition   : classicism
Chunking    : text-specific: books/chapters for prose, canto/book for poetry (~350 words)
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()
_pooler = os.environ.get("POOLER_DATABASE_URL")
if _pooler:
    os.environ["DATABASE_URL"] = _pooler

import re
import sys
import time
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, execute_one
from embed import embed_documents

CORPUS_CODE = "classical-latin"
CORPUS_NAME = "Classical Latin Literature"
TRADITION   = "classicism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org"

TEXTS = [
    {
        "external_id":  "caesar-gallic-wars",
        "title":        "De Bello Gallico (The Gallic Wars)",
        "author":       "Julius Caesar",
        "translator":   "W. A. McDevitte and W. S. Bohn (1851)",
        "gutenberg_id": 10657,
        "url":          "https://www.gutenberg.org/cache/epub/10657/pg10657.txt",
        "parse_mode":   "books",
        "collection":   "Caesar",
        "book_pattern": r'(?:^|\n)(BOOK\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH)|Book\s+[IVX]+\b)',
    },
    {
        "external_id":  "virgil-aeneid-dryden",
        "title":        "The Aeneid",
        "author":       "Virgil",
        "translator":   "John Dryden (1697)",
        "gutenberg_id": 22456,
        "url":          "https://www.gutenberg.org/cache/epub/22456/pg22456.txt",
        "parse_mode":   "poetry_books",
        "collection":   "Virgil",
        "book_pattern": r'(?:^|\n)\s*(BOOK\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)|BOOK\s+[IVX]+\.?\s*$)',
    },
    {
        "external_id":  "lucretius-de-rerum-natura",
        "title":        "De Rerum Natura (On the Nature of Things)",
        "author":       "Lucretius",
        "translator":   "William Leonard (1916)",
        "gutenberg_id": 785,
        "url":          "https://www.gutenberg.org/cache/epub/785/pg785.txt",
        "parse_mode":   "poetry_books",
        "collection":   "Lucretius",
        "book_pattern": r'(?:^|\n)\s*(BOOK\s+[IVX]+\.?\s*$|BOOK\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH))',
    },
    {
        "external_id":  "tacitus-annals-tiberius",
        "title":        "The Reign of Tiberius (Annals I-VI)",
        "author":       "Cornelius Tacitus",
        "translator":   "Thomas Gordon (1737) / Church & Brodribb (1876)",
        "gutenberg_id": 7959,
        "url":          "https://www.gutenberg.org/cache/epub/7959/pg7959.txt",
        "parse_mode":   "books",
        "collection":   "Tacitus",
        "book_pattern": r'(?:^|\n)\s*(BOOK\s+[IVX]+\.?\s*$|BOOK\s+THE\s+(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH))',
    },
    {
        "external_id":  "cicero-de-officiis",
        "title":        "De Officiis (On Duties)",
        "author":       "Marcus Tullius Cicero",
        "translator":   "Walter Miller (1913)",
        "gutenberg_id": 47001,
        "url":          "https://www.gutenberg.org/cache/epub/47001/pg47001.txt",
        "parse_mode":   "books",
        "collection":   "Cicero",
        "book_pattern": r'(?:^|\n)\s*(BOOK\s+[IVX]+\.?\s*$|BOOK\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD))',
    },
    {
        "external_id":  "cicero-de-amicitia",
        "title":        "De Amicitia (On Friendship)",
        "author":       "Marcus Tullius Cicero",
        "translator":   "Evelyn Shuckburgh (1900)",
        "gutenberg_id": 7491,
        "url":          "https://www.gutenberg.org/cache/epub/7491/pg7491.txt",
        "parse_mode":   "paragraphs",
        "collection":   "Cicero",
        "book_pattern": None,
    },
]

TARGET_WORDS = 350
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_WHITESPACE = re.compile(r'\s+')


def _strip_gutenberg(text: str) -> str:
    for marker in ["*** START OF THE PROJECT GUTENBERG EBOOK",
                   "*** START OF THIS PROJECT GUTENBERG EBOOK"]:
        idx = text.find(marker)
        if idx >= 0:
            after = text[idx + len(marker):]
            nl = after.find('\n')
            text = after[nl+1:] if nl >= 0 else after
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


def _clean(s: str) -> str:
    return _WHITESPACE.sub(' ', (s or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _chunk_paragraphs(paras: list[str], ref_prefix: str) -> list[dict]:
    chunks = []
    buffer: list[str] = []
    chunk_num = 1

    for para in paras:
        words = para.split()
        if not words:
            continue
        buf_words = sum(len(b.split()) for b in buffer)
        if buffer and buf_words + len(words) > TARGET_WORDS and buf_words >= MIN_WORDS:
            txt = ' '.join(buffer)
            chunks.append({'text': txt, 'reference': f"{ref_prefix} — passage {chunk_num}",
                           'chapter': ref_prefix,
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            chunk_num += 1
            buffer = [para]
        else:
            buffer.append(para)

    if buffer:
        txt = ' '.join(buffer)
        chunks.append({'text': txt, 'reference': f"{ref_prefix} — passage {chunk_num}",
                       'chapter': ref_prefix,
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
    return chunks


def _parse_by_books(text: str, text_def: dict) -> list[dict]:
    """Split text at book boundaries, then paragraph-chunk each book."""
    pattern = text_def.get('book_pattern')
    if not pattern:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, text_def['title'])

    book_re = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    matches = list(book_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, text_def['title'])

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip() if match.lastindex else f"Section {bi+1}"
        start = match.start()
        end   = matches[bi+1].start() if bi+1 < len(matches) else len(text)
        body  = text[start:end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
        sub   = _chunk_paragraphs(paras, f"{text_def['title']}, {book_label}")
        chunks.extend(sub)

    return chunks


def _parse_poetry_books(text: str, text_def: dict) -> list[dict]:
    """Poetry: split at book boundaries, then ~TARGET_WORDS line-group chunks."""
    pattern = text_def.get('book_pattern')
    if not pattern:
        lines = [_clean(l) for l in text.split('\n') if _clean(l) and len(_clean(l).split()) > 2]
        return _chunk_paragraphs(lines, text_def['title'])

    book_re = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    matches = list(book_re.finditer(text))

    if not matches:
        lines = [_clean(l) for l in text.split('\n') if _clean(l) and len(_clean(l).split()) > 2]
        return _chunk_paragraphs(lines, text_def['title'])

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip() if match.lastindex else f"Book {bi+1}"
        start = match.start()
        end   = matches[bi+1].start() if bi+1 < len(matches) else len(text)
        body  = text[start:end]
        # Group into ~TARGET_WORDS chunks, respecting line breaks
        lines = [_clean(l) for l in body.split('\n') if _clean(l) and len(_clean(l).split()) > 2]
        sub   = _chunk_paragraphs(lines, f"{text_def['title']}, {book_label}")
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
    url = f"https://www.gutenberg.org/ebooks/{text_def['gutenberg_id']}"
    display = f"{text_def['title']} — {text_def['author']} (trans. {text_def['translator']})"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url, translator)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, text_def['external_id'], display, TRADITION, LANGUAGE,
          text_def['collection'], url, text_def['translator']))
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
        print(f"Ingesting: {text_def['title']} by {text_def['author']}")
        print(f"  Source: Gutenberg #{text_def['gutenberg_id']}")

        raw = None
        for attempt in range(3):
            try:
                resp = httpx.get(text_def['url'], timeout=120.0, follow_redirects=True,
                                 headers={'Accept-Encoding': 'identity'})
                if resp.status_code == 404:
                    print(f"  404 — skipping.")
                    break
                resp.raise_for_status()
                raw = resp.content.decode('utf-8', errors='replace')
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  Retry {attempt+1}: {e}")
                    time.sleep(5)
                else:
                    print(f"  [ERROR] Failed: {e}")

        if raw is None:
            continue

        text = _strip_gutenberg(raw)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        mode = text_def['parse_mode']

        if mode == 'poetry_books':
            chunks = _parse_poetry_books(text, text_def)
        elif mode == 'paragraphs':
            paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
            chunks = _chunk_paragraphs(paras, text_def['title'])
        else:
            chunks = _parse_by_books(text, text_def)

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
                cur.execute(
                    "DELETE FROM chunk_embeddings WHERE chunk_id IN "
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
                        chunk.get('chapter'), None,
                        chunk['word_count'], chunk['token_count'],
                        None, LANGUAGE, TRADITION, CORPUS_CODE,
                        text_def['collection'], False,
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
            print(f"  ✓ {written} chunks committed")
        except Exception as e:
            print(f"  [ERROR] write failed for {text_def['title']}: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Classical Latin ingestion complete.")
    print(f"  Texts ingested : {total_texts}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Classical Latin Literature (Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
