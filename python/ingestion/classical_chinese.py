# -*- coding: utf-8 -*-
"""
Classical Chinese Philosophy ingestion (Public Domain translations, Project Gutenberg).

Texts included:
  - Confucius, The Analects    — James Legge (1861)   Gutenberg #3330
  - Laozi, Tao Te Ching        — James Legge (1891)   Gutenberg #216
  - Confucius, The Shih King (Book of Poetry) — Legge (1871) Gutenberg #9394
  - Sun Tzu, The Art of War    — Lionel Giles (1910)  Gutenberg #132

All translations published before 1926 — Public Domain in the USA.

Corpus code : classical-chinese
Tradition   : taoism / confucianism → stored as 'east-asian'
Chunking    : book/chapter-level groups (~350 words)

Note: Mencius, Great Learning, Doctrine of the Mean, and I Ching are planned Phase 2
      additions when Gutenberg IDs or clean PD source URLs are confirmed.
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

CORPUS_CODE = "classical-chinese"
CORPUS_NAME = "Classical Chinese Philosophy"
TRADITION   = "east-asian"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org"

TEXTS = [
    {
        "external_id":  "analects-confucius-legge",
        "title":        "The Analects of Confucius",
        "author":       "Confucius",
        "translator":   "James Legge (1861)",
        "gutenberg_id": 3330,
        "url":          "https://www.gutenberg.org/cache/epub/3330/pg3330.txt",
        "parse_mode":   "analects",
        "collection":   "Confucian Classics",
    },
    {
        "external_id":  "tao-te-ching-legge",
        "title":        "Tao Te Ching (The Tao and its Characteristics)",
        "author":       "Laozi",
        "translator":   "James Legge (1891)",
        "gutenberg_id": 216,
        "url":          "https://www.gutenberg.org/cache/epub/216/pg216.txt",
        "parse_mode":   "tao",
        "collection":   "Taoist Classics",
    },
    {
        "external_id":  "shih-king-legge",
        "title":        "The Shih King (Book of Poetry)",
        "author":       "Various (compiled by Confucius)",
        "translator":   "James Legge (1871)",
        "gutenberg_id": 9394,
        "url":          "https://www.gutenberg.org/cache/epub/9394/pg9394.txt",
        "parse_mode":   "shih_king",
        "collection":   "Confucian Classics",
    },
    {
        "external_id":  "art-of-war-giles",
        "title":        "The Art of War",
        "author":       "Sun Tzu",
        "translator":   "Lionel Giles (1910)",
        "gutenberg_id": 132,
        "url":          "https://www.gutenberg.org/cache/epub/132/pg132.txt",
        "parse_mode":   "art_of_war",
        "collection":   "Chinese Military Classics",
    },
]

TARGET_WORDS = 350
MIN_WORDS    = 60
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


def _parse_analects(text: str) -> list[dict]:
    """
    Analects (Legge #3330): BOOK I.  HSIO R. ... BOOK XX.
    Chapters: CHAPTER I. / CHAP. II.
    Group chapters into ~TARGET_WORDS chunks per book.
    """
    book_re = re.compile(
        r'(?:^|\n)(BOOK\s+[IVXLC]+\.\s*\w+)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(book_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Analects")

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip()
        start = match.start()
        end   = matches[bi+1].start() if bi+1 < len(matches) else len(text)
        body  = text[start:end]

        # Extract individual sayings — numbered like "1." or "I."
        saying_re = re.compile(
            r'(?:^|\n)\s*(?:CHAP(?:TER)?\.?\s+[IVXLC\d]+\.?|(?:\d+|[IVX]+)\.\s+(?=[A-Z]))',
            re.MULTILINE | re.IGNORECASE
        )
        saying_matches = list(saying_re.finditer(body))

        if saying_matches:
            sayings = []
            for si, sm in enumerate(saying_matches):
                s_start = sm.start()
                s_end   = saying_matches[si+1].start() if si+1 < len(saying_matches) else len(body)
                saying  = _clean(body[s_start:s_end])
                if saying and len(saying.split()) >= 5:
                    sayings.append(saying)

            # Group sayings into chunks
            buffer: list[str] = []
            buf_words = 0
            chunk_num = 1
            for saying in sayings:
                sw = len(saying.split())
                if buffer and buf_words + sw > TARGET_WORDS and buf_words >= MIN_WORDS:
                    txt = ' '.join(buffer)
                    chunks.append({'text': txt,
                                   'reference': f"Analects {book_label} — sayings {chunk_num}",
                                   'chapter': book_label,
                                   'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
                    chunk_num += 1
                    buffer = [saying]
                    buf_words = sw
                else:
                    buffer.append(saying)
                    buf_words += sw
            if buffer:
                txt = ' '.join(buffer)
                chunks.append({'text': txt,
                               'reference': f"Analects {book_label} — sayings {chunk_num}",
                               'chapter': book_label,
                               'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
        else:
            # Fallback: paragraph chunk
            paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
            sub = _chunk_paragraphs(paras, f"Analects {book_label}")
            chunks.extend(sub)

    return chunks


def _parse_tao(text: str) -> list[dict]:
    """
    Tao Te Ching (#216 Legge): PART 1 and PART 2 with paragraph chapters.
    Paragraph-grouped into ~TARGET_WORDS chunks (chapters are very short, no numbered headers).
    """
    chap_re = re.compile(
        r'(?:^|\n)(PART\s+\d+\.?\s*$)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(chap_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Tao Te Ching")

    # Sub-chunk each Part into ~TARGET_WORDS paragraph groups
    chunks = []
    for pi, match in enumerate(matches):
        part_label = match.group(1).strip()
        start = match.start()
        end   = matches[pi+1].start() if pi+1 < len(matches) else len(text)
        body  = text[start:end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
        sub   = _chunk_paragraphs(paras, f"Tao Te Ching {part_label}")
        chunks.extend(sub)

    return chunks


def _parse_shih_king(text: str) -> list[dict]:
    """
    Shih King (Book of Poetry): 4 parts, many odes.
    Split by Part, then chunk by ode groups.
    """
    part_re = re.compile(
        r'(?:^|\n)(PART\s+(?:I{1,4}|FIRST|SECOND|THIRD|FOURTH)\.?\s*$)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(part_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Shih King")

    chunks = []
    for pi, match in enumerate(matches):
        part_label = match.group(1).strip()
        start = match.start()
        end   = matches[pi+1].start() if pi+1 < len(matches) else len(text)
        body  = text[start:end]
        paras = [_clean(p) for p in re.split(r'\n{2,}', body) if p.strip() and len(p.split()) > 5]
        sub   = _chunk_paragraphs(paras, f"Shih King {part_label}")
        chunks.extend(sub)

    return chunks


def _parse_art_of_war(text: str) -> list[dict]:
    """
    Art of War (Giles #132): 'Chapter I. Laying plans', 'Chapter II. Waging War', etc.
    Each of the 13 chapters is its own chunk (already reasonable size).
    """
    chap_re = re.compile(
        r'(?:^|\n)(Chapter\s+[IVXLC]+\.?\s+[\w\s,\-]+)\n',
        re.MULTILINE
    )
    matches = list(chap_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Art of War")

    chunks = []
    buffer: list[tuple] = []
    buf_words = 0

    for ci, match in enumerate(matches):
        chap_label = match.group(1).strip()
        start = match.start()
        end   = matches[ci+1].start() if ci+1 < len(matches) else len(text)
        body  = _clean(text[start:end])
        words = len(body.split())

        if not body or words < 10:
            continue

        if buffer and buf_words + words > TARGET_WORDS and buf_words >= MIN_WORDS:
            first_label = buffer[0][0]
            last_label  = buffer[-1][0]
            txt = ' '.join(b[1] for b in buffer)
            ref = first_label if first_label == last_label else f"{first_label} – {last_label}"
            chunks.append({'text': txt, 'reference': f"Art of War — {ref}",
                           'chapter': "Art of War",
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            buffer = [(chap_label, body)]
            buf_words = words
        else:
            buffer.append((chap_label, body))
            buf_words += words

    if buffer:
        first_label = buffer[0][0]
        last_label  = buffer[-1][0]
        txt = ' '.join(b[1] for b in buffer)
        ref = first_label if first_label == last_label else f"{first_label} – {last_label}"
        chunks.append({'text': txt, 'reference': f"Art of War — {ref}",
                       'chapter': "Art of War",
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})

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

        if mode == 'analects':
            chunks = _parse_analects(text)
        elif mode == 'tao':
            chunks = _parse_tao(text)
        elif mode == 'shih_king':
            chunks = _parse_shih_king(text)
        elif mode == 'art_of_war':
            chunks = _parse_art_of_war(text)
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
    print(f"Classical Chinese ingestion complete.")
    print(f"  Texts ingested : {total_texts}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Classical Chinese Philosophy (Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
