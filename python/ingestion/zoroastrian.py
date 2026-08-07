# -*- coding: utf-8 -*-
"""
Zoroastrian Scriptures ingestion (Public Domain, Internet Archive DjVu text).

Sources:
  - The Vendidad (Fargards I-XXII) — James Darmesteter, 1880 (SBE Vol 4)
    IA: https://archive.org/download/zendavestapart1t025014mbp/zendavestapart1t025014mbp_djvu.txt
  - Yasna (Ahunavaiti Gatha, Ushtavaiti Gatha, Spenta Mainyu, Vohu Khshathra, Vahishtoishti)
    + Yasna 1–72 — James Darmesteter / L. H. Mills, 1883/1887 (SBE Vol 23)
    IA: https://archive.org/download/zendavesta02darm/zendavesta02darm_djvu.txt

All texts published 1880-1887, translators deceased >70 years — Public Domain.

Chunking:
  - Vendidad: one Fargard = one chunk (natural canonical division)
  - Yasna: one hā (section) = one chunk; Gathas grouped by Gatha (~5-10 chapters)

Note: DjVu OCR text has minor OCR artefacts (spaced letters, scanning noise).
These are normalised during preprocessing.

Corpus code : avesta
Tradition   : zoroastrianism
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

CORPUS_CODE = "avesta"
CORPUS_NAME = "Zend-Avesta (Darmesteter/Mills, 1880-1887)"
TRADITION   = "zoroastrianism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://archive.org"

IA_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}

TEXTS = [
    {
        "external_id": "vendidad-darmesteter",
        "title":       "The Vendidad",
        "author":      "Anonymous (Zoroastrian scripture)",
        "translator":  "James Darmesteter (1880)",
        "ia_url":      "https://archive.org/download/zendavestapart1t025014mbp/zendavestapart1t025014mbp_djvu.txt",
        "parse_mode":  "vendidad",
        "collection":  "Vendidad",
    },
    {
        "external_id": "yasna-darmesteter-mills",
        "title":       "Yasna and Gathas",
        "author":      "Anonymous (Zoroastrian scripture)",
        "translator":  "James Darmesteter / L. H. Mills (1883-1887)",
        "ia_url":      "https://archive.org/download/zendavesta02darm/zendavesta02darm_djvu.txt",
        "parse_mode":  "yasna",
        "collection":  "Yasna",
    },
]

TARGET_WORDS = 400
MIN_WORDS    = 60
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_MULTI_SPACE = re.compile(r'[ \t]{2,}')
_WHITESPACE  = re.compile(r'\s+')


def _clean_djvu(text: str) -> str:
    """Clean common DjVu OCR artefacts: spaced letters, page markers, hyphenation."""
    # Remove page markers like "1 2 3" on their own lines
    text = re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=re.MULTILINE)
    # Remove lines that are purely numbers or whitespace
    text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
    # Collapse spaced-out letter sequences (DjVu artifact): "T R A N S L A T E D" → "TRANSLATED"
    text = re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                  lambda m: m.group(0).replace(' ', ''), text)
    # Fix broken hyphenation across lines
    text = re.sub(r'-\n\s+', '', text)
    # Normalise whitespace
    text = _MULTI_SPACE.sub(' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
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
            chunks.append({'text': txt, 'reference': f"{ref_prefix} — §{chunk_num}",
                           'chapter': ref_prefix,
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            chunk_num += 1
            buffer = [para]
        else:
            buffer.append(para)

    if buffer:
        txt = ' '.join(buffer)
        chunks.append({'text': txt, 'reference': f"{ref_prefix} — §{chunk_num}",
                       'chapter': ref_prefix,
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
    return chunks


def _parse_vendidad(text: str) -> list[dict]:
    """
    Vendidad: 22 Fargards. Headers: FARGARD I, FARGARD II, etc.
    Each Fargard is its own chunk; long ones may be sub-chunked.
    """
    fargard_re = re.compile(
        r'(?:^|\n)\s*(FARGARD\s+[IVXLCDM\d]+\.?\s*[\w\s]*?)(?:\n|$)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(fargard_re.finditer(text))

    if not matches:
        # Fallback: paragraph chunks
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 10]
        return _chunk_paragraphs(paras, "Vendidad")

    chunks = []
    for fi, match in enumerate(matches):
        fargard_label = match.group(1).strip()
        start = match.start()
        end   = matches[fi+1].start() if fi+1 < len(matches) else len(text)
        body  = _clean(text[start:end])

        if not body or len(body.split()) < 20:
            continue

        words = len(body.split())
        if words > TARGET_WORDS * 2:
            # Sub-chunk long Fargards by paragraph
            paras = [_clean(p) for p in re.split(r'\n{2,}', text[start:end])
                     if p.strip() and len(p.split()) > 10]
            sub = _chunk_paragraphs(paras, f"Vendidad, {fargard_label}")
            chunks.extend(sub)
        else:
            chunks.append({
                'text': body, 'reference': f"Vendidad, {fargard_label}",
                'chapter': fargard_label,
                'word_count': words, 'token_count': _approx_tokens(body),
            })

    return chunks


def _parse_yasna(text: str) -> list[dict]:
    """
    Yasna: 72 hās + Gathas embedded in them.
    Headers: YASNA I, YASNA X, GATHA I (Ahunavaiti), etc.
    Group every 3 hās into one chunk for reasonable chunk size.
    """
    yasna_re = re.compile(
        r'(?:^|\n)\s*((?:YASNA|GATHA|GATHAS?|HA)\s+[IVXLCDM\d]+\.?\s*[\w\s]*?)(?:\n|$)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(yasna_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 10]
        return _chunk_paragraphs(paras, "Yasna")

    # Extract individual sections
    sections = []
    for si, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[si+1].start() if si+1 < len(matches) else len(text)
        body  = _clean(text[start:end])
        if body and len(body.split()) > 10:
            sections.append({'label': label, 'text': body, 'words': len(body.split())})

    # Group sections into ~TARGET_WORDS chunks
    chunks = []
    buffer: list[dict] = []
    buf_words = 0
    group_num = 1

    for section in sections:
        if buffer and buf_words + section['words'] > TARGET_WORDS and buf_words >= MIN_WORDS:
            first = buffer[0]['label']
            last  = buffer[-1]['label']
            txt   = ' '.join(s['text'] for s in buffer)
            ref   = f"Yasna {first}" if first == last else f"Yasna {first} – {last}"
            chunks.append({'text': txt, 'reference': ref, 'chapter': "Yasna",
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            group_num += 1
            buffer = [section]
            buf_words = section['words']
        else:
            buffer.append(section)
            buf_words += section['words']

    if buffer:
        first = buffer[0]['label']
        last  = buffer[-1]['label']
        txt   = ' '.join(s['text'] for s in buffer)
        ref   = f"Yasna {first}" if first == last else f"Yasna {first} – {last}"
        chunks.append({'text': txt, 'reference': ref, 'chapter': "Yasna",
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})

    return chunks


def _strip_ia_header(text: str) -> str:
    """Remove Internet Archive / Google Books header boilerplate."""
    # IA DjVu texts often start with library/scan notices
    markers = [
        "CONTENTS",
        "INTRODUCTION",
        "PREFACE",
        "CHAPTER I",
        "FARGARD I",
        "YASNA I",
    ]
    for marker in markers:
        idx = text.upper().find(marker)
        if idx > 0 and idx < 5000:
            return text[idx:]
    return text


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
          TRADITION, LANGUAGE, text_def['collection'],
          text_def['ia_url'], text_def['translator']))
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
                resp = httpx.get(text_def['ia_url'], timeout=120.0, follow_redirects=True,
                                 headers=IA_HEADERS)
                if resp.status_code == 404:
                    print(f"  404 — skipping.")
                    break
                resp.raise_for_status()
                raw = resp.content.decode('utf-8', errors='replace')
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  Retry {attempt+1}: {e}")
                    time.sleep(10)
                else:
                    print(f"  [ERROR] Failed: {e}")

        if raw is None:
            continue

        raw = _clean_djvu(raw)
        raw = _strip_ia_header(raw)
        raw = raw.replace('\r\n', '\n').replace('\r', '\n')

        mode = text_def['parse_mode']
        if mode == 'vendidad':
            chunks = _parse_vendidad(raw)
        elif mode == 'yasna':
            chunks = _parse_yasna(raw)
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', raw) if p.strip() and len(p.split()) > 10]
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
                        text_def['collection'], True,
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
    print(f"Zoroastrian ingestion complete.")
    print(f"  Texts ingested : {total_texts}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Zoroastrian Avesta (Public Domain, Internet Archive)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
