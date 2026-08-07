# -*- coding: utf-8 -*-
"""
Hadith ingestion: Sahih al-Bukhari + Sahih Muslim.

Source : fawazahmed0/hadith-api (Unlicense — public domain)
         https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1/

Language decision (English-only):
  The retrieval eval measured ~64% Chunk R@5 for Arabic cross-lingual retrieval
  vs ~80%+ for monolingual English across the Buddhist corpus. All API queries
  arrive in English; the 15-20 pp gap is commercially disqualifying for Arabic-only.
  Muhsin Khan (Bukhari) and Abdul Hamid Siddiqui (Muslim) are the de facto standard
  English translations used in academic and Islamic scholarship globally.

Chunking: one hadith = one chunk. Hadiths are semantically self-contained units;
splitting or merging would damage retrieval precision.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one, execute_many
from embed import embed_documents

CDN_BASE = "https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1/editions"

COLLECTIONS = [
    {
        "code":     "sahih-bukhari",
        "name":     "Sahih al-Bukhari",
        "edition":  "eng-bukhari",
        "tradition": "islam",
        "language": "en",
        "license":  "Unlicense",
        "base_url": "https://sunnah.com/bukhari",
        "ref_prefix": "Bukhari",
    },
    {
        "code":     "sahih-muslim",
        "name":     "Sahih Muslim",
        "edition":  "eng-muslim",
        "tradition": "islam",
        "language": "en",
        "license":  "Unlicense",
        "base_url": "https://sunnah.com/muslim",
        "ref_prefix": "Muslim",
    },
    {
        "code":     "sunan-abu-dawood",
        "name":     "Sunan Abu Dawood",
        "edition":  "eng-abudawud",
        "tradition": "islam",
        "language": "en",
        "license":  "Unlicense",
        "base_url": "https://sunnah.com/abudawud",
        "ref_prefix": "Abu Dawood",
    },
]

EMBED_BATCH = 500   # hadiths to embed per voyage call batch


def fetch_collection(edition: str) -> dict:
    url = f"{CDN_BASE}/{edition}.min.json"
    print(f"  Downloading {url} ...")
    for attempt in range(3):
        try:
            resp = httpx.get(url, timeout=180.0, follow_redirects=True)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < 2:
                print(f"  Retry {attempt + 1}: {e}")
                time.sleep(5)
            else:
                raise


def book_name(metadata: dict, book_id: str) -> str:
    """Look up a book name from metadata.section, fall back to 'Book N'."""
    names = metadata.get("section", {})
    return names.get(str(book_id), f"Book {book_id}")


def upsert_corpus(conn, col: dict) -> int:
    row = execute_one(conn, """
        INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, (col["code"], col["name"], col["tradition"],
          col["language"], col["license"], col["base_url"]))
    conn.commit()
    return row["id"]


def upsert_text(conn, corpus_id: int, col: dict, sid: str, section_name: str) -> int:
    external_id = f"{col['code']}-s{sid}"
    row = execute_one(conn, """
        INSERT INTO canon_texts
            (corpus_id, external_id, title_english, tradition, language, collection, sub_collection)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (corpus_id, external_id) DO UPDATE SET sub_collection = EXCLUDED.sub_collection
        RETURNING id
    """, (corpus_id, external_id, col["name"], col["tradition"],
          col["language"], col["name"], section_name))
    conn.commit()
    return row["id"]


def run(force: bool = False) -> None:
    conn = get_conn()

    for col in COLLECTIONS:
        print(f"\n{'='*60}")
        print(f"Ingesting {col['name']} ...")
        print(f"{'='*60}")

        corpus_id   = upsert_corpus(conn, col)
        data        = fetch_collection(col["edition"])
        metadata    = data.get("metadata", {})
        hadiths_raw = data.get("hadiths", [])

        print(f"  {len(hadiths_raw):,} hadiths in collection")

        # Group by reference.book (reliable; section_detail ranges are absent in full JSON)
        books: dict[str, dict] = {}
        skipped = 0
        for h in hadiths_raw:
            text = (h.get("text") or "").strip()
            if not text or len(text.split()) < 5:
                skipped += 1
                continue
            bid   = str((h.get("reference") or {}).get("book", "0") or "0")
            bname = book_name(metadata, bid)
            if bid not in books:
                books[bid] = {"name": bname, "hadiths": []}
            books[bid]["hadiths"].append(h)

        total_usable = sum(len(b["hadiths"]) for b in books.values())
        print(f"  {total_usable:,} usable hadiths across {len(books)} books "
              f"({skipped} skipped — empty/trivial)")

        total_books = len(books)
        for book_n, (bid, bk) in enumerate(books.items(), 1):
            text_id = upsert_text(conn, corpus_id, col, bid, bk["name"])
            print(f"  [{book_n:03d}/{total_books}] {bk['name'][:50]} ({len(bk['hadiths'])} hadiths)", flush=True)

            existing = execute_one(conn,
                "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s",
                (text_id,))
            if existing and existing["n"] > 0 and not force:
                continue

            if force:
                execute(conn,
                    "DELETE FROM chunk_embeddings WHERE chunk_id IN "
                    "(SELECT id FROM document_chunks WHERE text_id = %s)", (text_id,))
                execute(conn, "DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
                conn.commit()

            rows = []
            for h in bk["hadiths"]:
                num      = h.get("hadithnumber", 0)
                ref_obj  = h.get("reference") or {}
                ref_book = ref_obj.get("book", "")
                ref_hdth = ref_obj.get("hadith", "")
                reference = (f"{col['ref_prefix']} {ref_book}:{ref_hdth}"
                             if ref_book and ref_hdth else f"{col['ref_prefix']} #{num}")
                text = (h.get("text") or "").strip()
                rows.append((text_id, num, text, reference,
                             bk["name"], None, len(text.split()), max(1, len(text) // 4)))

            # Bulk insert — execute_values with fetch=True returns rows directly
            cur = conn.cursor()
            returned = psycopg2.extras.execute_values(cur, """
                INSERT INTO document_chunks
                    (text_id, chunk_index, chunk_text, reference,
                     chapter, section, word_count, token_count)
                VALUES %s RETURNING id
            """, rows, fetch=True)
            chunk_ids = [r["id"] for r in returned]   # RealDictCursor → dict rows
            conn.commit()

            # Embed and store — convert lists to numpy for pgvector adapter
            texts      = [r[2] for r in rows]
            embeddings = embed_documents(texts)
            emb_np     = [np.array(e, dtype=np.float32) for e in embeddings]
            psycopg2.extras.execute_values(cur, """
                INSERT INTO chunk_embeddings (chunk_id, embedding)
                VALUES %s ON CONFLICT (chunk_id) DO NOTHING
            """, list(zip(chunk_ids, emb_np)))
            conn.commit()

        print(f"\n  {col['name']} done.")

    conn.close()
    print("\nHadith ingestion complete.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-embed and overwrite existing chunks")
    args = parser.parse_args()
    run(force=args.force)
