# -*- coding: utf-8 -*-
"""
Sanskrit Classical Literature ingestion (Public Domain translations from Project Gutenberg).

Texts included:
  - Valmiki, The Ramayana     — Ralph T. H. Griffith (1870-1874)  (#24869)  ~2.3MB — sampled
  - Kalidasa, Shakuntala and Other Works — Arthur Ryder (1912)    (#16659)  ~411KB
  - Kalidasa, Sakoontala (alt. trans.) — Monier Williams (1853)   (#12169)

Chunking:
  - Ramayana: one kanda-chapter = one chunk (too large to load fully — sample key kandas)
  - Kalidasa: act/scene paragraph-groups (~350 words)

Note: Mahabharata (Ganguli, 1883-1896) is planned for Phase 2 expansion — full text
      (~100K verses) requires separate cost/time planning before ingestion.

Corpus code : sanskrit-classical
Tradition   : hinduism          (shares with BG/Upanishads; distinct corpus code)
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

CORPUS_CODE = "sanskrit-classical"
CORPUS_NAME = "Sanskrit Classical Literature"
TRADITION   = "hinduism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org"

# All 7 kandas — Phase 2 expansion adds Aranya (III), Kishkindha (IV), Sundara (V)
# to the original 4 (Bala I, Ayodhya II, Yuddha VI, Uttara VII).
RAMAYANA_SAMPLE_KANDAS = {"BOOK I", "BOOK II", "BOOK III", "BOOK IV", "BOOK V", "BOOK VI", "BOOK VII"}

TEXTS = [
    {
        "external_id":  "ramayana-griffith",
        "title":        "The Ramayana",
        "author":       "Valmiki",
        "translator":   "Ralph T. H. Griffith (1870-1874)",
        "gutenberg_id": 24869,
        "url":          "https://www.gutenberg.org/cache/epub/24869/pg24869.txt",
        "parse_mode":   "ramayana",
        "collection":   "Ramayana",
    },
    {
        "external_id":  "kalidasa-shakuntala-ryder",
        "title":        "Shakuntala and Other Works",
        "author":       "Kalidasa",
        "translator":   "Arthur W. Ryder (1912)",
        "gutenberg_id": 16659,
        "url":          "https://www.gutenberg.org/cache/epub/16659/pg16659.txt",
        "parse_mode":   "kalidasa",
        "collection":   "Kalidasa",
    },
]

TARGET_WORDS = 350
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_WHITESPACE = re.compile(r'\s+')

KANDA_NAMES = {
    "BOOK I":   "Bala Kanda (Book of Childhood)",
    "BOOK II":  "Ayodhya Kanda (Book of Ayodhya)",
    "BOOK III": "Aranya Kanda (Book of the Forest)",
    "BOOK IV":  "Kishkindha Kanda (Book of Kishkindha)",
    "BOOK V":   "Sundara Kanda (Book of Beauty)",
    "BOOK VI":  "Yuddha Kanda (Book of War)",
    "BOOK VII": "Uttara Kanda (Epilogue)",
}


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


def _parse_ramayana(text: str) -> list[dict]:
    """
    Griffith's Ramayana uses BOOK I ... BOOK VII for kandas, then CANTO headings.
    We sample specific kandas (I, II, VI, VII) and chunk by canto.
    """
    # Find book boundaries
    book_re = re.compile(
        r'(?:^|\n)(BOOK\s+(?:I{1,3}|IV|V?I{1,3}|VII))\b',
        re.IGNORECASE | re.MULTILINE
    )
    book_matches = list(book_re.finditer(text))

    if not book_matches:
        # Fallback: paragraph chunks on whole text
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Ramayana")

    chunks = []
    for bi, match in enumerate(book_matches):
        book_label = match.group(1).strip().upper()
        # Only ingest sampled kandas
        if book_label not in RAMAYANA_SAMPLE_KANDAS:
            continue

        kanda_name = KANDA_NAMES.get(book_label, book_label)
        start = match.start()
        end   = book_matches[bi+1].start() if bi+1 < len(book_matches) else len(text)
        body  = text[start:end]

        # Split by canto within this kanda
        canto_re = re.compile(
            r'(?:^|\n)(CANTO\s+[IVXLCDM\d]+\.?\s*[\w\s]*)\n',
            re.IGNORECASE | re.MULTILINE
        )
        canto_matches = list(canto_re.finditer(body))

        if canto_matches:
            for ci, cm in enumerate(canto_matches):
                canto_label = cm.group(1).strip()
                cs = cm.start()
                ce = canto_matches[ci+1].start() if ci+1 < len(canto_matches) else len(body)
                canto_body = _clean(body[cs:ce])
                if not canto_body or len(canto_body.split()) < 20:
                    continue
                ref = f"Ramayana, {kanda_name}, {canto_label}"
                # If canto is very long, sub-chunk it
                if len(canto_body.split()) > TARGET_WORDS * 2:
                    sub_paras = [_clean(p) for p in re.split(r'\n{2,}', body[cs:ce]) if p.strip()]
                    sub_chunks = _chunk_paragraphs(sub_paras, ref)
                    chunks.extend(sub_chunks)
                else:
                    chunks.append({'text': canto_body, 'reference': ref,
                                   'chapter': kanda_name,
                                   'word_count': len(canto_body.split()),
                                   'token_count': _approx_tokens(canto_body)})
        else:
            # No canto splits — paragraph chunk the whole kanda
            paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
            sub = _chunk_paragraphs(paras, f"Ramayana, {kanda_name}")
            chunks.extend(sub)

    return chunks


def _parse_kalidasa(text: str) -> list[dict]:
    """
    Ryder's Kalidasa: multiple works (Shakuntala, Meghaduta, others).
    Split at work/act boundaries, then paragraph-chunk.
    """
    # Look for major work titles or ACT headers
    act_re = re.compile(
        r'(?:^|\n)(ACT\s+[IVXLCDM\d]+|SHAKUNTALA|THE LITTLE CLAY CART|MEGHADUTA|'
        r'THE CLOUD MESSENGER|THE SEASON OF SPRING|MALATI AND MADHAVA|URVASIE)\b',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(act_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Kalidasa")

    chunks = []
    for mi, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[mi+1].start() if mi+1 < len(matches) else len(text)
        body  = text[start:end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
        sub   = _chunk_paragraphs(paras, f"Kalidasa — {label}")
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
                resp = httpx.get(text_def['url'], timeout=180.0, follow_redirects=True,
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

        if mode == 'ramayana':
            chunks = _parse_ramayana(text)
        elif mode == 'kalidasa':
            chunks = _parse_kalidasa(text)
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
    print(f"Sanskrit Classical ingestion complete.")
    print(f"  Texts ingested : {total_texts}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Sanskrit Classical Literature (Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
