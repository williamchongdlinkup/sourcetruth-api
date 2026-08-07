# -*- coding: utf-8 -*-
"""
Rigveda ingestion: Ralph T. H. Griffith translation (1896-97, Public Domain).

Source: Project Gutenberg #8496 — The Rig Veda, translated by Ralph T. H. Griffith
Griffith died 1906; published 1896-97 — Public Domain in USA.

Corpus: 1,028 hymns (suktas) in 10 mandalas. Each sukta = one chunk.
Reference format: RV 1.1 (Mandala.Sukta)

Corpus code : rigveda
Tradition   : hinduism
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

CORPUS_CODE = "rigveda"
CORPUS_NAME = "Rigveda (Griffith, 1896)"
TRADITION   = "hinduism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org/ebooks/8496"

# Internet Archive sources — 3 Google Books volumes, ordered to give I-X sequence
# Vol 02 contains Mandalas I-II, Vol 00 = III-VI, Vol 01 = VII-IX
IA_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}
IA_URLS = [
    "https://archive.org/download/hymnsrigveda02grifgoog/hymnsrigveda02grifgoog_djvu.txt",  # Mandalas I-II
    "https://archive.org/download/hymnsrigveda00grifgoog/hymnsrigveda00grifgoog_djvu.txt",  # Mandalas III-VI
    "https://archive.org/download/hymnsrigveda01grifgoog/hymnsrigveda01grifgoog_djvu.txt",  # Mandalas VII-IX
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
                   "End of the Project Gutenberg"]:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
            break
    return text.strip()


def _clean(s: str) -> str:
    return _WHITESPACE.sub(' ', (s or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


_VALID_MANDALAS = {'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'}


def _normalize_mandala(raw: str) -> str:
    """Resolve OCR artifacts in DjVu running-header Roman numerals.

    DjVu OCR commonly reads "." as "l"/"L", turning "VIII." → "VIIL",
    and can misread "X." as "L" entirely (X dropped). For Rigveda mandalas
    I-X, no valid label contains L, C, D, or M.
    """
    s = raw.strip().upper()
    if s in _VALID_MANDALAS:
        return s
    # "L" alone = "X." with X dropped — common in Mandala X running headers
    if s == 'L':
        return 'X'
    # Replace OCR-misread chars (L, C, D, M → I restores trailing-dot artifacts)
    fixed = re.sub(r'[LCDM]', 'I', s)
    if fixed in _VALID_MANDALAS:
        return fixed
    # Try stripping non-IVX suffix (e.g. "XI" from "XL" after L→I)
    m = re.match(r'^([IVX]+)', fixed)
    if m and m.group(1) in _VALID_MANDALAS:
        return m.group(1)
    return ''


def _parse_rigveda(text: str) -> list[dict]:
    """
    Griffith's Rigveda (Google Books DjVu via IA).

    The DjVu text has no standalone MANDALA headers; mandala info comes from
    running page footers: " OF [BOOK III. "  (with common OCR artifacts).
    Strategy:
      1. Build a (position → mandala) map from [BOOK X.] footer markers.
      2. Split by HYMN headers.
      3. Tag each hymn with the most recent mandala seen before it.
    """
    # Running header pattern: "[BOOK III." or "[BOOK III]" (OCR artifacts handled)
    hdr_re = re.compile(r'\[BOOK\s+([IVXLCDM]+)\.?\]?', re.IGNORECASE)
    mandala_map: list[tuple[int, str]] = []
    for m in hdr_re.finditer(text):
        mn = _normalize_mandala(m.group(1))
        if mn:
            mandala_map.append((m.start(), mn))

    # Also accept standalone mandala headers if they exist
    standalone_re = re.compile(
        r'(?:^|\n)(?:MANDALA|BOOK)\s+([IVXLCDM]+)\.?\s*\n',
        re.IGNORECASE | re.MULTILINE
    )
    for m in standalone_re.finditer(text):
        mn = _normalize_mandala(m.group(1))
        if mn:
            mandala_map.append((m.start(), mn))

    mandala_map.sort(key=lambda x: x[0])

    def mandala_at(pos: int) -> str:
        result = 'I'
        for p, mn in mandala_map:
            if p <= pos:
                result = mn
            else:
                break
        return result

    # Split by HYMN headers — [^\n]*? matches any header title including periods and OCR artifacts
    hymn_re = re.compile(
        r'(?:^|\n)(HYMN\s+[IVXLCDM]+[^\n]*?)\n',
        re.MULTILINE | re.IGNORECASE
    )
    hymn_matches = list(hymn_re.finditer(text))

    if not hymn_matches:
        return _parse_hymns_flat(text)

    # Group hymns by mandala, then merge short ones within each mandala
    by_mandala: dict[str, list[tuple[str, str, int]]] = {}  # mandala → [(label, text, words)]
    for hi, match in enumerate(hymn_matches):
        hymn_label = match.group(1).strip()
        start = match.start()
        end   = hymn_matches[hi + 1].start() if hi + 1 < len(hymn_matches) else len(text)
        hymn_text = _clean(text[start:end])
        hymn_words = len(hymn_text.split())
        if not hymn_text or hymn_words < 10:
            continue
        mn = mandala_at(start)
        by_mandala.setdefault(mn, []).append((hymn_label, hymn_text, hymn_words))

    # Mandala order: I II III IV V VI VII VIII IX X
    order = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X']

    chunks = []
    for mn in order:
        hymns = by_mandala.get(mn)
        if not hymns:
            continue
        buffer_hymns: list[tuple[str, str]] = []
        buf_words = 0

        def flush_mandala():
            if not buffer_hymns:
                return
            first_label = buffer_hymns[0][0]
            txt = ' '.join(h[1] for h in buffer_hymns)
            num_m = re.search(r'([IVXLCDM]+|\d+)', first_label, re.IGNORECASE)
            hymn_num = num_m.group(1) if num_m else first_label
            ref = f"RV {mn}.{hymn_num}"
            chunks.append({'text': txt, 'reference': ref,
                           'chapter': f"Mandala {mn}",
                           'word_count': len(txt.split()),
                           'token_count': _approx_tokens(txt)})

        for hymn_label, hymn_text, hymn_words in hymns:
            if buffer_hymns and buf_words + hymn_words > TARGET_WORDS and buf_words >= MIN_WORDS:
                flush_mandala()
                buffer_hymns = [(hymn_label, hymn_text)]
                buf_words    = hymn_words
            else:
                buffer_hymns.append((hymn_label, hymn_text))
                buf_words += hymn_words
        flush_mandala()

    return chunks


def _parse_hymns_flat(text: str) -> list[dict]:
    """Fallback: parse hymns without mandala structure."""
    hymn_re = re.compile(
        r'(?:^|\n)(HYMN\s+(?:I{1,4}V?|V?I{1,4}|IX|X{0,3}I{0,4}V?|[A-Z]{0,4}\d{0,3})\.?\s*[\w\s]*?)\n',
        re.MULTILINE
    )
    return _chunk_by_pattern(hymn_re, text, "RV", "Rigveda")


def _parse_hymns_in_mandala(body: str, mandala_num: str) -> list[dict]:
    """Parse hymns within a single mandala."""
    # Griffith uses patterns like: "HYMN I. Agni." or "I. To Agni."
    hymn_re = re.compile(
        r'(?:^|\n)(HYMN\s+(?:[IVXLC]+|\d+)\.?\s*[\w\s,]+?)\n',
        re.MULTILINE | re.IGNORECASE
    )
    matches = list(hymn_re.finditer(body))

    if not matches:
        # Fallback: treat whole mandala as ~350-word paragraph chunks
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 10]
        chunks = []
        buffer: list[str] = []
        buf_words = 0
        hymn_num = 1
        for para in paras:
            w = len(para.split())
            if buffer and buf_words + w > TARGET_WORDS and buf_words >= MIN_WORDS:
                txt = ' '.join(buffer)
                ref = f"RV {mandala_num}.{hymn_num}"
                chunks.append({'text': txt, 'reference': ref, 'chapter': f"Mandala {mandala_num}",
                                'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
                hymn_num += 1
                buffer = [para]
                buf_words = w
            else:
                buffer.append(para)
                buf_words += w
        if buffer:
            txt = ' '.join(buffer)
            chunks.append({'text': txt, 'reference': f"RV {mandala_num}.{hymn_num}",
                           'chapter': f"Mandala {mandala_num}",
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
        return chunks

    chunks = []
    buffer_hymns: list[tuple[str, str]] = []  # (hymn_label, text)
    buf_words = 0

    def flush():
        if not buffer_hymns:
            return
        first_label = buffer_hymns[0][0]
        last_label  = buffer_hymns[-1][0]
        txt = ' '.join(h[1] for h in buffer_hymns)
        # Extract hymn number from label
        num_m = re.search(r'([IVXLC]+|\d+)', first_label, re.IGNORECASE)
        hymn_num = num_m.group(1) if num_m else first_label
        ref = f"RV {mandala_num}.{hymn_num}"
        chunks.append({'text': txt, 'reference': ref, 'chapter': f"Mandala {mandala_num}",
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})

    for hi, match in enumerate(matches):
        hymn_label = match.group(1).strip()
        start = match.start()
        end   = matches[hi+1].start() if hi+1 < len(matches) else len(body)
        hymn_text = _clean(body[start:end])
        hymn_words = len(hymn_text.split())

        if not hymn_text or hymn_words < 10:
            continue

        if buffer_hymns and buf_words + hymn_words > TARGET_WORDS and buf_words >= MIN_WORDS:
            flush()
            buffer_hymns = [(hymn_label, hymn_text)]
            buf_words    = hymn_words
        else:
            buffer_hymns.append((hymn_label, hymn_text))
            buf_words += hymn_words

    flush()
    return chunks


def _chunk_by_pattern(pattern, text, ref_prefix, title):
    matches = list(pattern.finditer(text))
    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        chunks = []
        buffer: list[str] = []
        buf_words = 0
        n = 1
        for para in paras:
            w = len(para.split())
            if buffer and buf_words + w > TARGET_WORDS and buf_words >= MIN_WORDS:
                txt = ' '.join(buffer)
                chunks.append({'text': txt, 'reference': f"{ref_prefix} {n}", 'chapter': title,
                                'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
                n += 1; buffer = [para]; buf_words = w
            else:
                buffer.append(para); buf_words += w
        if buffer:
            txt = ' '.join(buffer)
            chunks.append({'text': txt, 'reference': f"{ref_prefix} {n}", 'chapter': title,
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
        return chunks
    # Each match = one chunk
    chunks = []
    for mi, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start(); end = matches[mi+1].start() if mi+1 < len(matches) else len(text)
        body  = _clean(text[start:end])
        if body and len(body.split()) >= 10:
            chunks.append({'text': body, 'reference': f"{ref_prefix} — {label}", 'chapter': label,
                           'word_count': len(body.split()), 'token_count': _approx_tokens(body)})
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


def _upsert_text(conn, corpus_id: int, mandala_num: str) -> int:
    external_id = f"rigveda-mandala-{mandala_num}"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url, translator)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, f"Rigveda Mandala {mandala_num}",
          TRADITION, LANGUAGE, "Rigveda", BASE_URL, "Ralph T. H. Griffith (1896)"))
    conn.commit()
    return row['id']


def _clean_djvu_ia(text: str) -> str:
    """Remove Internet Archive / Google Books header and OCR artifacts."""
    import re as _re
    # Remove page numbers on their own lines
    text = _re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=_re.MULTILINE)
    # Fix spaced-out letter sequences (DjVu OCR: "R I G V E D A" → "RIGVEDA")
    text = _re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                   lambda m: m.group(0).replace(' ', ''), text)
    # Strip Google Books boilerplate paragraphs (may appear at start of each volume)
    for marker in ['This is a digital copy', 'Google Books']:
        while True:
            idx = text.find(marker)
            if idx < 0:
                break
            # Find end of boilerplate block (ends at first blank line after a long paragraph)
            end = text.find('\n\n', idx + 200)
            if end < 0:
                break
            # Only strip if this block is short (boilerplate, not actual content)
            block_len = end - idx
            if block_len < 4000:
                text = text[:idx] + text[end:]
            else:
                break
    # Collapse broken hyphenation
    text = _re.sub(r'-\n\s+', '', text)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def run(force: bool = False) -> None:
    print(f"Downloading Rigveda (Griffith) from Internet Archive ...")
    all_raw_parts = []
    for url in IA_URLS:
        raw = None
        for attempt in range(3):
            try:
                resp = httpx.get(url, timeout=120.0, follow_redirects=True, headers=IA_HEADERS)
                if resp.status_code == 404:
                    print(f"  404 for {url} — skipping.")
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
                    print(f"  [ERROR] Failed: {e}")
        if raw:
            all_raw_parts.append(raw)

    if not all_raw_parts:
        print("[ERROR] No Rigveda content downloaded")
        return

    # Combine volumes
    combined = '\n\n'.join(all_raw_parts)
    text = _clean_djvu_ia(combined).replace('\r\n', '\n').replace('\r', '\n')
    print(f"  Combined text: {len(text)//1024}KB")

    all_chunks = _parse_rigveda(text)
    print(f"  Parsed {len(all_chunks)} chunks total")

    if not all_chunks:
        print("  [ERROR] No chunks produced")
        return

    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    # Group chunks by mandala (extract from reference like "RV I.3")
    from collections import defaultdict
    by_mandala: dict[str, list[dict]] = defaultdict(list)
    for chunk in all_chunks:
        ref = chunk.get('reference', '')
        m = re.match(r'RV\s+([IVXLC]+|Mandala\s+\w+)', ref, re.IGNORECASE)
        mandala_num = m.group(1) if m else 'X'
        by_mandala[mandala_num].append(chunk)

    total_chunks = 0
    total_texts  = 0

    for mandala_num, chunks in sorted(by_mandala.items()):
        text_id = _upsert_text(conn, corpus_id, mandala_num)

        if not force:
            existing = execute_one(conn,
                "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
            if existing and existing['n'] > 0:
                n = existing['n']
                print(f"  Mandala {mandala_num}: already ingested ({n} chunks) — skip")
                total_chunks += n
                total_texts  += 1
                continue

        if force:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN "
                            "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
            conn.commit()

        print(f"  Mandala {mandala_num}: {len(chunks)} chunks ...", end=' ', flush=True)

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
                    print(f"\n  [ERROR] Voyage failed: {e}")

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
                          None, LANGUAGE, TRADITION, CORPUS_CODE, "Rigveda", True))
                    chunk_id = cur.fetchone()['id']
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                                (chunk_id, emb_np))
                written += 1
            conn.commit()
            total_chunks += written
            total_texts  += 1
            print(f"✓ {written} chunks")
        except Exception as e:
            print(f"\n  [ERROR] write failed: {e}")
            try: conn.rollback()
            except Exception: conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Rigveda ingestion complete.")
    print(f"  Mandalas ingested : {total_texts}")
    print(f"  Total chunks      : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Rigveda (Griffith 1896, Public Domain)')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    run(force=args.force)
