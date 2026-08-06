# -*- coding: utf-8 -*-
"""
Mishnah ingestion: Silverstein English translation (CC-BY).

Source: Sefaria-Export GCS bucket (storage.googleapis.com/sefaria-export/)
        Version: "The Mishna with Obadiah Bartenura by Rabbi Shraga Silverstein"
        License: CC-BY (verified via Sefaria-Export metadata 2026-08-06)
        Origin: Rabbi Shraga Silverstein (self-published) → Sefaria (sefaria.org/shraga-silverstein)
        Attribution: Rabbi Shraga Silverstein. Via Sefaria (sefaria.org/shraga-silverstein). CC BY.

Coverage: Seder Zeraim (Berakhot only) · Seder Moed (complete) ·
          Seder Nashim (complete) · Seder Nezikin (complete) ≈ 4 of 6 sedarim
          Script skips tractates where Silverstein version is absent.

Chunking: One mishnah = one chunk (mirrors Hadith approach — each mishnah is
          a self-contained legal/narrative unit). Very short mishnayot
          (< MIN_WORDS) are merged with the following mishnah.
Reference: "Berakhot 1:1", "Shabbat 2:3", etc.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import numpy as np
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one
from embed import embed_documents

GCS_BASE = "https://storage.googleapis.com/sefaria-export"

# Sedarim covered by Silverstein (in GCS order)
COVERED_SEDARIM = [
    "Seder Zeraim",
    "Seder Moed",
    "Seder Nashim",
    "Seder Nezikin",
]

VERSION_TITLE    = "The Mishna with Obadiah Bartenura by Rabbi Shraga Silverstein"
ALLOWED_LICENCES = {"Public Domain", "CC0", "CC-BY", "CC-BY-SA"}

CORPUS_CODE = "mishnah-silverstein"
CORPUS_NAME = "Mishnah (Silverstein with Bartenura)"
TRADITION   = "judaism"
LANGUAGE    = "en"
LICENSE     = "CC-BY"
BASE_URL    = "https://www.sefaria.org/Mishnah"
ATTRIBUTION = "Rabbi Shraga Silverstein. Via Sefaria (sefaria.org/shraga-silverstein). CC BY."

MIN_WORDS   = 20    # mishnayot shorter than this are merged with the next
MAX_RETRIES = 5
RETRY_DELAY = 30


# ── Helpers ────────────────────────────────────────────────────────────────────

_HTML_TAG  = re.compile(r'<[^>]+>')
_WHITESPACE = re.compile(r'\s+')


def _strip_html(text: str) -> str:
    text = _HTML_TAG.sub(' ', text or '')
    return _WHITESPACE.sub(' ', text).strip()


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 4)


def _list_gcs_prefixes(prefix: str) -> list[str]:
    """Return subdirectory names immediately under a GCS prefix."""
    url = f"{GCS_BASE}/?prefix={quote(prefix, safe='/')}&delimiter=/"
    try:
        resp = httpx.get(url, timeout=30.0)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] GCS listing failed for {prefix}: {e}")
        return []
    raw = re.findall(r'<Prefix>(.*?)</Prefix>', resp.text)
    names = []
    for p in raw:
        if p.endswith('/') and p != prefix:
            name = p[len(prefix):].rstrip('/')
            if name:
                names.append(name)
    return sorted(names)


def _download_version(seder: str, tractate: str) -> dict | None:
    """Download the Silverstein version for a tractate. Returns None on 404."""
    filename = f"{VERSION_TITLE}.json"
    url = (f"{GCS_BASE}/json/Mishnah/{quote(seder, safe='')}"
           f"/{quote(tractate, safe='')}/English/{quote(filename, safe='')}")
    try:
        resp = httpx.get(url, timeout=60.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [WARN] Download failed ({url[:80]}): {e}")
        return None


def _tractate_name(raw: str) -> str:
    """Strip leading 'Mishnah ' prefix for display (e.g. 'Mishnah Berakhot' → 'Berakhot')."""
    return raw.removeprefix('Mishnah ').strip()


def _make_chunks(tractate_name: str, text_array: list) -> list[dict]:
    """
    Convert a Sefaria Mishnah text array (chapters → mishnayot) into chunk dicts.
    text_array[ch_idx][m_idx] = mishnah text string.
    One mishnah = one chunk; very short mishnayot are merged with the next.
    """
    chunks: list[dict] = []
    pending_text  = ''
    pending_ch    = 1
    pending_m_num = 1

    def _flush(text: str, ch: int, m_start: int, m_end: int) -> None:
        if not text:
            return
        ref = f"{tractate_name} {ch}:{m_start}" if m_start == m_end else f"{tractate_name} {ch}:{m_start}-{m_end}"
        chunks.append({
            'text':        text,
            'reference':   ref,
            'chapter':     str(ch),
            'section':     str(m_start),
            'word_count':  len(text.split()),
            'token_count': _approx_tokens(text),
        })

    for ch_idx, mishnayot in enumerate(text_array):
        ch_num = ch_idx + 1
        if not mishnayot:
            continue
        for m_idx, raw_text in enumerate(mishnayot):
            m_num = m_idx + 1
            text  = _strip_html(raw_text)
            if not text:
                continue

            if len(text.split()) < MIN_WORDS:
                # Accumulate short mishnah
                if pending_text:
                    pending_text = pending_text + ' ' + text
                else:
                    pending_text  = text
                    pending_ch    = ch_num
                    pending_m_num = m_num
            else:
                # Flush any pending short mishnah together with this one? No —
                # flush pending alone, then emit this one.
                if pending_text:
                    _flush(pending_text, pending_ch, pending_m_num, pending_m_num)
                    pending_text = ''
                pending_text  = text
                pending_ch    = ch_num
                pending_m_num = m_num

    if pending_text:
        _flush(pending_text, pending_ch, pending_m_num, pending_m_num)

    return chunks


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _upsert_corpus(conn) -> int:
    row = execute_one(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (CORPUS_CODE, CORPUS_NAME, TRADITION, LANGUAGE, LICENSE, BASE_URL))
    conn.commit()
    return row['id']


def _upsert_text(conn, corpus_id: int, tractate_raw: str, tractate: str, seder: str) -> int:
    slug = tractate.lower().replace(' ', '-').replace("'", '')
    external_id = f"mishnah-{slug}"
    url = f"https://www.sefaria.org/{quote(tractate_raw.replace(' ', '_'), safe='_')}"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language,
             collection, sub_collection, url)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (corpus_id, external_id)
        DO UPDATE SET title_english = EXCLUDED.title_english
        RETURNING id
    """, (corpus_id, external_id, tractate, TRADITION, LANGUAGE,
          'Mishnah', seder, url))
    conn.commit()
    return row['id']


# ── Main ───────────────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    conn = get_conn()
    corpus_id = _upsert_corpus(conn)
    print(f"Corpus '{CORPUS_CODE}' id={corpus_id}")

    total_chunks    = 0
    total_tractates = 0
    skipped         = 0

    for seder in COVERED_SEDARIM:
        prefix   = f"json/Mishnah/{seder}/"
        tractates = _list_gcs_prefixes(prefix)
        print(f"\n{'='*60}")
        print(f"{seder} — {len(tractates)} tractates in GCS")
        print(f"{'='*60}")

        for tractate_raw in tractates:
            tractate = _tractate_name(tractate_raw)
            print(f"\n  {tractate}", end=' ... ', flush=True)

            data = _download_version(seder, tractate_raw)
            if data is None:
                print("Silverstein version not found — skip")
                skipped += 1
                continue

            lic = data.get('license', '')
            if lic not in ALLOWED_LICENCES:
                print(f"BLOCKED ({lic!r}) — skip")
                skipped += 1
                continue

            text_array = data.get('text') or []
            if not text_array:
                print("empty text — skip")
                skipped += 1
                continue

            text_id = _upsert_text(conn, corpus_id, tractate_raw, tractate, seder)

            if not force:
                existing = execute_one(conn,
                    "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s",
                    (text_id,))
                if existing and existing['n'] > 0:
                    n = existing['n']
                    print(f"already ingested ({n} chunks) — skip")
                    total_chunks    += n
                    total_tractates += 1
                    continue

            if force:
                execute(conn,
                    "DELETE FROM chunk_embeddings WHERE chunk_id IN "
                    "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                execute(conn, "DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
                conn.commit()

            chunks = _make_chunks(tractate, text_array)
            if not chunks:
                print("no chunks produced — skip")
                skipped += 1
                continue

            print(f"{len(chunks)} mishnayot")

            texts = [c['text'] for c in chunks]

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    embeddings = embed_documents(texts)
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        print(f"  [WARN] Voyage attempt {attempt}/{MAX_RETRIES}: {e}. Retry in {RETRY_DELAY}s...")
                        time.sleep(RETRY_DELAY)
                    else:
                        print(f"  [ERROR] Voyage failed after {MAX_RETRIES} attempts: {e}")
                        embeddings = None
                        break

            if embeddings is None:
                continue

            try:
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
                            chunk['chapter'], chunk['section'],
                            chunk['word_count'], chunk['token_count'],
                            None,           # entity_ids
                            LANGUAGE, TRADITION, CORPUS_CODE,
                            'Mishnah', False,   # is_verse=False
                        ))
                        chunk_id = cur.fetchone()['id']
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                            (chunk_id, emb_np),
                        )
                conn.commit()
                total_chunks    += len(chunks)
                total_tractates += 1
                print(f"  ✓ {len(chunks)} chunks committed")
            except Exception as e:
                print(f"  [ERROR] write failed for {tractate}: {e}")
                try:
                    conn.rollback()
                except Exception:
                    conn = get_conn()

    conn.close()
    print(f"\n{'='*60}")
    print(f"Mishnah ingestion complete.")
    print(f"  Tractates ingested : {total_tractates}")
    print(f"  Tractates skipped  : {skipped}")
    print(f"  Total chunks       : {total_chunks:,}")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Mishnah — Silverstein (CC-BY)')
    parser.add_argument('--force', action='store_true', help='Re-embed and overwrite existing chunks')
    args = parser.parse_args()
    run(force=args.force)
