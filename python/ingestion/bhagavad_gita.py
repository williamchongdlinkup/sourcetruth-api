# -*- coding: utf-8 -*-
"""
Bhagavad Gita ingestion: Edwin Arnold's "The Song Celestial" (1885, Public Domain).

Source : Project Gutenberg #2388
         Edwin Arnold, 1885 — died 1904; published 1885 (> 120 years ago). Public Domain.

Corpus : 18 Adhyayas (chapters/books). Chunked by stanza groups (~200-300 words).
Reference: "Bhagavad Gita 1" (adhyaya) or "Bhagavad Gita 1:1-8" (stanza range)
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

GUTENBERG_URL = "https://www.gutenberg.org/cache/epub/2388/pg2388.txt"

CORPUS_CODE = "bhagavad-gita"
CORPUS_NAME = "Bhagavad Gita (Arnold, 1885)"
TRADITION   = "hinduism"
LANGUAGE    = "en"
LICENSE     = "Public Domain"
BASE_URL    = "https://www.gutenberg.org/ebooks/2388"

TARGET_WORDS = 250   # target words per chunk
MIN_WORDS    = 80    # merge stanzas below this threshold
MAX_RETRIES  = 5
RETRY_DELAY  = 30

# The 18 Adhyaya names from Arnold's text
ADHYAYA_NAMES = [
    "Arjuna-Vishada (Arjuna's Despondency)",
    "Sankhya-Yoga (The Book of Doctrines)",
    "Karma-Yoga (The Book of Virtue in Work)",
    "Jnana-Karma-Sannyasa-Yoga (The Book of Religion by Knowledge)",
    "Karma-Sannyasa-Yoga (The Book of Religion by Renouncing Fruit of Works)",
    "Dhyana-Yoga (The Book of Religion by Self-Restraint)",
    "Jnana-Vijnana-Yoga (The Book of Religion by Discernment)",
    "Akshara-Brahma-Yoga (The Book of Religion by Devotion to the One Supreme God)",
    "Raja-Vidya-Raja-Guhya-Yoga (The Book of the Kingly Knowledge and the Kingly Mystery)",
    "Vibhuti-Yoga (The Book of Religion by Heavenly Perfections)",
    "Vishvarupa-Darsana-Yoga (The Book of the Manifesting of the One and Manifold)",
    "Bhakti-Yoga (The Book of the Religion of Faith)",
    "Kshetra-Kshetrajna-Vibhaga-Yoga (The Book of Religion by Separation of Matter and Spirit)",
    "Gunatraya-Vibhaga-Yoga (The Book of Religion by Separation from the Qualities)",
    "Purushottama-Yoga (The Book of the Supreme Spirit)",
    "Daivasura-Sampad-Vibhaga-Yoga (The Book of the Two Paths)",
    "Shraddha-Traya-Vibhaga-Yoga (The Book of the Threefold Faith)",
    "Moksha-Sannyasa-Yoga (The Book of Religion by Deliverance and Renunciation)",
]

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
    return _WHITESPACE.sub(' ', s).strip()


ROMAN_TO_INT = {
    'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6,'VII':7,'VIII':8,'IX':9,'X':10,
    'XI':11,'XII':12,'XIII':13,'XIV':14,'XV':15,'XVI':16,'XVII':17,'XVIII':18,
}


def _parse_adhyayas(text: str) -> list[dict]:
    """
    Split text into 18 adhyayas. Arnold Gutenberg #2388 uses 'CHAPTER I' ... 'CHAPTER XVIII'.
    Falls back to 'BOOK THE FIRST' etc. if Chapter format not found.
    """
    # Primary: CHAPTER I, CHAPTER II, ... CHAPTER XVIII (Arnold Gutenberg format)
    roman_re = r'(?:X{0,2}(?:IX|IV|VI{0,3}|I{1,3})|X{1,3})'
    chap_pattern = re.compile(
        r'^\s*(CHAPTER\s+(' + roman_re + r'))\s*\r?$',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(chap_pattern.finditer(text))

    if not matches:
        # Fallback: BOOK THE FIRST ... BOOK THE EIGHTEENTH
        ordinals = [
            "FIRST","SECOND","THIRD","FOURTH","FIFTH","SIXTH","SEVENTH","EIGHTH","NINTH","TENTH",
            "ELEVENTH","TWELFTH","THIRTEENTH","FOURTEENTH","FIFTEENTH","SIXTEENTH","SEVENTEENTH","EIGHTEENTH"
        ]
        pattern2 = re.compile(r'BOOK\s+THE\s+(' + '|'.join(ordinals) + r')\b', re.IGNORECASE)
        matches = list(pattern2.finditer(text))
        ordinal_to_num = {o: i+1 for i, o in enumerate(ordinals)}
        if matches:
            adhyayas = []
            for i, match in enumerate(matches):
                start = match.start()
                end   = matches[i+1].start() if i+1 < len(matches) else len(text)
                num   = ordinal_to_num.get(match.group(1).upper(), i+1)
                title = ADHYAYA_NAMES[num-1] if num <= 18 else f"Adhyaya {num}"
                adhyayas.append({'num': num, 'title': title, 'body': text[start:end].strip()})
            return adhyayas

    if not matches:
        return _parse_fallback(text)

    adhyayas = []
    for i, match in enumerate(matches):
        start    = match.start()
        end      = matches[i+1].start() if i+1 < len(matches) else len(text)
        roman    = match.group(2).upper().strip()
        num      = ROMAN_TO_INT.get(roman, i + 1)
        title    = ADHYAYA_NAMES[num-1] if num <= 18 else f"Adhyaya {num}"
        body     = text[start:end].strip()
        adhyayas.append({'num': num, 'title': title, 'body': body})

    return adhyayas


def _parse_fallback(text: str) -> list[dict]:
    """If no chapter headers found, treat entire text as one adhyaya (preserving newlines for chunking)."""
    return [{'num': 1, 'title': 'Bhagavad Gita', 'body': text}]


def _chunk_adhyaya(adhyaya: dict) -> list[dict]:
    """
    Split an adhyaya body into stanza-group chunks targeting TARGET_WORDS per chunk.
    Stanzas are separated by blank lines or character attribution lines (KRISHNA./ARJUNA.).
    """
    body = adhyaya['body']
    num  = adhyaya['num']
    title = adhyaya['title']

    # Split into paragraph/stanza blocks
    blocks = re.split(r'\n{2,}', body)
    blocks = [_clean(b) for b in blocks if b.strip()]

    # Filter out very short attribution/header lines (< 5 words) as standalone blocks
    stanzas = []
    for b in blocks:
        words = b.split()
        if len(words) < 4:
            # Prepend attribution to next stanza if possible
            if stanzas:
                stanzas[-1] = stanzas[-1] + ' ' + b
            continue
        stanzas.append(b)

    if not stanzas:
        return []

    chunks = []
    current_words: list[str] = []
    stanza_start = 1
    stanza_idx   = 0

    for si, stanza in enumerate(stanzas):
        words = stanza.split()
        if current_words and len(current_words) + len(words) > TARGET_WORDS and len(current_words) >= MIN_WORDS:
            # Flush current group
            text = ' '.join(current_words)
            ref = f"Bhagavad Gita {num}:{stanza_start}-{si}"
            chunks.append({
                'text':      text,
                'reference': ref,
                'chapter':   str(num),
                'title':     title,
                'word_count':  len(current_words),
                'token_count': max(1, len(' '.join(current_words).encode()) // 4),
            })
            current_words = words
            stanza_start  = si + 1
        else:
            current_words.extend(words)

    if current_words:
        text = ' '.join(current_words)
        ref = f"Bhagavad Gita {num}:{stanza_start}-{len(stanzas)}"
        chunks.append({
            'text':      text,
            'reference': ref,
            'chapter':   str(num),
            'title':     title,
            'word_count':  len(current_words),
            'token_count': max(1, len(text.encode()) // 4),
        })

    # If only one chunk produced, use simpler reference
    if len(chunks) == 1:
        chunks[0]['reference'] = f"Bhagavad Gita {num}: {title}"

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


def _upsert_text(conn, corpus_id: int, adhyaya_num: int, title: str) -> int:
    external_id = f"bg-adhyaya-{adhyaya_num}"
    url = f"https://www.gutenberg.org/ebooks/2388"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, url)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, f"Adhyaya {adhyaya_num}: {title}", TRADITION, LANGUAGE,
          "Bhagavad Gita", url))
    conn.commit()
    return row['id']


def run(force: bool = False) -> None:
    print(f"Downloading Bhagavad Gita (Arnold, Gutenberg #2388) ...")
    for attempt in range(3):
        try:
            resp = httpx.get(GUTENBERG_URL, timeout=120.0, follow_redirects=True)
            resp.raise_for_status()
            raw = resp.text
            break
        except Exception as e:
            if attempt < 2:
                print(f"  Retry {attempt+1}: {e}")
                time.sleep(5)
            else:
                raise

    text = _strip_gutenberg(raw)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    adhyayas = _parse_adhyayas(text)
    print(f"  Parsed {len(adhyayas)} adhyayas.")

    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks = 0
    total_texts  = 0

    for adh in adhyayas:
        print(f"\n  Adhyaya {adh['num']}: {adh['title'][:50]}", end=' ... ', flush=True)

        text_id = _upsert_text(conn, corpus_id, adh['num'], adh['title'])

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

        chunks = _chunk_adhyaya(adh)
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
                        "Bhagavad Gita", True,
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
            print(f"  [ERROR] write failed for adhyaya {adh['num']}: {e}")
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Bhagavad Gita ingestion complete.")
    print(f"  Adhyayas ingested : {total_texts}")
    print(f"  Total chunks      : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Bhagavad Gita (Arnold, Public Domain)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
