# -*- coding: utf-8 -*-
"""
Christian Theology ingestion (Public Domain).

Texts:
  1. Augustine — Confessions (Pusey, 1838)          — Gutenberg #3296
  2. Augustine — City of God Vol I (Dods, 1872)     — Gutenberg #45304
  3. Augustine — City of God Vol II (Dods, 1872)    — Gutenberg #45305
  4. Apostolic Fathers (Lightfoot, 1891)             — IA apostolicfathers18812ligh

All translators/editors deceased >70 years; US PD confirmed.

Corpus code: christian-theology
Tradition  : christianity
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

CORPUS_CODE = "christian-theology"
CORPUS_NAME = "Christian Theology — Patristics (Augustine, Apostolic Fathers)"
TRADITION   = "christianity"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org"

TARGET_WORDS = 350
MIN_WORDS    = 60
MAX_RETRIES  = 5
RETRY_DELAY  = 30

IA_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}

TEXTS = [
    {
        "external_id":  "augustine-confessions",
        "title":        "Confessions",
        "author":       "Augustine of Hippo",
        "translator":   "E. B. Pusey (1838)",
        "source_type":  "gutenberg",
        "gutenberg_id": 3296,
        "url":          "https://www.gutenberg.org/cache/epub/3296/pg3296.txt",
        "parse_mode":   "confessions",
        "collection":   "Augustine",
    },
    {
        "external_id":  "augustine-city-of-god-vol1",
        "title":        "The City of God, Vol I",
        "author":       "Augustine of Hippo",
        "translator":   "Marcus Dods (1872)",
        "source_type":  "gutenberg",
        "gutenberg_id": 45304,
        "url":          "https://www.gutenberg.org/cache/epub/45304/pg45304.txt",
        "parse_mode":   "city_of_god",
        "collection":   "Augustine",
    },
    {
        "external_id":  "augustine-city-of-god-vol2",
        "title":        "The City of God, Vol II",
        "author":       "Augustine of Hippo",
        "translator":   "Marcus Dods (1872)",
        "source_type":  "gutenberg",
        "gutenberg_id": 45305,
        "url":          "https://www.gutenberg.org/cache/epub/45305/pg45305.txt",
        "parse_mode":   "city_of_god",
        "collection":   "Augustine",
    },
    {
        "external_id":  "apostolic-fathers-lightfoot",
        "title":        "The Apostolic Fathers",
        "author":       "Clement of Rome; Ignatius; Polycarp; Hermas; Barnabas",
        "translator":   "J. B. Lightfoot (1891)",
        "source_type":  "ia",
        "ia_id":        "apostolicfathers18812ligh",
        "url":          "https://archive.org/download/apostolicfathers18812ligh/apostolicfathers18812ligh_djvu.txt",
        "fallback_urls": [
            "https://archive.org/download/apostolicfathers00ligh/apostolicfathers00ligh_djvu.txt",
        ],
        "parse_mode":   "apostolic",
        "collection":   "Apostolic Fathers",
    },
]

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
                   "End of the Project Gutenberg"]:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
            break
    return text.strip()


def _clean_djvu(text: str) -> str:
    text = re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=re.MULTILINE)
    text = re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                  lambda m: m.group(0).replace(' ', ''), text)
    for marker in ['This is a digital copy', 'Google Books', 'Digitized by']:
        idx = text.find(marker)
        if 0 <= idx < 8000:
            for cm_str in ['CHAPTER', 'EPISTLE', 'THE DIDACHE', 'I. CLEMENT']:
                cm = text.find(cm_str, idx)
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


def _parse_confessions(text: str) -> list[dict]:
    """
    Augustine Confessions: 13 books.
    Book headers: "BOOK I.", "BOOK THE FIRST", or "THE FIRST BOOK".
    """
    book_re = re.compile(
        r'(?:^|\n)(BOOK\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|'
        r'SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH|THIRTEENTH|I{1,3}V?|V?I{1,3})\b)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(book_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Confessions")

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip()
        bk_start   = match.start()
        bk_end     = matches[bi + 1].start() if bi + 1 < len(matches) else len(text)
        body       = text[bk_start:bk_end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
        sub   = _chunk_paragraphs(paras, f"Confessions, {book_label}")
        chunks.extend(sub)

    return chunks


def _parse_city_of_god(text: str) -> list[dict]:
    """
    City of God: 22 books across 2 volumes.
    Book headers: "BOOK I", "BOOK II" etc., then Chapter headers within.
    """
    book_re = re.compile(
        r'(?:^|\n)(BOOK\s+(?:I{1,3}V?|V?I{1,4}|IX|X{1,3}I{0,3}|X{0,3}V?I{1,3}|XX{0,2}I{0,3}|XXII)\b)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(book_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "City of God")

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip()
        bk_start   = match.start()
        bk_end     = matches[bi + 1].start() if bi + 1 < len(matches) else len(text)
        book_body  = text[bk_start:bk_end]

        # Split by chapter headers
        chap_re = re.compile(r'(?:^|\n)(CHAPTER\s+\w+)', re.IGNORECASE | re.MULTILINE)
        chap_matches = list(chap_re.finditer(book_body))

        if chap_matches:
            for ci, cm in enumerate(chap_matches):
                c_start = cm.start()
                c_end   = chap_matches[ci + 1].start() if ci + 1 < len(chap_matches) else len(book_body)
                c_label = cm.group(1).strip()
                c_text  = _clean(book_body[c_start:c_end])
                if not c_text or len(c_text.split()) < 10:
                    continue
                if len(c_text.split()) > TARGET_WORDS * 2:
                    paras = [_clean(p) for p in re.split(r'\n{2,}', book_body[c_start:c_end]) if p.strip()]
                    sub   = _chunk_paragraphs(paras, f"City of God, {book_label}, {c_label}")
                    chunks.extend(sub)
                else:
                    ref = f"City of God, {book_label}, {c_label}"
                    chunks.append({'text': c_text, 'reference': ref, 'chapter': book_label,
                                   'word_count': len(c_text.split()), 'token_count': _approx_tokens(c_text)})
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', book_body) if p.strip() and len(p.split()) > 5]
            sub   = _chunk_paragraphs(paras, f"City of God, {book_label}")
            chunks.extend(sub)

    return chunks


def _parse_apostolic(text: str) -> list[dict]:
    """
    Lightfoot Apostolic Fathers: multiple texts (1 Clement, Ignatius, Polycarp,
    Didache, Barnabas, Hermas). Find text-level headers, then chapter-chunk.
    """
    # Top-level text markers: "THE EPISTLE OF...", "THE DIDACHE", "THE SHEPHERD"
    text_re = re.compile(
        r'(?:^|\n)((?:THE\s+EPISTLE|THE\s+SHEPHERD|THE\s+DIDACHE|THE\s+LETTER|'
        r'EPISTLE\s+OF|I\.\s+CLEMENT|IGNATIUS|POLYCARP|BARNABAS|HERMAS)[\w\s,\-\.]{0,60})\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(text_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Apostolic Fathers")

    chunks = []
    for mi, match in enumerate(matches):
        label = _clean(match.group(1))
        start = match.start()
        end   = matches[mi + 1].start() if mi + 1 < len(matches) else len(text)
        body  = text[start:end]

        # Try chapter splits
        chap_re = re.compile(r'(?:^|\n)(CHAPTER\s+\w+|SECTION\s+\w+)', re.IGNORECASE | re.MULTILINE)
        chap_matches = list(chap_re.finditer(body))

        if len(chap_matches) >= 2:
            for ci, cm in enumerate(chap_matches):
                c_start = cm.start()
                c_end   = chap_matches[ci + 1].start() if ci + 1 < len(chap_matches) else len(body)
                c_label = cm.group(1).strip()
                c_text  = _clean(body[c_start:c_end])
                if not c_text or len(c_text.split()) < 10:
                    continue
                ref = f"{label} — {c_label}"
                if len(c_text.split()) > TARGET_WORDS * 2:
                    paras = [_clean(p) for p in re.split(r'\n{2,}', body[c_start:c_end]) if p.strip()]
                    chunks.extend(_chunk_paragraphs(paras, ref))
                else:
                    chunks.append({'text': c_text, 'reference': ref, 'chapter': label,
                                   'word_count': len(c_text.split()), 'token_count': _approx_tokens(c_text)})
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
            chunks.extend(_chunk_paragraphs(paras, label))

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
    url = (f"https://www.gutenberg.org/ebooks/{text_def['gutenberg_id']}"
           if text_def['source_type'] == 'gutenberg'
           else f"https://archive.org/details/{text_def.get('ia_id', '')}")
    display = f"{text_def['title']} — {text_def['author']} (trans. {text_def['translator']})"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, text_def['external_id'], display,
          TRADITION, LANGUAGE, text_def['collection'], url))
    conn.commit()
    return row['id']


def _fetch_text(text_def: dict) -> str | None:
    urls = [text_def['url']] + text_def.get('fallback_urls', [])
    headers = IA_HEADERS if text_def['source_type'] == 'ia' else {}
    for url in urls:
        for attempt in range(3):
            try:
                resp = httpx.get(url, timeout=180.0, follow_redirects=True, headers=headers)
                if resp.status_code in (404, 503):
                    print(f"  {resp.status_code} — {url.split('/')[-1]}")
                    break
                resp.raise_for_status()
                raw = resp.content.decode('utf-8', errors='replace')
                if len(raw) > 5000:
                    print(f"  ✓ {len(raw) // 1024}KB downloaded")
                    return raw
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  Retry {attempt + 1}: {e}")
                    time.sleep(5)
                else:
                    print(f"  [ERROR] {e}")
    return None


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for text_def in TEXTS:
        print(f"\n{'='*60}")
        print(f"Ingesting: {text_def['title']} by {text_def['author']}")

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

        raw = _fetch_text(text_def)
        if raw is None:
            print(f"  [ERROR] Could not download — skipping")
            continue

        if text_def['source_type'] == 'gutenberg':
            text = _strip_gutenberg(raw).replace('\r\n', '\n').replace('\r', '\n')
        else:
            text = _clean_djvu(raw).replace('\r\n', '\n').replace('\r', '\n')

        mode = text_def['parse_mode']
        if mode == 'confessions':
            chunks = _parse_confessions(text)
        elif mode == 'city_of_god':
            chunks = _parse_city_of_god(text)
        elif mode == 'apostolic':
            chunks = _parse_apostolic(text)
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
            chunks = _chunk_paragraphs(paras, text_def['title'])

        print(f"  Parsed {len(chunks)} chunks (mode={mode})")

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
            print(f"  [ERROR] Write failed for {text_def['title']}: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Christian Theology ingestion complete.")
    print(f"  Texts  : {total_texts}")
    print(f"  Chunks : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Christian Theology (Augustine, Apostolic Fathers, PD)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
