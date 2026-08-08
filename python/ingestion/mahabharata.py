# -*- coding: utf-8 -*-
"""
Mahabharata ingestion — Kisari Mohan Ganguli translation (1883-1896, Public Domain).

Source: "The Mahabharata by Kisari Mohan Ganguli" (single-file edition)
  IA item: the-mahabharata-by-kisari-mohan-ganguli
  PD status: Translation published 1883-1896. Ganguli died 1896.
             Fully Public Domain worldwide.

Target parvas (books):
  - Adi Parva       (Book 1)  — origins, genealogies, early stories
  - Shanti Parva    (Book 12) — the great philosophical treatise
  - Anushasana Parva(Book 13) — ethics, duties, instructions

Chunking: ~350 words per chunk, respecting section boundaries.

Corpus code: mahabharata
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

CORPUS_CODE = "mahabharata"
CORPUS_NAME = "The Mahabharata — Ganguli Translation (1883-1896)"
TRADITION   = "hinduism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://archive.org"

# IA single-file edition containing all 18 parvas
IA_URL = "https://archive.org/download/the-mahabharata-by-kisari-mohan-ganguli/The%20Mahabharata%20by%20Kisari%20Mohan%20Ganguli_djvu.txt"
IA_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}

TARGET_WORDS = 350
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30

# Parvas to ingest and their canonical names
# Ganguli text uses variant spellings — map all forms to canonical name
TARGET_PARVAS = {
    "ADI PARVA":          "Adi Parva (Book of the Beginning)",
    "SHANTI PARVA":       "Shanti Parva (Book of Peace)",
    "SANTI PARVA":        "Shanti Parva (Book of Peace)",
    "ANUSHASANA PARVA":   "Anushasana Parva (Book of Instructions)",
    "ANUSASANA PARVA":    "Anushasana Parva (Book of Instructions)",
    "ANUSASANIKA PARVA":  "Anushasana Parva (Book of Instructions)",
}

# Ganguli text uses these header patterns for parva boundaries
PARVA_PATTERNS = [
    r'(?:^|\n)\s*(ADI\s+PARVA)\b',
    r'(?:^|\n)\s*(SHANTI\s+PARVA)\b',
    r'(?:^|\n)\s*(ANUSHASANA\s+PARVA)\b',
    # Also match "SECTION I", "SECTION II" etc. as sub-section markers within a parva
]

_WHITESPACE = re.compile(r'\s+')


def _clean(s: str) -> str:
    return _WHITESPACE.sub(' ', (s or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _clean_djvu(text: str) -> str:
    """Strip IA DjVu OCR artefacts common to the Mahabharata file."""
    # Remove standalone page numbers
    text = re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=re.MULTILINE)
    # Collapse spaced-out all-caps words (OCR artefact: "M A H A B H A R A T A")
    text = re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                  lambda m: m.group(0).replace(' ', ''), text)
    # Fix hyphenation across lines
    text = re.sub(r'-\n\s+', '', text)
    # Normalise whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _find_parvas(text: str) -> list[dict]:
    """
    Locate the target parva sections in the full Ganguli text.
    Returns list of {'name': canonical_name, 'external_id': slug, 'body': text}.
    """
    # Build a combined pattern to find ANY parva start
    # We scan for known parva names; the text uses "ADI PARVA", "SHANTI PARVA" etc.
    all_parva_re = re.compile(
        r'(?:^|\n)\s*((?:'
        r'ADI|SABHA|VANA|ARANYAKA|VIRATA|UDYOGA|BHISHMA|DRONA|KARNA|'
        r'SHALYA|SAUPTIKA|STRI|SHANTI|SANTI|ANUSHASANA|ANUSASANA|ANUSASANIKA|'
        r'ASHVAMEDHIKA|ASVAMEDHIKA|ASHRAMAVASIKA|MAUSALA|MAHAPRASTHANIKA|SVARGAROHANA'
        r')\s+PARVA)\b',
        re.IGNORECASE | re.MULTILINE
    )

    all_matches = list(all_parva_re.finditer(text))
    if not all_matches:
        print("  [WARN] No parva boundaries found — attempting paragraph parse")
        return []

    print(f"  Found {len(all_matches)} parva markers in text")

    parvas: list[dict] = []
    for i, match in enumerate(all_matches):
        raw_name = re.sub(r'\s+', ' ', match.group(1).strip().upper())
        # Normalise spelling variants
        for variant, canonical_key in [
            ('SANTI PARVA', 'SHANTI PARVA'),
            ('ANUSASANA PARVA', 'ANUSHASANA PARVA'),
            ('ANUSASANIKA PARVA', 'ANUSHASANA PARVA'),
        ]:
            raw_name = raw_name.replace(variant, canonical_key)

        if raw_name not in TARGET_PARVAS:
            continue

        canonical = TARGET_PARVAS[raw_name]
        start = match.start()
        end   = all_matches[i + 1].start() if i + 1 < len(all_matches) else len(text)
        body  = text[start:end].strip()

        # Prefer the largest body for each canonical name (skips TOC stubs)
        existing = next((p for p in parvas if p['name'] == canonical), None)
        if existing is None or len(body) > len(existing['body']):
            slug = re.sub(r'[^a-z0-9]+', '-', canonical.lower()).strip('-')
            entry = {'name': canonical, 'external_id': f'mahabharata-{slug}', 'body': body}
            if existing is None:
                parvas.append(entry)
            else:
                parvas[parvas.index(existing)] = entry

    # Drop parvas whose body is tiny (< 10,000 chars = TOC stubs with no real content)
    parvas = [p for p in parvas if len(p['body']) >= 10_000]
    return parvas


def _chunk_section(section_body: str, ref_prefix: str) -> list[dict]:
    """Split a parva body into TARGET_WORDS chunks by section/paragraph boundaries."""
    # Split at "SECTION I", "SECTION II", etc.
    section_re = re.compile(
        r'(?:^|\n)\s*(SECTION\s+[IVXLCDM\d]+\.?)\s*\n',
        re.IGNORECASE | re.MULTILINE
    )
    sec_matches = list(section_re.finditer(section_body))

    if sec_matches:
        # Use sections as chunking boundaries
        chunks: list[dict] = []
        buffer_secs: list[tuple[str, str]] = []
        buf_words = 0

        def _flush(buf: list[tuple[str, str]]) -> None:
            if not buf:
                return
            combined = ' '.join(_clean(f"{s[0]} {s[1]}") for s in buf)
            if len(combined.split()) < MIN_WORDS:
                return
            label = buf[0][0]
            ref   = f"{ref_prefix} — {label}"
            chunks.append({
                'text':        combined,
                'reference':   ref,
                'chapter':     ref_prefix,
                'word_count':  len(combined.split()),
                'token_count': _approx_tokens(combined),
            })

        for si, sm in enumerate(sec_matches):
            sec_start = sm.start()
            sec_end   = sec_matches[si + 1].start() if si + 1 < len(sec_matches) else len(section_body)
            heading   = sm.group(1).strip()
            body      = _clean(section_body[sec_start:sec_end])
            bw        = len(body.split())

            if buffer_secs and buf_words + bw > TARGET_WORDS and buf_words >= MIN_WORDS:
                _flush(buffer_secs)
                buffer_secs = [(heading, body)]
                buf_words   = bw
            else:
                buffer_secs.append((heading, body))
                buf_words += bw

        _flush(buffer_secs)
        return chunks
    else:
        # Fall back to paragraph chunking
        paras = [_clean(p) for p in re.split(r'\n{2,}', section_body)
                 if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, ref_prefix)


def _chunk_paragraphs(paras: list[str], ref_prefix: str) -> list[dict]:
    chunks: list[dict] = []
    buffer: list[str] = []
    buf_words = 0
    chunk_num = 1

    for para in paras:
        words = para.split()
        if not words:
            continue
        if buffer and buf_words + len(words) > TARGET_WORDS and buf_words >= MIN_WORDS:
            txt = ' '.join(buffer)
            chunks.append({
                'text':        txt,
                'reference':   f"{ref_prefix} — §{chunk_num}",
                'chapter':     ref_prefix,
                'word_count':  len(txt.split()),
                'token_count': _approx_tokens(txt),
            })
            chunk_num += 1
            buffer    = [para]
            buf_words = len(words)
        else:
            buffer.append(para)
            buf_words += len(words)

    if buffer:
        txt = ' '.join(buffer)
        chunks.append({
            'text':        txt,
            'reference':   f"{ref_prefix} — §{chunk_num}",
            'chapter':     ref_prefix,
            'word_count':  len(txt.split()),
            'token_count': _approx_tokens(txt),
        })
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


def _upsert_text(conn, corpus_id: int, parva: dict) -> int:
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, parva['external_id'], parva['name'],
          TRADITION, LANGUAGE, "Mahabharata",
          "https://archive.org/details/the-mahabharata-by-kisari-mohan-ganguli"))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    print(f"\nDownloading Mahabharata (Ganguli) from IA ...")
    raw = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.get(IA_URL, timeout=300.0, follow_redirects=True, headers=IA_HEADERS)
            if resp.status_code in (404, 503):
                print(f"  {resp.status_code} — aborting")
                conn.close()
                return
            resp.raise_for_status()
            raw = resp.content.decode('utf-8', errors='replace')
            print(f"  Downloaded {len(raw) // 1024:,}KB")
            break
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  Retry {attempt + 1}: {e}")
                time.sleep(10)
            else:
                print(f"  [ERROR] {e}")

    if raw is None:
        conn.close()
        return

    text   = _clean_djvu(raw).replace('\r\n', '\n').replace('\r', '\n')
    parvas = _find_parvas(text)

    if not parvas:
        print("[ERROR] No target parvas found in text.")
        conn.close()
        return

    print(f"\nFound {len(parvas)} target parvas: {[p['name'] for p in parvas]}")

    total_chunks = 0
    total_texts  = 0

    for parva in parvas:
        print(f"\n{'='*60}")
        print(f"Ingesting: {parva['name']}")

        text_id = _upsert_text(conn, corpus_id, parva)

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

        chunks = _chunk_section(parva['body'], parva['name'])
        print(f"  Parsed {len(chunks)} chunks")

        if not chunks:
            print("  No chunks produced — skip")
            continue

        embeddings = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = embed_documents([c['text'] for c in chunks])
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
                    """, (
                        text_id, idx, chunk['text'], chunk['reference'],
                        chunk['chapter'], None,
                        chunk['word_count'], chunk['token_count'],
                        None, LANGUAGE, TRADITION, CORPUS_CODE, "Mahabharata", False,
                    ))
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
            print(f"  [ERROR] Write failed for {parva['name']}: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Mahabharata ingestion complete.")
    print(f"  Parvas ingested : {total_texts}")
    print(f"  Total chunks    : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Mahabharata — Ganguli translation (PD)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
