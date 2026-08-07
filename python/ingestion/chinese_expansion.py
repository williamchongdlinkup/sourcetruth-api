# -*- coding: utf-8 -*-
"""
Classical Chinese expansion: Mencius + I Ching.
Adds to the existing 'classical-chinese' corpus (reuses same corpus code).

Sources:
  - Mencius — James Legge (1861), "Chinese Literature" anthology (Gutenberg #10056)
    Contains "Sayings of Mencius" in Legge's translation — Public Domain.
  - I Ching (Yi King) — James Legge (1882), SBE Vol 16
    Internet Archive: iching00jame — DjVu OCR, 914KB, Public Domain.

Run: python ingestion/chinese_expansion.py
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

# Reuse the existing classical-chinese corpus (adds new texts to it)
CORPUS_CODE = "classical-chinese"
CORPUS_NAME = "Classical Chinese Philosophy"
TRADITION   = "east-asian"
LANGUAGE    = "en"
LICENSE     = "Public Domain"

IA_HEADERS  = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}

TEXTS = [
    {
        "external_id":  "mencius-legge",
        "title":        "The Works of Mencius (Sayings of Mencius)",
        "author":       "Mencius",
        "translator":   "James Legge (1861)",
        "source_type":  "gutenberg",
        # Gutenberg cache CDN drops large files — try direct URL then mirror
        "url":          "https://www.gutenberg.org/files/10056/10056.txt",
        "fallback_urls": [
            "https://gutenberg.org/cache/epub/10056/pg10056.txt",
            "https://www.gutenberg.org/ebooks/10056.txt.utf-8",
        ],
        "parse_mode":   "mencius",
        "collection":   "Confucian Classics",
    },
    {
        "external_id":  "i-ching-legge",
        "title":        "The I Ching (Yi King) — Book of Changes",
        "author":       "Anonymous (attributed to King Wen and Duke of Zhou)",
        "translator":   "James Legge (1882)",
        "source_type":  "ia",
        "url":          "https://archive.org/download/iching00jame/iching00jame_djvu.txt",
        "parse_mode":   "iching",
        "collection":   "Taoist Classics",
    },
]

TARGET_WORDS = 350
MIN_WORDS    = 60
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
            text = after[nl+1:] if nl >= 0 else after
            break
    for marker in ["*** END OF THE PROJECT GUTENBERG EBOOK",
                   "*** END OF THIS PROJECT GUTENBERG EBOOK",
                   "End of the Project Gutenberg"]:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
            break
    return text.strip()


def _clean_djvu(text: str) -> str:
    """Remove IA DjVu OCR artefacts."""
    text = re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=re.MULTILINE)
    text = re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                  lambda m: m.group(0).replace(' ', ''), text)
    for marker in ['This is a digital copy', 'Google Books']:
        idx = text.find(marker)
        if 0 <= idx < 5000:
            for content_marker in ['INTRODUCTION', 'APPENDIX I', 'SECTION I',
                                    'THE FIRST', 'HEXAGRAM', 'Book I', 'THE TEXT']:
                cm = text.find(content_marker, idx)
                if cm > 0:
                    text = text[cm:]
                    break
    text = re.sub(r'-\n\s+', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
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


def _parse_mencius(text: str) -> list[dict]:
    """
    Extract Mencius from the Chinese Literature anthology (#10056).
    The anthology has sections labelled "SAYINGS OF MENCIUS" or "MENCIUS".
    """
    # Find the Mencius section in the anthology
    mencius_start = -1
    for marker in ["SAYINGS OF MENCIUS", "MENCIUS", "Mencius"]:
        idx = text.find(marker)
        if idx >= 0:
            mencius_start = idx
            break

    if mencius_start < 0:
        # Fallback: treat whole text as Mencius if it's a dedicated file
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Mencius")

    # Find end of Mencius section (next major section)
    mencius_end = len(text)
    for next_marker in ["SHI-KING", "TRAVELS", "SORROWS", "FAH-HIEN", "*** END"]:
        idx = text.find(next_marker, mencius_start + 500)
        if idx > 0:
            mencius_end = idx
            break

    mencius_text = text[mencius_start:mencius_end]

    # Split by Book headers
    book_re = re.compile(
        r'(?:^|\n)(BOOK\s+[IVXLC]+\.?\s*[\w\s]*?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(book_re.finditer(mencius_text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', mencius_text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Mencius")

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip()
        start = match.start()
        end   = matches[bi+1].start() if bi+1 < len(matches) else len(mencius_text)
        body  = mencius_text[start:end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
        sub   = _chunk_paragraphs(paras, f"Mencius {book_label}")
        chunks.extend(sub)

    return chunks


def _parse_iching(text: str) -> list[dict]:
    """
    I Ching: 64 hexagrams + appendices.
    Each hexagram has a number and name. Group into chunks of ~350 words.
    """
    # Hexagram headers: "APPENDIX I", "HEXAGRAM I.", "I. KHIEN.", numbered sections
    hexagram_re = re.compile(
        r'(?:^|\n)((?:HEXAGRAM|APPENDIX)\s+[IVXLC\d]+\.?\s*[\w\s,\-]*?)\n|'
        r'(?:^|\n)([IVXLC]{1,5})\.\s+([\w\s]+?)\.\s*\n',
        re.MULTILINE | re.IGNORECASE
    )
    matches = list(hexagram_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 10]
        return _chunk_paragraphs(paras, "I Ching")

    sections = []
    for mi, match in enumerate(matches):
        label = (match.group(1) or f"{match.group(2)}. {match.group(3)}").strip()
        start = match.start()
        end   = matches[mi+1].start() if mi+1 < len(matches) else len(text)
        body  = _clean(text[start:end])
        if body and len(body.split()) >= 15:
            sections.append({'label': label, 'text': body, 'words': len(body.split())})

    # Group into ~TARGET_WORDS chunks
    chunks = []
    buffer: list[dict] = []
    buf_words = 0
    chunk_num = 1

    for section in sections:
        if buffer and buf_words + section['words'] > TARGET_WORDS and buf_words >= MIN_WORDS:
            first = buffer[0]['label']
            last  = buffer[-1]['label']
            txt   = ' '.join(s['text'] for s in buffer)
            ref   = f"I Ching {first}" if first == last else f"I Ching {first}–{last}"
            chunks.append({'text': txt, 'reference': ref, 'chapter': "I Ching",
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            chunk_num += 1
            buffer = [section]
            buf_words = section['words']
        else:
            buffer.append(section)
            buf_words += section['words']

    if buffer:
        first = buffer[0]['label']
        last  = buffer[-1]['label']
        txt   = ' '.join(s['text'] for s in buffer)
        ref   = f"I Ching {first}" if first == last else f"I Ching {first}–{last}"
        chunks.append({'text': txt, 'reference': ref, 'chapter': "I Ching",
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})

    return chunks


def _get_corpus_id(conn) -> int:
    """Get existing corpus ID or create it."""
    from db import execute_one as _exe
    row = _exe(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (CORPUS_CODE, CORPUS_NAME, TRADITION, LANGUAGE, LICENSE, "https://www.gutenberg.org"))
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
          TRADITION, LANGUAGE, text_def['collection'], text_def['url'], text_def['translator']))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _get_corpus_id(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id} (adding to existing corpus)")

    total_chunks = 0
    total_texts  = 0

    for text_def in TEXTS:
        print(f"\n{'='*60}")
        print(f"Ingesting: {text_def['title']}")

        raw = None
        urls_to_try = [text_def['url']] + text_def.get('fallback_urls', [])
        headers = IA_HEADERS if text_def['source_type'] == 'ia' else {'Accept-Encoding': 'identity'}
        for url in urls_to_try:
            for attempt in range(3):
                try:
                    resp = httpx.get(url, timeout=120.0, follow_redirects=True, headers=headers)
                    if resp.status_code == 404:
                        print(f"  404 — skip {url.split('/')[-1]}")
                        break
                    resp.raise_for_status()
                    raw = resp.content.decode('utf-8', errors='replace')
                    print(f"  Downloaded {len(raw)//1024}KB from {url.split('/')[-1]}")
                    break
                except Exception as e:
                    if attempt < 2:
                        print(f"  Retry {attempt+1}: {e}")
                        time.sleep(5)
                    else:
                        print(f"  [ERROR] {url.split('/')[-1]}: {e}")
            if raw:
                break

        if raw is None:
            continue

        if text_def['source_type'] == 'ia':
            text = _clean_djvu(raw).replace('\r\n', '\n').replace('\r', '\n')
        else:
            text = _strip_gutenberg(raw).replace('\r\n', '\n').replace('\r', '\n')

        mode = text_def['parse_mode']
        if mode == 'mencius':
            chunks = _parse_mencius(text)
        elif mode == 'iching':
            chunks = _parse_iching(text)
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
            chunks = _chunk_paragraphs(paras, text_def['title'])

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
                    print(f"  [WARN] Voyage attempt {attempt}: {e}. Retry {RETRY_DELAY}s...")
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
    print(f"Chinese expansion complete.")
    print(f"  Texts added    : {total_texts}")
    print(f"  Chunks added   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Add Mencius + I Ching to classical-chinese corpus')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    run(force=args.force)
