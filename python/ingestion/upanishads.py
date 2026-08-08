# -*- coding: utf-8 -*-
"""
Upanishads ingestion: Max Müller SBE translations (1879-1884, Public Domain).

Sources:
  Part I  (#3283) — Chandogya Upanishad, Kena (Talavakara) Upanishad
  Part II (#8310) — Katha, Mundaka, Taittiriya, Brihadaranyaka, Prasna Upanishads

Both volumes are Sacred Books of the East by F. Max Müller.
Müller died 1900; volumes published 1879 and 1884. Public Domain in the USA.

Chunking: Section-level (~300 words per chunk). Consecutive short sections are merged.
Reference: "Chandogya Upanishad 1.1" (section notation)
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
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import get_conn, execute, execute_one
from embed import embed_documents

GUTENBERG_PARTS = [
    {
        "gutenberg_id": 3283,
        "url": "https://www.gutenberg.org/cache/epub/3283/pg3283.txt",
        "description": "The Upanishads Part I (SBE Vol 1) — Chandogya, Kena",
    },
    {
        "gutenberg_id": 8310,
        "url": "https://www.gutenberg.org/cache/epub/8310/pg8310.txt",
        "description": "The Upanishads Part II (SBE Vol 15) — Katha, Mundaka, Taittiriya, Aitareya, Brihadaranyaka, Prasna",
    },
]

# Known Upanishad names to scan for in the text
KNOWN_UPANISHADS = [
    "KHÂNDOGYA-UPANISHAD",
    "CHANDOGYA UPANISHAD",
    "KHÂNDOGYA UPANISHAD",
    "KENA-UPANISHAD",
    "TALAVAKÂRA-UPANISHAD",
    "KENA UPANISHAD",
    "KATHA-UPANISHAD",
    "KATHA UPANISHAD",
    "MUNDAKA-UPANISHAD",
    "MUNDAKA UPANISHAD",
    "MUNDAKOPANISHAD",
    "TAITTIRÎYA-UPANISHAD",
    "TAITTIRIYA UPANISHAD",
    "AITAREYA-UPANISHAD",
    "AITAREYA UPANISHAD",
    "BRIHADÂRANYAKA-UPANISHAD",
    "BRIHADARANYAKA UPANISHAD",
    "BRIHAD-ÂRANYAKA",
    "PRASNA-UPANISHAD",
    "PRASNA UPANISHAD",
    "ÎSÂ-UPANISHAD",
    "ISA UPANISHAD",
    "SVETÂSVATARA-UPANISHAD",
    "MAITRÂYANA-UPANISHAD",
]

# Canonical name mapping for display
CANONICAL_NAMES = {
    "KHÂNDOGYA": "Chandogya Upanishad",
    "CHANDOGYA": "Chandogya Upanishad",
    "TALAVAKÂRA": "Kena Upanishad",
    "KENA": "Kena Upanishad",
    "KATHA": "Katha Upanishad",
    "MUNDAKA": "Mundaka Upanishad",
    "TAITTIRÎYA": "Taittiriya Upanishad",
    "TAITTIRIYA": "Taittiriya Upanishad",
    "AITAREYA": "Aitareya Upanishad",
    "BRIHADÂRANYAKA": "Brihadaranyaka Upanishad",
    "BRIHADARANYAKA": "Brihadaranyaka Upanishad",
    "BRIHAD": "Brihadaranyaka Upanishad",
    "PRASNA": "Prasna Upanishad",
    "ÎSÂ": "Isa Upanishad",
    "ISA": "Isa Upanishad",
    "SVETÂSVATARA": "Shvetashvatara Upanishad",
    "MAITRÂYANA": "Maitrayani Upanishad",
}

CORPUS_CODE = "upanishads"
CORPUS_NAME = "Principal Upanishads (Müller, SBE)"
TRADITION   = "hinduism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.sacred-texts.com/hin/sbe01/index.htm"

TARGET_WORDS = 300
MIN_WORDS    = 80
MAX_RETRIES  = 5
RETRY_DELAY  = 30

_WHITESPACE = re.compile(r'\s+')
_FOOTNOTE   = re.compile(r'\[\d+\]')  # strip footnote markers like [1] [42]


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
                   "End of Project Gutenberg",
                   "THE ONLINE DISTRIBUTED PROOFREADING TEAM"]:
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
            break
    return text.strip()


def _clean(s: str) -> str:
    s = _FOOTNOTE.sub('', s)
    return _WHITESPACE.sub(' ', s).strip()


def _canonical_name(raw_header: str) -> str:
    upper = raw_header.upper()
    for key, name in CANONICAL_NAMES.items():
        if key in upper:
            return name
    # Generic cleanup
    name = re.sub(r'[-_]UPANISHAD', ' Upanishad', raw_header, flags=re.IGNORECASE)
    name = name.replace('Â', 'a').replace('Î', 'i').replace('Û', 'u').replace('Ñ', 'n')
    return name.title().strip()


_KNOWN_NAMES = (
    r'Isa|Isha|Katha|Kena|Talavak[aâ]ra|Chandogya|Kh[aâ]nd[ao]gya|Kh[aâ]nda|'
    r'Mundaka|Mundak[oō]|Taittir[iî]ya|Aitareya|Briha[d]?[aâ]r[aâ]nyaka|Brihad|Pra[sś]na|'
    r'[ŚS]vet[aâ][sś]vatara|Svetasvatara|Maitr[aâ]yan[iî]|Mandukya'
)

# Known raw header strings from Müller SBE texts (handles diacriticals directly)
_KNOWN_RAW_HEADERS = [name.upper() for name in KNOWN_UPANISHADS] + [
    "KHANDOGYA-UPANISHAD", "TAITTIRIYA-UPANISHAD", "BRIHADARANYAKA-UPANISHAD",
    "PRASNA-UPANISHAD", "MUNDAKA-UPANISHAD", "KATHA-UPANISHAD",
    "AITAREYA-UPANISHAD", "ISA-UPANISHAD", "ISA UPANISHAD",
    "TALAVAKARA-UPANISHAD", "SVETASVATARA-UPANISHAD", "MAITRAYANI-UPANISHAD",
]


def _find_upanishad_sections(text: str) -> list[dict]:
    """
    Find each Upanishad in the combined text by scanning for header lines.
    Pass 1: regex (handles normal forms).
    Pass 2: direct scan for known raw headers if regex finds <2 sections.
    Deduplicates by canonical name, keeping the largest (main content) section.
    """
    # Pass 1: regex — optional "THE " prefix, no nested quantifiers to avoid catastrophic backtracking
    upan_pattern = re.compile(
        r'^\s*(?:THE\s+)?((?:' + _KNOWN_NAMES + r')[\w\-\s]{0,40}[Uu]panishad)\s*$',
        re.MULTILINE | re.IGNORECASE
    )
    matches = list(upan_pattern.finditer(text))

    # Pass 2: if regex found fewer than 2 sections, scan for known raw header strings
    if len(matches) < 2:
        line_positions: list[tuple[int, str]] = []
        text_upper = text.upper()
        for raw_header in _KNOWN_RAW_HEADERS:
            pos = 0
            while True:
                idx = text_upper.find(raw_header, pos)
                if idx < 0:
                    break
                # Only accept if it's a standalone-ish header line
                line_start = text.rfind('\n', 0, idx) + 1
                line_end   = text.find('\n', idx)
                if line_end < 0:
                    line_end = len(text)
                line_content = text[line_start:line_end].strip()
                # Accept if the line is short and dominated by the header
                if len(line_content) < len(raw_header) + 10:
                    line_positions.append((line_start, line_content))
                pos = idx + 1
        # Deduplicate by position and sort
        seen_pos: set[int] = set()
        raw_matches: list[tuple[int, str]] = []
        for pos, content in sorted(line_positions):
            if pos not in seen_pos:
                seen_pos.add(pos)
                raw_matches.append((pos, content))
        raw_matches.sort(key=lambda x: x[0])

        if len(raw_matches) >= 2:
            by_name: dict[str, dict] = {}
            for i, (start, raw_header_str) in enumerate(raw_matches):
                end  = raw_matches[i + 1][0] if i + 1 < len(raw_matches) else len(text)
                body = text[start:end].strip()
                name = _canonical_name(raw_header_str)
                if len(body) > 500:
                    if name not in by_name or len(body) > len(by_name[name]['body']):
                        by_name[name] = {'name': name, 'body': body}
            sections = list(by_name.values())
            if sections:
                return sections

    if not matches:
        return [{'name': 'Upanishads', 'body': text}]

    by_name2: dict[str, dict] = {}
    for i, match in enumerate(matches):
        start = match.start()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body  = text[start:end].strip()
        raw_name = match.group(1).strip()
        name = _canonical_name(raw_name)
        if len(body) > 1000:
            if name not in by_name2 or len(body) > len(by_name2[name]['body']):
                by_name2[name] = {'name': name, 'body': body}

    sections = list(by_name2.values())
    return sections if sections else [{'name': 'Upanishads', 'body': text}]


def _chunk_section(section: dict) -> list[dict]:
    """Split a section body into ~TARGET_WORDS chunks by paragraph boundaries."""
    body = section['body']
    name = section['name']

    # Split into paragraphs
    paragraphs = re.split(r'\n{2,}', body)
    paragraphs = [_clean(p) for p in paragraphs if p.strip() and len(p.split()) > 3]

    if not paragraphs:
        return []

    chunks = []
    current_words: list[str] = []
    para_count = 0

    def _flush(words: list[str], count: int) -> None:
        text = ' '.join(words)
        if not text or len(words) < 10:
            return
        ref = f"{name}, section {count}"
        chunks.append({
            'text':        text,
            'reference':   ref,
            'chapter':     name,
            'word_count':  len(words),
            'token_count': max(1, len(text.encode()) // 4),
        })

    for para in paragraphs:
        words = para.split()
        if current_words and len(current_words) + len(words) > TARGET_WORDS and len(current_words) >= MIN_WORDS:
            _flush(current_words, para_count)
            current_words = words
        else:
            current_words.extend(words)
        para_count += 1

    if current_words:
        _flush(current_words, para_count)

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


def _upsert_text(conn, corpus_id: int, name: str) -> int:
    external_id = f"upanishad-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, name, TRADITION, LANGUAGE, "Principal Upanishads",
          "https://www.gutenberg.org/ebooks/3283"))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for part in GUTENBERG_PARTS:
        print(f"\nDownloading {part['description']} ...")
        raw = None
        for attempt in range(3):
            try:
                resp = httpx.get(part['url'], timeout=120.0, follow_redirects=True)
                if resp.status_code == 404:
                    print(f"  404 — skipping this part.")
                    break
                resp.raise_for_status()
                raw = resp.text
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  Retry {attempt+1}: {e}")
                    time.sleep(5)
                else:
                    print(f"  [ERROR] Failed to download: {e}")

        if raw is None:
            continue

        text = _strip_gutenberg(raw)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        sections = _find_upanishad_sections(text)
        print(f"  Found {len(sections)} Upanishad section(s): {[s['name'] for s in sections]}")

        for section in sections:
            print(f"\n  {section['name']}", end=' ... ', flush=True)

            text_id = _upsert_text(conn, corpus_id, section['name'])

            if not force:
                existing = execute_one(conn,
                    "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
                if existing and existing['n'] > 0:
                    n = existing['n']
                    print(f"already ingested ({n} chunks) — skip")
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

            chunks = _chunk_section(section)
            if not chunks:
                print("no chunks — skip")
                continue

            print(f"{len(chunks)} chunks")

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
                            "Principal Upanishads", False,
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
                print(f"    ✓ {written} chunks committed")
            except Exception as e:
                print(f"  [ERROR] write failed for {section['name']}: {e}")
                try:
                    conn.rollback()
                except Exception:
                    conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Upanishads ingestion complete.")
    print(f"  Texts ingested : {total_texts}")
    print(f"  Total chunks   : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Upanishads (Müller SBE, Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
