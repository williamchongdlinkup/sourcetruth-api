# -*- coding: utf-8 -*-
"""
Western Philosophy ingestion (Public Domain translations).

Sub-verticals:
  Ancient Greek (additional):
    - Plato, Phaedrus          — Jowett (1871)    PG #1636
    - Plato, Theaetetus        — Jowett (1871)    PG #1726
    - Aristotle, Metaphysics   — Ross (1908)       PG #12699

  Early Modern Philosophy:
    - Descartes, Discourse on Method   — Veitch (1850)       PG #59
    - Descartes, Six Meditations       — Haldane/Ross (1911) PG #70091
    - Locke, Two Treatises of Gov't    — (1689, PD)          PG #7370
    - Locke, Essay on Human Under.     — Calkins abridg.     PG #10615
    - Hume, Enquiry (Human Under.)     — (1748, PD)          PG #9662
    - Hume, Dialogues on Religion      — (1779, PD)          PG #4583
    - Spinoza, Ethics                  — White (1883)        PG #3800

  German Idealism:
    - Kant, Critique of Pure Reason    — Meiklejohn (1855)   PG #4280
    - Kant, Fundamental Principles     — Abbott (1895)       PG #5682
    - Schopenhauer, World as Will V1   — Haldane/Kemp (1883) PG #38427
    - Hegel, Lectures Hist. Philosophy — Haldane (1892)      PG #51635

  Nietzsche:
    - Nietzsche, Thus Spoke Zarathustra — Common (1909)      PG #1998
    - Nietzsche, Beyond Good and Evil   — Zimmern (1907)     PG #4363
    - Nietzsche, Genealogy of Morals    — Samuel (1913)      PG #52319

All PD in USA. Translators/editors all deceased >70 years.

Corpus code: western-philosophy
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

CORPUS_CODE = "western-philosophy"
CORPUS_NAME = "Western Philosophy — Early Modern to Nietzsche (PD)"
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
    # ── Ancient Greek expansion ───────────────────────────────────────────────
    {
        "external_id":  "plato-phaedrus",
        "title":        "Phaedrus",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 1636,
        "url":          "https://www.gutenberg.org/cache/epub/1636/pg1636.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "plato-theaetetus",
        "title":        "Theaetetus",
        "author":       "Plato",
        "translator":   "Benjamin Jowett (1871)",
        "gutenberg_id": 1726,
        "url":          "https://www.gutenberg.org/cache/epub/1726/pg1726.txt",
        "parse_mode":   "dialogue",
        "collection":   "Platonic Dialogues",
    },
    {
        "external_id":  "aristotle-metaphysics",
        "title":        "Metaphysics",
        "author":       "Aristotle",
        "translator":   "W. D. Ross (1908)",
        "gutenberg_id": 12699,
        "source_type":  "ia",
        "url":          "https://archive.org/download/aristotlemetaphy00arisuoft/aristotlemetaphy00arisuoft_djvu.txt",
        "parse_mode":   "book_chapter",
        "collection":   "Aristotelian Works",
    },
    # ── Descartes ─────────────────────────────────────────────────────────────
    {
        "external_id":  "descartes-discourse-method",
        "title":        "Discourse on the Method",
        "author":       "René Descartes",
        "translator":   "John Veitch (1850)",
        "gutenberg_id": 59,
        "url":          "https://www.gutenberg.org/cache/epub/59/pg59.txt",
        "parse_mode":   "prose_parts",
        "collection":   "Descartes",
    },
    {
        "external_id":  "descartes-meditations",
        "title":        "Six Meditations on First Philosophy",
        "author":       "René Descartes",
        "translator":   "Elizabeth S. Haldane / G. R. T. Ross (1911)",
        "gutenberg_id": 70091,
        "url":          "https://www.gutenberg.org/cache/epub/70091/pg70091.txt",
        "parse_mode":   "meditations_descartes",
        "collection":   "Descartes",
    },
    # ── Locke ─────────────────────────────────────────────────────────────────
    {
        "external_id":  "locke-two-treatises",
        "title":        "Two Treatises of Government",
        "author":       "John Locke",
        "translator":   "John Locke (1689)",
        "gutenberg_id": 7370,
        "url":          "https://www.gutenberg.org/cache/epub/7370/pg7370.txt",
        "parse_mode":   "book_chapter",
        "collection":   "Locke",
    },
    {
        "external_id":  "locke-essay-human-understanding",
        "title":        "An Essay Concerning Human Understanding (abridged)",
        "author":       "John Locke",
        "translator":   "John Locke (1690)",
        "gutenberg_id": 10615,
        "url":          "https://www.gutenberg.org/cache/epub/10615/pg10615.txt",
        "parse_mode":   "book_chapter",
        "collection":   "Locke",
    },
    # ── Hume ──────────────────────────────────────────────────────────────────
    {
        "external_id":  "hume-enquiry-human-understanding",
        "title":        "An Enquiry Concerning Human Understanding",
        "author":       "David Hume",
        "translator":   "David Hume (1748)",
        "gutenberg_id": 9662,
        "url":          "https://www.gutenberg.org/cache/epub/9662/pg9662.txt",
        "parse_mode":   "prose_sections",
        "collection":   "Hume",
    },
    {
        "external_id":  "hume-dialogues-natural-religion",
        "title":        "Dialogues Concerning Natural Religion",
        "author":       "David Hume",
        "translator":   "David Hume (1779)",
        "gutenberg_id": 4583,
        "url":          "https://www.gutenberg.org/cache/epub/4583/pg4583.txt",
        "parse_mode":   "dialogue",
        "collection":   "Hume",
    },
    # ── Spinoza ───────────────────────────────────────────────────────────────
    {
        "external_id":  "spinoza-ethics",
        "title":        "Ethics",
        "author":       "Baruch Spinoza",
        "translator":   "R. H. M. Elwes (1883)",
        "gutenberg_id": 3800,
        "source_type":  "ia",
        "url":          "https://archive.org/download/in.ernet.dli.2015.263056/2015.263056.Ethics_djvu.txt",
        "parse_mode":   "spinoza_ethics",
        "collection":   "Spinoza",
    },
    # ── Kant ──────────────────────────────────────────────────────────────────
    {
        "external_id":  "kant-critique-pure-reason",
        "title":        "The Critique of Pure Reason",
        "author":       "Immanuel Kant",
        "translator":   "J. M. D. Meiklejohn (1855)",
        "gutenberg_id": 4280,
        "url":          "https://www.gutenberg.org/files/4280/4280-0.txt",
        "parse_mode":   "book_chapter",
        "collection":   "Kant",
    },
    {
        "external_id":  "kant-groundwork-metaphysics-morals",
        "title":        "Fundamental Principles of the Metaphysic of Morals",
        "author":       "Immanuel Kant",
        "translator":   "Thomas Kingsmill Abbott (1895)",
        "gutenberg_id": 5682,
        "url":          "https://www.gutenberg.org/cache/epub/5682/pg5682.txt",
        "parse_mode":   "prose_sections",
        "collection":   "Kant",
    },
    # ── Schopenhauer ──────────────────────────────────────────────────────────
    {
        "external_id":  "schopenhauer-world-as-will-vol1",
        "title":        "The World as Will and Idea, Vol I",
        "author":       "Arthur Schopenhauer",
        "translator":   "R. B. Haldane / J. Kemp (1883)",
        "gutenberg_id": 38427,
        "url":          "https://www.gutenberg.org/files/38427/38427-0.txt",
        "parse_mode":   "book_chapter",
        "collection":   "Schopenhauer",
    },
    # ── Hegel ─────────────────────────────────────────────────────────────────
    {
        "external_id":  "hegel-lectures-history-philosophy-vol1",
        "title":        "Lectures on the History of Philosophy, Vol I",
        "author":       "Georg Wilhelm Friedrich Hegel",
        "translator":   "E. S. Haldane (1892)",
        "gutenberg_id": 51635,
        "url":          "https://www.gutenberg.org/files/51635/51635-0.txt",
        "parse_mode":   "book_chapter",
        "collection":   "Hegel",
    },
    # ── Nietzsche ─────────────────────────────────────────────────────────────
    {
        "external_id":  "nietzsche-thus-spoke-zarathustra",
        "title":        "Thus Spake Zarathustra",
        "author":       "Friedrich Nietzsche",
        "translator":   "Thomas Common (1909)",
        "gutenberg_id": 1998,
        "url":          "https://www.gutenberg.org/files/1998/1998-0.txt",
        "parse_mode":   "nietzsche_zarathustra",
        "collection":   "Nietzsche",
    },
    {
        "external_id":  "nietzsche-beyond-good-and-evil",
        "title":        "Beyond Good and Evil",
        "author":       "Friedrich Nietzsche",
        "translator":   "Helen Zimmern (1907)",
        "gutenberg_id": 4363,
        "source_type":  "ia",
        "url":          "https://archive.org/download/beyondgoodandevi00nietuoft/beyondgoodandevi00nietuoft_djvu.txt",
        "parse_mode":   "nietzsche_numbered",
        "collection":   "Nietzsche",
    },
    {
        "external_id":  "nietzsche-genealogy-of-morals",
        "title":        "The Genealogy of Morals",
        "author":       "Friedrich Nietzsche",
        "translator":   "Horace B. Samuel (1913)",
        "gutenberg_id": 52319,
        "url":          "https://www.gutenberg.org/files/52319/52319-0.txt",
        "parse_mode":   "nietzsche_numbered",
        "collection":   "Nietzsche",
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
            for cm_str in ['PART I', 'BOOK I', 'CHAPTER', 'PREFACE', 'INTRODUCTION']:
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


# ── Generic chunkers ──────────────────────────────────────────────────────────

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


# ── Dialogue (Plato, Hume Dialogues) ─────────────────────────────────────────

def _parse_dialogue(text: str, title: str) -> list[dict]:
    paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 3]
    return _chunk_paragraphs(paras, title)


# ── Book/Chapter structure (Locke, Kant, Schopenhauer, Aristotle Metaphysics) ─

def _parse_book_chapter(text: str, title: str) -> list[dict]:
    """
    Finds BOOK I/II... headers, then CHAPTER I/II... within each book.
    Falls through to paragraph chunking if no structure found.
    """
    book_re = re.compile(
        r'(?:^|\n)(BOOK\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|'
        r'SEVENTH|EIGHTH|NINTH|TENTH|I{1,3}V?|V?I{0,3}X?|IX|XI{0,3}|X{1,3})\b)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(book_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, title)

    chunks: list[dict] = []
    for bi, match in enumerate(matches):
        book_label = match.group(1).strip()
        bk_start   = match.start()
        bk_end     = matches[bi + 1].start() if bi + 1 < len(matches) else len(text)
        book_body  = text[bk_start:bk_end]

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
                    chunks.extend(_chunk_paragraphs(paras, f"{title}, {book_label}, {c_label}"))
                else:
                    ref = f"{title}, {book_label}, {c_label}"
                    chunks.append({'text': c_text, 'reference': ref, 'chapter': book_label,
                                   'word_count': len(c_text.split()), 'token_count': _approx_tokens(c_text)})
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', book_body) if p.strip() and len(p.split()) > 5]
            chunks.extend(_chunk_paragraphs(paras, f"{title}, {book_label}"))

    return chunks


# ── Prose with PART/SECTION headers ──────────────────────────────────────────

def _parse_prose_sections(text: str, title: str) -> list[dict]:
    """
    Handles texts with PART I / SECTION I / numbered sections.
    Used for: Hume Enquiry, Kant Groundwork, Descartes Discourse.
    """
    sec_re = re.compile(
        r'(?:^|\n)((?:PART|SECTION|CHAPTER)\s+[IVXLCDM\d]+\.?\s*[\w ,\-]*?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(sec_re.finditer(text))

    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, title)

    chunks: list[dict] = []
    for si, match in enumerate(matches):
        label  = match.group(1).strip()
        start  = match.start()
        end    = matches[si + 1].start() if si + 1 < len(matches) else len(text)
        body   = _clean(text[start:end])
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


def _parse_prose_parts(text: str, title: str) -> list[dict]:
    """For short philosophical essays with PART labels (Descartes Discourse)."""
    part_re = re.compile(r'(?:^|\n)(PART\s+[IVXLCDM\d]+)', re.IGNORECASE | re.MULTILINE)
    matches = list(part_re.finditer(text))
    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, title)

    chunks: list[dict] = []
    for si, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[si + 1].start() if si + 1 < len(matches) else len(text)
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


# ── Descartes Meditations ──────────────────────────────────────────────────────

def _parse_meditations_descartes(text: str) -> list[dict]:
    """Each Meditation is its own chunk (short, ~500 words)."""
    med_re = re.compile(
        r'(?:^|\n)(MEDITATION\s+(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|[IVX\d]+)\b)',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(med_re.finditer(text))
    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Meditations on First Philosophy")

    chunks: list[dict] = []
    for mi, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[mi + 1].start() if mi + 1 < len(matches) else len(text)
        body  = _clean(text[start:end])
        if not body or len(body.split()) < 10:
            continue
        if len(body.split()) > TARGET_WORDS * 2:
            paras = [_clean(p) for p in re.split(r'\n{2,}', text[start:end]) if p.strip()]
            chunks.extend(_chunk_paragraphs(paras, f"Meditations — {label}"))
        else:
            ref = f"Meditations on First Philosophy — {label}"
            chunks.append({'text': body, 'reference': ref, 'chapter': label,
                           'word_count': len(body.split()), 'token_count': _approx_tokens(body)})
    return chunks


# ── Spinoza Ethics ────────────────────────────────────────────────────────────

def _parse_spinoza_ethics(text: str) -> list[dict]:
    """
    Spinoza's Ethics has 5 Parts, each with Definitions, Axioms, Propositions.
    Parse by Part, then chunk at TARGET_WORDS.
    """
    part_re = re.compile(
        r'(?:^|\n)(PART\s+(?:I{1,3}V?|V?I{0,3}|THE\s+\w+)\b[\w\s,]*?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(part_re.finditer(text))
    if not matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Ethics")

    chunks: list[dict] = []
    for pi, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.start()
        end   = matches[pi + 1].start() if pi + 1 < len(matches) else len(text)
        paras = [_clean(p) for p in re.split(r'\n{2,}', text[start:end]) if p.strip() and len(p.split()) > 5]
        chunks.extend(_chunk_paragraphs(paras, f"Ethics, {label}"))
    return chunks


# ── Nietzsche Zarathustra ─────────────────────────────────────────────────────

def _parse_nietzsche_zarathustra(text: str) -> list[dict]:
    """
    Zarathustra has 4 Parts, each with named discourses.
    One discourse ≈ one chunk (many are short; merge to TARGET_WORDS).
    """
    part_re = re.compile(
        r'(?:^|\n)(PART\s+(?:FIRST|SECOND|THIRD|FOURTH|[IVX\d]+)\b)',
        re.IGNORECASE | re.MULTILINE
    )
    p_matches = list(part_re.finditer(text))

    if not p_matches:
        paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
        return _chunk_paragraphs(paras, "Thus Spake Zarathustra")

    chunks: list[dict] = []
    for pi, pm in enumerate(p_matches):
        part_label = pm.group(1).strip()
        p_start    = pm.start()
        p_end      = p_matches[pi + 1].start() if pi + 1 < len(p_matches) else len(text)
        part_text  = text[p_start:p_end]

        # Discourses: ALL-CAPS titled sections
        disc_re = re.compile(r'(?:^|\n)([A-Z][A-Z ,\'\-]{3,60})\s*\n', re.MULTILINE)
        disc_matches = [m for m in disc_re.finditer(part_text)
                        if 2 <= len(m.group(1).split()) <= 10]

        if disc_matches:
            buffer_secs: list[tuple[str, str]] = []
            buf_words = 0

            def _flush(buf: list[tuple[str, str]]) -> None:
                if not buf:
                    return
                combined = ' '.join(_clean(f"{s[0]} {s[1]}") for s in buf)
                if len(combined.split()) < MIN_WORDS:
                    return
                ref = f"Zarathustra, {part_label} — {buf[0][0][:50]}"
                chunks.append({'text': combined, 'reference': ref, 'chapter': part_label,
                               'word_count': len(combined.split()), 'token_count': _approx_tokens(combined)})

            for di, dm in enumerate(disc_matches):
                ds = dm.start()
                de = disc_matches[di + 1].start() if di + 1 < len(disc_matches) else len(part_text)
                heading = dm.group(1).strip()
                body    = _clean(part_text[ds:de])
                bw      = len(body.split())
                if buffer_secs and buf_words + bw > TARGET_WORDS and buf_words >= MIN_WORDS:
                    _flush(buffer_secs)
                    buffer_secs = [(heading, body)]
                    buf_words   = bw
                else:
                    buffer_secs.append((heading, body))
                    buf_words += bw
            _flush(buffer_secs)
        else:
            paras = [_clean(p) for p in re.split(r'\n{2,}', part_text) if p.strip() and len(p.split()) > 5]
            chunks.extend(_chunk_paragraphs(paras, f"Thus Spake Zarathustra, {part_label}"))

    return chunks


# ── Nietzsche numbered aphorisms (BGE, Genealogy) ────────────────────────────

def _parse_nietzsche_numbered(text: str, title: str) -> list[dict]:
    """
    BGE and Genealogy use numbered aphorisms/sections.
    Group numbered sections into TARGET_WORDS chunks.
    """
    # Try Part/Chapter headers first
    part_re = re.compile(
        r'(?:^|\n)((?:PART|CHAPTER|ESSAY|PREFACE)\s+[\w\s,\-]{0,60}?)\n',
        re.IGNORECASE | re.MULTILINE
    )
    p_matches = list(part_re.finditer(text))

    if p_matches:
        chunks: list[dict] = []
        for pi, pm in enumerate(p_matches):
            part_label = pm.group(1).strip()
            p_start    = pm.start()
            p_end      = p_matches[pi + 1].start() if pi + 1 < len(p_matches) else len(text)
            part_text  = text[p_start:p_end]
            paras = [_clean(p) for p in re.split(r'\n{2,}', part_text) if p.strip() and len(p.split()) > 5]
            chunks.extend(_chunk_paragraphs(paras, f"{title} — {part_label}"))
        return chunks

    paras = [_clean(p) for p in re.split(r'\n{2,}', text) if p.strip() and len(p.split()) > 5]
    return _chunk_paragraphs(paras, title)


# ── DB helpers ────────────────────────────────────────────────────────────────

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
        print(f"  Source : Gutenberg #{text_def['gutenberg_id']} — {text_def['translator']}")

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

        if mode == 'dialogue':
            chunks = _parse_dialogue(text, text_def['title'])
        elif mode == 'book_chapter':
            chunks = _parse_book_chapter(text, text_def['title'])
        elif mode == 'prose_sections':
            chunks = _parse_prose_sections(text, text_def['title'])
        elif mode == 'prose_parts':
            chunks = _parse_prose_parts(text, text_def['title'])
        elif mode == 'meditations_descartes':
            chunks = _parse_meditations_descartes(text)
        elif mode == 'spinoza_ethics':
            chunks = _parse_spinoza_ethics(text)
        elif mode == 'nietzsche_zarathustra':
            chunks = _parse_nietzsche_zarathustra(text)
        elif mode == 'nietzsche_numbered':
            chunks = _parse_nietzsche_numbered(text, text_def['title'])
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
    print(f"Western Philosophy ingestion complete.")
    print(f"  Texts  : {total_texts}")
    print(f"  Chunks : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Western Philosophy corpus (PD)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
