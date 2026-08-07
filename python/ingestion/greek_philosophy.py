# -*- coding: utf-8 -*-
"""
Greek Classical Philosophy ingestion (Public Domain).

Texts included (all from Project Gutenberg, PD translations):
  - Marcus Aurelius, Meditations          — George Long, 1862  (#2680)
  - Epictetus, Discourses + Enchiridion   — George Long, 1877  (#10661)
  - Plato, The Apology of Socrates        — Benjamin Jowett, 1871 (#1656)
  - Plato, Phaedo                         — Benjamin Jowett, 1871 (#1658)
  - Aristotle, Nicomachean Ethics         — D.P. Chase, 1847    (#8438)

Corpus code: greek-philosophy
Tradition  : hellenism
Chunking   : text-specific modes:
  meditations — book/numbered-entry grouping (~200 words per chunk)
  discourse   — individual discourse or ~400-word passage chunks
  dialogue    — ~400-word paragraph-group chunks (Plato)
  ethics      — book/chapter-level chunks (Aristotle)
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
from typing import Literal

import httpx
import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, execute, execute_one
from embed import embed_documents

CORPUS_CODE = "greek-philosophy"
CORPUS_NAME = "Greek Classical Philosophy"
TRADITION   = "hellenism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org"

TEXTS = [
    {
        "external_id":  "marcus-aurelius-meditations",
        "title":        "Meditations",
        "author":       "Marcus Aurelius",
        "translator":   "George Long (1862)",
        "gutenberg_id": 2680,
        "url":          "https://www.gutenberg.org/cache/epub/2680/pg2680.txt",
        "parse_mode":   "meditations",
        "collection":   "Stoicism",
    },
    {
        "external_id":  "epictetus-discourses-enchiridion",
        "title":        "Discourses and Enchiridion (selection)",
        "author":       "Epictetus",
        "translator":   "George Long (1877)",
        "gutenberg_id": 10661,
        "url":          "https://www.gutenberg.org/cache/epub/10661/pg10661.txt",
        "parse_mode":   "discourse",
        "collection":   "Stoicism",
    },
    {
        "external_id":  "plato-apology",
        "title":        "The Apology of Socrates",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 1656,
        "url":          "https://www.gutenberg.org/cache/epub/1656/pg1656.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "plato-phaedo",
        "title":        "Phaedo",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 1658,
        "url":          "https://www.gutenberg.org/cache/epub/1658/pg1658.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "aristotle-nicomachean-ethics",
        "title":        "Nicomachean Ethics",
        "author":       "Aristotle",
        "translator":   "D. P. Chase (1847)",
        "gutenberg_id": 8438,
        "url":          "https://www.gutenberg.org/cache/epub/8438/pg8438.txt",
        "parse_mode":   "ethics",
        "collection":   "Aristotelian Works",
    },
    # ── Phase A additions ──────────────────────────────────────────────────────
    {
        "external_id":  "plato-republic",
        "title":        "The Republic",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 55201,
        "url":          "https://www.gutenberg.org/cache/epub/55201/pg55201.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "plato-symposium",
        "title":        "The Symposium",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 1600,
        "url":          "https://www.gutenberg.org/cache/epub/1600/pg1600.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "plato-meno",
        "title":        "Meno",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 1643,
        "url":          "https://www.gutenberg.org/cache/epub/1643/pg1643.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "plato-timaeus",
        "title":        "Timaeus",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 1572,
        "url":          "https://www.gutenberg.org/cache/epub/1572/pg1572.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "aristotle-politics",
        "title":        "Politics: A Treatise on Government",
        "author":       "Aristotle",
        "translator":   "William Ellis (1776)",
        "gutenberg_id": 6762,
        "url":          "https://www.gutenberg.org/cache/epub/6762/pg6762.txt",
        "parse_mode":   "politics",
        "collection":   "Aristotelian Works",
    },
    {
        "external_id":  "aristotle-rhetoric",
        "title":        "Rhetoric",
        "author":       "Aristotle",
        "translator":   "W. Rhys Roberts (1924)",
        "gutenberg_id": 1080,
        "url":          "https://www.gutenberg.org/cache/epub/1080/pg1080.txt",
        "parse_mode":   "politics",
        "collection":   "Aristotelian Works",
    },
]

TARGET_WORDS = 350
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_WHITESPACE = re.compile(r'\s+')


def _strip_gutenberg(text: str) -> str:
    for marker in ["*** START OF THE PROJECT GUTENBERG EBOOK",
                   "*** START OF THIS PROJECT GUTENBERG EBOOK",
                   "*END*THE SMALL PRINT"]:
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


# ── Meditations parser ─────────────────────────────────────────────────────────

def _parse_meditations(text: str) -> list[dict]:
    """
    Marcus Aurelius Meditations: 12 books, each with numbered entries.
    Groups entries targeting TARGET_WORDS per chunk.
    Long translation (Gutenberg #2680) uses 'THE FIRST BOOK' format and Roman numeral entries.
    """
    book_pattern = re.compile(
        r'^(THE\s+(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|'
        r'SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)\s+BOOK)\s*$',
        re.IGNORECASE | re.MULTILINE
    )
    # Fallback: BOOK THE FIRST or BOOK I
    book_pattern_r = re.compile(
        r'(?:^|\n)(BOOK\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|'
        r'SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH|(?:X{0,3}(?:IX|IV|V?I{0,3})))\b)',
        re.IGNORECASE | re.MULTILINE
    )

    matches = list(book_pattern.finditer(text))
    if not matches:
        matches = list(book_pattern_r.finditer(text))
    if not matches:
        # Numbered entries only
        return _parse_numbered_entries(text, "Meditations", "Meditations")

    chunks = []
    for bi, match in enumerate(matches):
        book_start = match.start()
        book_end   = matches[bi+1].start() if bi+1 < len(matches) else len(text)
        book_label = match.group(1).strip()
        book_num   = bi + 1
        book_text  = text[book_start:book_end]

        # Split by Roman numeral entries (I., II., XXXII., etc.) or decimal (1., 2.)
        entry_pattern = re.compile(
            r'(?:^|\n)\s*([IVXLCivxlc]{1,8}|\d{1,3})\.\s+(?=[A-Z\'"(])',
            re.MULTILINE
        )
        entry_matches = list(entry_pattern.finditer(book_text))

        if not entry_matches:
            paras = [_clean(p) for p in re.split(r'\n{2,}', book_text) if p.strip() and len(p.split()) > 5]
            buffer: list[str] = []
            for para in paras:
                words = para.split()
                if buffer and len(buffer) + len(words) > TARGET_WORDS and sum(len(w.split()) for w in buffer) >= MIN_WORDS:
                    txt = ' '.join(buffer)
                    chunks.append({'text': txt, 'reference': f"Meditations {book_num}", 'chapter': str(book_num),
                                   'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
                    buffer = [para]
                else:
                    buffer.append(para)
            if buffer:
                txt = ' '.join(buffer)
                chunks.append({'text': txt, 'reference': f"Meditations {book_num}", 'chapter': str(book_num),
                               'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            continue

        entries = []
        for ei, em in enumerate(entry_matches):
            entry_start = em.start()
            entry_end   = entry_matches[ei+1].start() if ei+1 < len(entry_matches) else len(book_text)
            entry_num   = em.group(1)
            entry_text  = _clean(book_text[entry_start:entry_end])
            if entry_text and len(entry_text.split()) >= 5:
                entries.append((book_num, entry_num, entry_text))

        # Group entries into chunks
        buffer_entries: list[tuple] = []
        buffer_words = 0
        for entry in entries:
            ew = len(entry[2].split())
            if buffer_entries and buffer_words + ew > TARGET_WORDS and buffer_words >= MIN_WORDS:
                _flush_entries(buffer_entries, chunks)
                buffer_entries = [entry]
                buffer_words   = ew
            else:
                buffer_entries.append(entry)
                buffer_words += ew
        if buffer_entries:
            _flush_entries(buffer_entries, chunks)

    return chunks


def _flush_entries(entries: list[tuple], chunks: list[dict]) -> None:
    if not entries:
        return
    book_num = entries[0][0]
    first_e  = entries[0][1]
    last_e   = entries[-1][1]
    txt      = ' '.join(e[2] for e in entries)
    ref      = (f"Meditations {book_num}.{first_e}" if first_e == last_e
                else f"Meditations {book_num}.{first_e}-{last_e}")
    chunks.append({'text': txt, 'reference': ref, 'chapter': str(book_num),
                   'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})


def _parse_numbered_entries(text: str, title: str, ref_prefix: str) -> list[dict]:
    """Generic numbered-entry parser when no book headers are found."""
    entry_pattern = re.compile(
        r'(?:^|\n)\s*([IVXLCivxlc]{1,8}|\d{1,3})\.\s+(?=[A-Z\'"(])',
        re.MULTILINE
    )
    entry_matches = list(entry_pattern.finditer(text))
    if not entry_matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, ref_prefix)

    chunks = []
    entries = []
    for ei, em in enumerate(entry_matches):
        entry_start = em.start()
        entry_end   = entry_matches[ei+1].start() if ei+1 < len(entry_matches) else len(text)
        entry_num   = em.group(1)
        entry_text  = _clean(text[entry_start:entry_end])
        if entry_text and len(entry_text.split()) >= 5:
            entries.append((entry_num, entry_text))

    buffer: list[tuple] = []
    buffer_words = 0
    for entry in entries:
        ew = len(entry[1].split())
        if buffer and buffer_words + ew > TARGET_WORDS and buffer_words >= MIN_WORDS:
            txt = ' '.join(e[1] for e in buffer)
            first, last = buffer[0][0], buffer[-1][0]
            ref = f"{ref_prefix} {first}" if first == last else f"{ref_prefix} {first}-{last}"
            chunks.append({'text': txt, 'reference': ref, 'chapter': ref_prefix,
                           'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
            buffer = [entry]
            buffer_words = ew
        else:
            buffer.append(entry)
            buffer_words += ew
    if buffer:
        txt = ' '.join(e[1] for e in buffer)
        first, last = buffer[0][0], buffer[-1][0]
        ref = f"{ref_prefix} {first}" if first == last else f"{ref_prefix} {first}-{last}"
        chunks.append({'text': txt, 'reference': ref, 'chapter': ref_prefix,
                       'word_count': len(txt.split()), 'token_count': _approx_tokens(txt)})
    return chunks


# ── Discourse parser (Epictetus) ───────────────────────────────────────────────

def _parse_discourse(text: str) -> list[dict]:
    """
    Epictetus Discourses + Enchiridion.
    Each discourse/chapter is its own chunk (already reasonable size).
    Very long ones are split at ~TARGET_WORDS.
    """
    chap_pattern = re.compile(
        r'(?:^|\n)((?:CHAPTER|DISCOURSE|SECTION)\s+[IVXLCDM\d]+[.\-]?\s*[\w ,]*)\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(chap_pattern.finditer(text))
    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Epictetus")

    chunks = []
    for ci, match in enumerate(matches):
        start = match.start()
        end   = matches[ci+1].start() if ci+1 < len(matches) else len(text)
        label = match.group(1).strip()
        body  = _clean(text[start:end])
        if not body or len(body.split()) < 5:
            continue
        if len(body.split()) > TARGET_WORDS * 2:
            sub = _chunk_paragraphs(
                [_clean(p) for p in re.split(r'\n{2,}', text[start:end]) if p.strip()],
                label
            )
            chunks.extend(sub)
        else:
            chunks.append({'text': body, 'reference': label, 'chapter': label,
                           'word_count': len(body.split()), 'token_count': _approx_tokens(body)})
    return chunks


# ── Dialogue parser (Plato) ────────────────────────────────────────────────────

def _parse_dialogue(text: str, title: str) -> list[dict]:
    """
    Plato dialogues: paragraph-based chunking (~TARGET_WORDS words per chunk).
    Preserves Stephanus page numbers as part of context when present.
    """
    paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 3]
    return _chunk_paragraphs(paras, title)


# ── Ethics parser (Aristotle) ─────────────────────────────────────────────────

def _parse_ethics(text: str) -> list[dict]:
    """
    Aristotle NE: Book I-X, each with named chapters.
    One chunk per chapter (~300-600 words already, no merging needed).
    """
    book_pattern = re.compile(
        r'(?:^|\n)(BOOK\s+(?:I{1,3}V?|V?I{1,3}|IX|X|XI?)\b)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(book_pattern.finditer(text))
    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Nicomachean Ethics")

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip()
        book_start = match.start()
        book_end   = matches[bi+1].start() if bi+1 < len(matches) else len(text)
        book_text  = text[book_start:book_end]

        # Split by chapter headers
        chap_pattern = re.compile(r'(?:^|\n)\s*(CHAPTER\s+\w+)', re.IGNORECASE | re.MULTILINE)
        chap_matches = list(chap_pattern.finditer(book_text))

        if chap_matches:
            for ci, cm in enumerate(chap_matches):
                c_start = cm.start()
                c_end   = chap_matches[ci+1].start() if ci+1 < len(chap_matches) else len(book_text)
                c_label = cm.group(1).strip()
                c_text  = _clean(book_text[c_start:c_end])
                if not c_text or len(c_text.split()) < 10:
                    continue
                ref = f"Nicomachean Ethics, {book_label}, {c_label}"
                chunks.append({'text': c_text, 'reference': ref, 'chapter': book_label,
                               'word_count': len(c_text.split()), 'token_count': _approx_tokens(c_text)})
        else:
            # No chapter splits — use paragraphs
            paras = [_clean(p) for p in re.split(r'\n{2,}', book_text) if p.strip() and len(p.split()) > 5]
            sub = _chunk_paragraphs(paras, f"Nicomachean Ethics, {book_label}")
            chunks.extend(sub)

    return chunks


# ── Politics / Rhetoric parser (Aristotle) ────────────────────────────────────

def _parse_politics(text: str, title: str) -> list[dict]:
    """
    Aristotle Politics and Rhetoric: Book I-VIII structure with chapters.
    Falls through to paragraph chunking if no Book headers are detected.
    """
    book_pattern = re.compile(
        r'(?:^|\n)(BOOK\s+(?:I{1,3}V?|V?I{0,3}|IX|X{1,3})\b)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(book_pattern.finditer(text))
    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, title)

    chunks = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip()
        book_start = match.start()
        book_end   = matches[bi + 1].start() if bi + 1 < len(matches) else len(text)
        book_text  = text[book_start:book_end]

        chap_pattern = re.compile(r'(?:^|\n)\s*(CHAPTER\s+\w+)', re.IGNORECASE | re.MULTILINE)
        chap_matches = list(chap_pattern.finditer(book_text))

        if chap_matches:
            for ci, cm in enumerate(chap_matches):
                c_start = cm.start()
                c_end   = chap_matches[ci + 1].start() if ci + 1 < len(chap_matches) else len(book_text)
                c_label = cm.group(1).strip()
                c_text  = _clean(book_text[c_start:c_end])
                if not c_text or len(c_text.split()) < 10:
                    continue
                # Split long chapters into ~TARGET_WORDS passages
                if len(c_text.split()) > TARGET_WORDS * 2:
                    paras = [_clean(p) for p in re.split(r'\n{2,}', book_text[c_start:c_end]) if p.strip()]
                    sub   = _chunk_paragraphs(paras, f"{title}, {book_label}, {c_label}")
                    chunks.extend(sub)
                else:
                    ref = f"{title}, {book_label}, {c_label}"
                    chunks.append({'text': c_text, 'reference': ref, 'chapter': book_label,
                                   'word_count': len(c_text.split()), 'token_count': _approx_tokens(c_text)})
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', book_text) if p.strip() and len(p.split()) > 5]
            sub   = _chunk_paragraphs(paras, f"{title}, {book_label}")
            chunks.extend(sub)

    return chunks


# ── Generic paragraph chunker ──────────────────────────────────────────────────

def _chunk_paragraphs(paras: list[str], ref_prefix: str) -> list[dict]:
    chunks = []
    buffer: list[str] = []
    chunk_num = 1

    for para in paras:
        words = para.split()
        if buffer and len(' '.join(buffer).split()) + len(words) > TARGET_WORDS and \
                len(' '.join(buffer).split()) >= MIN_WORDS:
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


# ── DB helpers ─────────────────────────────────────────────────────────────────

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
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, text_def['external_id'], display, TRADITION, LANGUAGE,
          text_def['collection'], url))
    conn.commit()
    return row['id']


# ── Main ───────────────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for text_def in TEXTS:
        print(f"\n{'='*60}")
        print(f"Ingesting: {text_def['title']} by {text_def['author']}")
        print(f"  Source: Gutenberg #{text_def['gutenberg_id']} — {text_def['translator']}")

        raw = None
        for attempt in range(3):
            try:
                resp = httpx.get(text_def['url'], timeout=120.0, follow_redirects=True)
                if resp.status_code == 404:
                    print(f"  404 — skipping.")
                    break
                resp.raise_for_status()
                raw = resp.text
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

        if mode == "meditations":
            chunks = _parse_meditations(text)
        elif mode == "discourse":
            chunks = _parse_discourse(text)
        elif mode == "dialogue":
            chunks = _parse_dialogue(text, text_def['title'])
        elif mode == "ethics":
            chunks = _parse_ethics(text)
        elif mode == "politics":
            chunks = _parse_politics(text, text_def['title'])
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
            chunks = _chunk_paragraphs(paras, text_def['title'])

        print(f"  Parsed {len(chunks)} chunks (mode={mode})")

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
            with conn.cursor() as cur:
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
            conn.commit()

        if not chunks:
            print("  No chunks produced — skip")
            continue

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
                        chunk['chapter'], None,
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
    print(f"Greek Philosophy ingestion complete.")
    print(f"  Texts ingested : {total_texts}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Greek Classical Philosophy (Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
