# -*- coding: utf-8 -*-
"""
Political Philosophy ingestion (Public Domain translations/originals).

Texts:
  - Hobbes,        Leviathan                         (1651)    PG #3207
  - Machiavelli,   The Prince                        (1532)    PG #1232
  - Rousseau,      The Social Contract & Discourses  (1762)    PG #46333
  - Burke,         Reflections on the Revolution     (1790)    PG #15679
  - Tocqueville,   Democracy in America Vol I        (1835)    PG #815
  - Mill,          On Liberty                        (1859)    PG #34901
  - Mill,          Utilitarianism                    (1863)    PG #11224
  - Wollstonecraft,Vindication of Rights of Woman    (1792)    PG #3420
  - The Federalist Papers                            (1788)    PG #1404
  - Paine,         Rights of Man                     (1791)    PG #3742

All PD in USA. All authors deceased >70 years.

Corpus code: political-philosophy
Tradition  : western-philosophy
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

CORPUS_CODE = "political-philosophy"
CORPUS_NAME = "Political Philosophy — Hobbes to Mill (PD)"
TRADITION   = "western-philosophy"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org"

TARGET_WORDS = 350
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30
IA_HEADERS   = {'User-Agent': 'Mozilla/5.0 (compatible; academic-research/1.0)'}

TEXTS = [
    {
        "external_id":  "hobbes-leviathan",
        "title":        "Leviathan",
        "author":       "Thomas Hobbes",
        "translator":   "Thomas Hobbes (1651)",
        "gutenberg_id": 3207,
        "source_type":  "ia",
        "url":          "https://archive.org/download/hobbessleviathan00hobbuoft/hobbessleviathan00hobbuoft_djvu.txt",
        "parse_mode":   "part_chapter",
        "collection":   "Political Classics",
    },
    {
        "external_id":  "machiavelli-the-prince",
        "title":        "The Prince",
        "author":       "Niccolò Machiavelli",
        "translator":   "W. K. Marriott (1908)",
        "gutenberg_id": 1232,
        "url":          "https://www.gutenberg.org/cache/epub/1232/pg1232.txt",
        "parse_mode":   "chapters",
        "collection":   "Political Classics",
    },
    {
        "external_id":  "rousseau-social-contract",
        "title":        "The Social Contract and Discourses",
        "author":       "Jean-Jacques Rousseau",
        "translator":   "G. D. H. Cole (1913)",
        "gutenberg_id": 46333,
        "source_type":  "ia",
        # IA University of Toronto scan: Cole translation; Gutenberg CDN drops connection
        "url":          "https://archive.org/download/therepublicofpla00rousuoft/therepublicofpla00rousuoft_djvu.txt",
        "parse_mode":   "part_chapter",
        "collection":   "Social Contract Tradition",
    },
    {
        "external_id":  "burke-reflections-revolution",
        "title":        "Reflections on the Revolution in France",
        "author":       "Edmund Burke",
        "translator":   "Edmund Burke (1790)",
        "gutenberg_id": 15679,
        "url":          "https://www.gutenberg.org/cache/epub/15679/pg15679.txt",
        "parse_mode":   "paragraphs",
        "collection":   "Conservative Thought",
    },
    {
        "external_id":  "tocqueville-democracy-america",
        "title":        "Democracy in America, Vol I",
        "author":       "Alexis de Tocqueville",
        "translator":   "Henry Reeve (1835)",
        "gutenberg_id": 815,
        "source_type":  "ia",
        "url":          "https://archive.org/download/democracyinamerica01tocquoft/democracyinamerica01tocquoft_djvu.txt",
        "parse_mode":   "part_chapter",
        "collection":   "Liberal Thought",
    },
    {
        "external_id":  "mill-on-liberty",
        "title":        "On Liberty",
        "author":       "John Stuart Mill",
        "translator":   "John Stuart Mill (1859)",
        "gutenberg_id": 34901,
        "url":          "https://www.gutenberg.org/cache/epub/34901/pg34901.txt",
        "parse_mode":   "chapters",
        "collection":   "Liberal Thought",
    },
    {
        "external_id":  "mill-utilitarianism",
        "title":        "Utilitarianism",
        "author":       "John Stuart Mill",
        "translator":   "John Stuart Mill (1863)",
        "gutenberg_id": 11224,
        "url":          "https://www.gutenberg.org/cache/epub/11224/pg11224.txt",
        "parse_mode":   "chapters",
        "collection":   "Liberal Thought",
    },
    {
        "external_id":  "wollstonecraft-vindication",
        "title":        "A Vindication of the Rights of Woman",
        "author":       "Mary Wollstonecraft",
        "translator":   "Mary Wollstonecraft (1792)",
        "gutenberg_id": 3420,
        "source_type":  "ia",
        "url":          "https://archive.org/download/vindicationofrig00wolliala/vindicationofrig00wolliala_djvu.txt",
        "parse_mode":   "chapters",
        "collection":   "Liberal Thought",
    },
    {
        "external_id":  "federalist-papers",
        "title":        "The Federalist Papers",
        "author":       "Alexander Hamilton; James Madison; John Jay",
        "translator":   "Hamilton / Madison / Jay (1788)",
        "gutenberg_id": 1404,
        "source_type":  "ia",
        "url":          "https://archive.org/download/federalistpapers00hami/federalistpapers00hami_djvu.txt",
        "parse_mode":   "federalist",
        "collection":   "American Founding",
    },
    {
        "external_id":  "paine-rights-of-man",
        "title":        "Rights of Man",
        "author":       "Thomas Paine",
        "translator":   "Thomas Paine (1791)",
        "gutenberg_id": 3742,
        "source_type":  "ia",
        "url":          "https://archive.org/download/rightsofman00pain/rightsofman00pain_djvu.txt",
        "parse_mode":   "part_chapter",
        "collection":   "American Founding",
    },
]

_WHITESPACE = re.compile(r'\s+')


def _clean(s: str) -> str:
    return _WHITESPACE.sub(' ', (s or '').strip())


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _clean_djvu(text: str) -> str:
    text = re.sub(r'(?:^|\n)\s*\d{1,4}\s*(?:\n|$)', '\n', text, flags=re.MULTILINE)
    text = re.sub(r'\b([A-Z]) ([A-Z])( [A-Z])+\b',
                  lambda m: m.group(0).replace(' ', ''), text)
    for marker in ['This is a digital copy', 'Google Books', 'Digitized by']:
        idx = text.find(marker)
        if 0 <= idx < 8000:
            for cm_str in ['CHAPTER', 'PART I', 'BOOK I', 'INTRODUCTION', 'PREFACE']:
                cm = text.find(cm_str, idx)
                if cm > 0:
                    text = text[cm:]
                    break
    text = re.sub(r'-\n\s+', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_gutenberg(text: str) -> str:
    for marker in ["*** START OF THE PROJECT GUTENBERG EBOOK",
                   "*** START OF THIS PROJECT GUTENBERG EBOOK",
                   "*END*THE SMALL PRINT"]:
        idx = text.find(marker)
        if idx >= 0:
            after = text[idx + len(marker):]
            nl = after.find('\n')
            text = after[nl + 1:] if nl >= 0 else after
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
            chunks.append({'text': txt, 'reference': f"{ref_prefix} — §{chunk_num}",
                           'chapter': ref_prefix,
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            chunk_num += 1
            buffer    = [para]
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


def _parse_paragraphs(text: str, title: str) -> list[dict]:
    paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
    return _chunk_paragraphs(paras, title)


def _parse_chapters(text: str, title: str) -> list[dict]:
    chap_re = re.compile(
        r'(?:^|\n)(CHAPTER\s+[IVXLCDM\d]+\.?\s*[\w ,\-]*?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(chap_re.finditer(text))
    if not matches:
        return _parse_paragraphs(text, title)

    chunks: list[dict] = []
    for ci, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[ci + 1].start() if ci + 1 < len(matches) else len(text)
        body  = _clean(text[start:end])
        if not body or len(body.split()) < 10:
            continue
        if len(body.split()) > TARGET_WORDS * 2:
            paras = [_clean(p) for p in re.split(r'\n{2,}', text[start:end]) if p.strip()]
            chunks.extend(_chunk_paragraphs(paras, f"{title} — {label}"))
        else:
            ref = f"{title} — {label}"
            chunks.append({'text': body, 'reference': ref, 'chapter': label,
                           'word_count': len(body.split()), 'token_count': _approx_tokens(body)})
    return chunks


def _parse_part_chapter(text: str, title: str) -> list[dict]:
    """Handles PART I, PART II ... each with CHAPTER subsections."""
    part_re = re.compile(
        r'(?:^|\n)((?:PART|BOOK)\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|'
        r'I{1,3}V?|V?I{0,3}|IX|X{1,3})\b[\w\s,\-]*?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    p_matches = list(part_re.finditer(text))

    if not p_matches:
        return _parse_chapters(text, title)

    chunks: list[dict] = []
    for pi, pm in enumerate(p_matches):
        part_label = pm.group(1).strip()
        p_start    = pm.start()
        p_end      = p_matches[pi + 1].start() if pi + 1 < len(p_matches) else len(text)
        part_text  = text[p_start:p_end]

        chap_re = re.compile(r'(?:^|\n)(CHAPTER\s+\w+[\w\s,\-\.]*?)\n',
                              re.IGNORECASE | re.MULTILINE)
        c_matches = list(chap_re.finditer(part_text))

        if c_matches:
            for ci, cm in enumerate(c_matches):
                c_label = cm.group(1).strip()
                c_start = cm.start()
                c_end   = c_matches[ci + 1].start() if ci + 1 < len(c_matches) else len(part_text)
                c_text  = _clean(part_text[c_start:c_end])
                if not c_text or len(c_text.split()) < 10:
                    continue
                if len(c_text.split()) > TARGET_WORDS * 2:
                    paras = [_clean(p) for p in re.split(r'\n{2,}', part_text[c_start:c_end]) if p.strip()]
                    chunks.extend(_chunk_paragraphs(paras, f"{title}, {part_label} — {c_label}"))
                else:
                    ref = f"{title}, {part_label} — {c_label}"
                    chunks.append({'text': c_text, 'reference': ref, 'chapter': part_label,
                                   'word_count': len(c_text.split()), 'token_count': _approx_tokens(c_text)})
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', part_text) if p.strip() and len(p.split()) > 5]
            chunks.extend(_chunk_paragraphs(paras, f"{title}, {part_label}"))

    return chunks


def _parse_federalist(text: str) -> list[dict]:
    """
    The Federalist Papers: each paper is "FEDERALIST No. N" / "THE FEDERALIST No. N".
    One chunk per paper (most are ~2,000-4,000 words; split long ones).
    """
    paper_re = re.compile(
        r'(?:^|\n)((?:THE\s+)?FEDERALIST\s+(?:No\.?|NUMBER)\s*\d+)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(paper_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "The Federalist Papers")

    chunks: list[dict] = []
    for mi, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[mi + 1].start() if mi + 1 < len(matches) else len(text)
        body  = _clean(text[start:end])
        if not body or len(body.split()) < 20:
            continue
        if len(body.split()) > TARGET_WORDS * 3:
            paras = [_clean(p) for p in re.split(r'\n{2,}', text[start:end]) if p.strip()]
            chunks.extend(_chunk_paragraphs(paras, f"The Federalist Papers — {label}"))
        else:
            ref = f"The Federalist Papers — {label}"
            chunks.append({'text': body, 'reference': ref, 'chapter': label,
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


def _upsert_text(conn, corpus_id: int, text_def: dict) -> int:
    url = f"https://www.gutenberg.org/ebooks/{text_def['gutenberg_id']}"
    display = f"{text_def['title']} — {text_def['author']}"
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


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for text_def in TEXTS:
        print(f"\n{'='*60}")
        print(f"Ingesting: {text_def['title']} — {text_def['author']}")

        source_type = text_def.get('source_type', 'gutenberg')
        dl_headers  = IA_HEADERS if source_type == 'ia' else {}
        raw = None
        for attempt in range(3):
            try:
                resp = httpx.get(text_def['url'], timeout=180.0, follow_redirects=True,
                                 headers=dl_headers)
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

        if source_type == 'ia':
            text = _clean_djvu(raw).replace('\r\n', '\n').replace('\r', '\n')
        else:
            text = _strip_gutenberg(raw).replace('\r\n', '\n').replace('\r', '\n')
        mode = text_def['parse_mode']

        if mode == 'chapters':
            chunks = _parse_chapters(text, text_def['title'])
        elif mode == 'part_chapter':
            chunks = _parse_part_chapter(text, text_def['title'])
        elif mode == 'federalist':
            chunks = _parse_federalist(text)
        else:
            chunks = _parse_paragraphs(text, text_def['title'])

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
                        chunk.get('chapter', text_def['title']), None,
                        chunk['word_count'], chunk['token_count'],
                        None, LANGUAGE, TRADITION, CORPUS_CODE,
                        text_def['collection'], False,
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
            print(f"  [ERROR] Write failed for {text_def['title']}: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Political Philosophy ingestion complete.")
    print(f"  Texts  : {total_texts}")
    print(f"  Chunks : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Political Philosophy corpus (PD)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
