# -*- coding: utf-8 -*-
"""
Rumi Masnavi ingestion — Nicholson translation (Public Domain).

Source: Reynold A. Nicholson, "The Mathnawi of Jalalu'ddin Rumi"
  Vol I (1925) — IA: in.ernet.dli.2015.325380
  Vol II (1926) — IA: in.gov.ignca.20683
  Both volumes published before 1928 → Public Domain in USA.
  Nicholson (1868–1945); UK PD since 2015.

Chunking: ~350 words per chunk, respecting verse-paragraph boundaries.

Corpus code: rumi-masnavi
Tradition  : islam  (Sufi tradition within Islam)
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

CORPUS_CODE = "rumi-masnavi"
CORPUS_NAME = "Masnavi-i Ma'navi — Rumi (Nicholson, 1925–26)"
TRADITION   = "islam"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://archive.org"

TARGET_WORDS = 350
MIN_WORDS    = 60
MAX_RETRIES  = 5
RETRY_DELAY  = 30

IA_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}

VOLUMES = [
    {
        "external_id":  "rumi-masnavi-vol1",
        "title":        "Masnavi Vol I — Rumi (Nicholson, 1925)",
        # IGNCA items have better OCR quality than ERNET DLI (14 chunks vs expected ~70)
        "ia_id":        "in.gov.ignca.20682",
        "fallback_ia":  "in.ernet.dli.2015.325380",
        "book_label":   "Book I",
        "year":         1925,
    },
    {
        "external_id":  "rumi-masnavi-vol2",
        "title":        "Masnavi Vol II — Rumi (Nicholson, 1926)",
        "ia_id":        "in.gov.ignca.20683",
        "fallback_ia":  "in.ernet.dli.2015.70297",
        "book_label":   "Book II",
        "year":         1926,
    },
]

_WHITESPACE = re.compile(r'\s+')


def _clean(s: str) -> str:
    return _WHITESPACE.sub(' ', (s or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _ia_djvu_url(ia_id: str) -> str:
    # IGNCA items: filename is just the numeric ID (e.g. 20682_djvu.txt)
    # ERNET DLI items: filename is like 2015.325380.Title_djvu.txt — use search
    if ia_id.startswith("in.gov.ignca."):
        num = ia_id.split(".")[-1]
        return f"https://archive.org/download/{ia_id}/{num}_djvu.txt"
    # For in.ernet.dli items, build URL by stripping "in.ernet.dli." prefix
    return f"https://archive.org/download/{ia_id}/{ia_id.replace('in.ernet.dli.', '')}_djvu.txt"


def _ia_djvu_urls(ia_id: str) -> list[str]:
    """Return candidate URLs to try for this IA item."""
    candidates = [_ia_djvu_url(ia_id)]
    # For ernet.dli, also try with title suffix
    if "ernet.dli.2015.325380" in ia_id:
        candidates.append(f"https://archive.org/download/{ia_id}/2015.325380.Mathnawi-Of_djvu.txt")
    if "ernet.dli.2015.70297" in ia_id:
        candidates.append(f"https://archive.org/download/{ia_id}/2015.70297_djvu.txt")
    return candidates


def _clean_djvu(text: str) -> str:
    """Strip IA DjVu OCR artefacts."""
    text = re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=re.MULTILINE)
    text = re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                  lambda m: m.group(0).replace(' ', ''), text)
    for marker in ['This is a digital copy', 'Google Books', 'Digitized by']:
        idx = text.find(marker)
        if 0 <= idx < 8000:
            for content_marker in ['BOOK I', 'PROLOGUE', 'THE PROLOGUE', 'In the name']:
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
            chunks.append({'text': txt,
                           'reference': f"{ref_prefix} — §{chunk_num}",
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
                       'reference': f"{ref_prefix} — §{chunk_num}",
                       'chapter': ref_prefix,
                       'word_count': len(txt.split()),
                       'token_count': _approx_tokens(txt)})
    return chunks


def _parse_masnavi(text: str, book_label: str) -> list[dict]:
    """
    The Masnavi consists of 6 books of rhyming couplets with prose headings.
    Each major section has a heading like "STORY OF...", "ON...", or verse numbers.
    Strategy: find section headers, group into TARGET_WORDS chunks.
    Falls back to paragraph chunking if structure not detected.
    """
    # Section headers: ALL CAPS lines or lines starting "STORY OF" / "ON "
    section_re = re.compile(
        r'(?:^|\n)([A-Z][A-Z ,\'\-]{5,80})\s*\n',
        re.MULTILINE
    )
    matches = list(section_re.finditer(text))

    # Filter to lines that look like real headings (not running text)
    def _is_heading(m) -> bool:
        s = m.group(1).strip()
        # Must be mostly uppercase, 4+ words is suspicious (probably running text in CAPS)
        words = s.split()
        if len(words) > 12:
            return False
        # At least 4 uppercase letters
        upper_count = sum(1 for c in s if c.isupper())
        return upper_count >= 4

    matches = [m for m in matches if _is_heading(m)]

    # If fewer than 5 headings found, fall back to paragraph chunking
    if len(matches) < 5:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, book_label)

    chunks = []
    buffer_secs: list[tuple[str, str]] = []  # (heading, body)
    buf_words = 0

    def _flush(buf: list[tuple[str, str]]) -> None:
        if not buf:
            return
        combined = ' '.join(_clean(s[0] + ' ' + s[1]) for s in buf)
        label = buf[0][0]
        ref   = f"{book_label} — {label[:60]}"
        # Sub-chunk if combined is too long
        if len(combined.split()) > TARGET_WORDS * 3:
            full = '\n\n'.join(s[1] for s in buf)
            paras = [_clean(p) for p in re.split(r'\n{2,}', full) if p.strip() and len(p.split()) > 5]
            chunks.extend(_chunk_paragraphs(paras, ref))
        else:
            chunks.append({'text': combined, 'reference': ref, 'chapter': book_label,
                           'word_count': len(combined.split()), 'token_count': _approx_tokens(combined)})

    for mi, match in enumerate(matches):
        sec_start  = match.start()
        sec_end    = matches[mi + 1].start() if mi + 1 < len(matches) else len(text)
        heading    = match.group(1).strip()
        body       = _clean(text[sec_start:sec_end])
        body_words = len(body.split())

        if buffer_secs and buf_words + body_words > TARGET_WORDS and buf_words >= MIN_WORDS:
            _flush(buffer_secs)
            buffer_secs = [(heading, body)]
            buf_words   = body_words
        else:
            buffer_secs.append((heading, body))
            buf_words += body_words

    _flush(buffer_secs)
    return chunks


def _fetch_volume(vol: dict) -> str | None:
    """Try primary then fallback IA item, trying multiple URL patterns per item."""
    for ia_id in [vol['ia_id'], vol.get('fallback_ia', '')]:
        if not ia_id:
            continue
        for url in _ia_djvu_urls(ia_id):
            print(f"  Trying {url.split('/')[-1]} from {ia_id} ...")
            for attempt in range(3):
                try:
                    resp = httpx.get(url, timeout=180.0, follow_redirects=True, headers=IA_HEADERS)
                    if resp.status_code in (404, 503):
                        print(f"    {resp.status_code} — try next URL")
                        break
                    resp.raise_for_status()
                    raw = resp.content.decode('utf-8', errors='replace')
                    if len(raw) > 5000:
                        print(f"    ✓ {len(raw) // 1024}KB")
                        return raw
                    else:
                        print(f"    Too small ({len(raw)} bytes) — try next")
                        break
                except Exception as e:
                    if attempt < 2:
                        print(f"    Retry {attempt + 1}: {e}")
                        time.sleep(5)
                    else:
                        print(f"    [ERROR] {e}")
    return None


def _upsert_corpus(conn) -> int:
    row = execute_one(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (CORPUS_CODE, CORPUS_NAME, TRADITION, LANGUAGE, LICENSE, BASE_URL))
    conn.commit()
    return row['id']


def _upsert_text(conn, corpus_id: int, vol: dict) -> int:
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, vol['external_id'], vol['title'],
          TRADITION, LANGUAGE, "Sufi Poetry",
          f"https://archive.org/details/{vol['ia_id']}"))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for vol in VOLUMES:
        print(f"\n{'='*60}")
        print(f"Ingesting: {vol['title']}")

        text_id = _upsert_text(conn, corpus_id, vol)

        if not force:
            existing = execute_one(conn,
                "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
            if existing and existing['n'] > 0:
                n = existing['n']
                print(f"  Already ingested ({n} chunks) — skip")
                total_chunks += n
                total_texts  += 1
                continue

        raw = _fetch_volume(vol)
        if raw is None:
            print(f"  [ERROR] Could not download {vol['title']} — skipping")
            continue

        text   = _clean_djvu(raw).replace('\r\n', '\n').replace('\r', '\n')
        chunks = _parse_masnavi(text, vol['book_label'])
        print(f"  Parsed {len(chunks)} chunks")

        if not chunks:
            print("  No chunks produced — skip")
            continue

        if force:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN "
                            "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
            conn.commit()

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
                    """, (text_id, idx, chunk['text'], chunk['reference'],
                          chunk['chapter'], None,
                          chunk['word_count'], chunk['token_count'],
                          None, LANGUAGE, TRADITION, CORPUS_CODE, "Sufi Poetry", True))
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
            print(f"  [ERROR] Write failed: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Rumi Masnavi ingestion complete.")
    print(f"  Volumes: {total_texts}")
    print(f"  Chunks : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Rumi Masnavi (Nicholson, PD)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
