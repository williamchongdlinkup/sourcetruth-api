# -*- coding: utf-8 -*-
"""
Bible v10 migration — replaces chapter-level chunks with 10-verse chunks.

For each Bible book in scope:
  1. Re-fetches verse-level data from getbible.net (ASV source)
  2. Builds v10 chunks (~10 verses, ~249 words mean)
  3. Deletes existing document_chunks rows (chunk_embeddings cascade-delete)
  4. Inserts new v10 chunk rows + embeddings via Voyage AI

Usage:
  # Preview only — no DB writes
  python ingestion/bible_migrate_v10.py --dry-run

  # Migrate one translation as a canary
  python ingestion/bible_migrate_v10.py --translation asv

  # Migrate all translations
  python ingestion/bible_migrate_v10.py --all

  # Specific book range (book numbers, 1-based, inclusive)
  python ingestion/bible_migrate_v10.py --translation asv --books 23-23   # Isaiah only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import httpx

EMBED_MODEL = "voyage-multilingual-2"
GETBIBLE_BASE = "https://api.getbible.net/v2"
BATCH_SIZE  = 25
MAX_RETRIES = 3

TRANSLATIONS = [
    {"abbrev": "asv",  "code": "bible-asv"},
    {"abbrev": "kjv",  "code": "bible-kjv"},
    {"abbrev": "web",  "code": "bible-web"},
    {"abbrev": "ylt",  "code": "bible-ylt"},
]

ALL_BOOKS = [
    (1,"Genesis"),(2,"Exodus"),(3,"Leviticus"),(4,"Numbers"),(5,"Deuteronomy"),
    (6,"Joshua"),(7,"Judges"),(8,"Ruth"),(9,"1 Samuel"),(10,"2 Samuel"),
    (11,"1 Kings"),(12,"2 Kings"),(13,"1 Chronicles"),(14,"2 Chronicles"),
    (15,"Ezra"),(16,"Nehemiah"),(17,"Esther"),(18,"Job"),(19,"Psalms"),
    (20,"Proverbs"),(21,"Ecclesiastes"),(22,"Song of Solomon"),(23,"Isaiah"),
    (24,"Jeremiah"),(25,"Lamentations"),(26,"Ezekiel"),(27,"Daniel"),
    (28,"Hosea"),(29,"Joel"),(30,"Amos"),(31,"Obadiah"),(32,"Jonah"),
    (33,"Micah"),(34,"Nahum"),(35,"Habakkuk"),(36,"Zephaniah"),(37,"Haggai"),
    (38,"Zechariah"),(39,"Malachi"),(40,"Matthew"),(41,"Mark"),(42,"Luke"),
    (43,"John"),(44,"Acts"),(45,"Romans"),(46,"1 Corinthians"),
    (47,"2 Corinthians"),(48,"Galatians"),(49,"Ephesians"),(50,"Philippians"),
    (51,"Colossians"),(52,"1 Thessalonians"),(53,"2 Thessalonians"),
    (54,"1 Timothy"),(55,"2 Timothy"),(56,"Titus"),(57,"Philemon"),
    (58,"Hebrews"),(59,"James"),(60,"1 Peter"),(61,"2 Peter"),
    (62,"1 John"),(63,"2 John"),(64,"3 John"),(65,"Jude"),(66,"Revelation"),
]


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _fetch_json(url: str) -> dict | None:
    for attempt in range(MAX_RETRIES):
        try:
            r = httpx.get(url, timeout=30.0, follow_redirects=True)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
            else:
                print(f"  [ERROR] {url}: {e}")
    return None


# ── v10 chunking ──────────────────────────────────────────────────────────────

def chunk_v10(book_data: dict, book_name: str) -> list[dict]:
    """Return list of chunk dicts: text, reference, chapter (TEXT), word_count."""
    verses = []
    for chap in sorted(book_data.get("chapters", []), key=lambda c: c.get("chapter", 0)):
        ch_num = chap.get("chapter", 0)
        for v in sorted(chap.get("verses", []), key=lambda v: v.get("verse", 0)):
            text = v.get("text", "").strip()
            if text:
                verses.append({"chapter": ch_num, "verse": v.get("verse", 0), "text": text})

    chunks = []
    for i in range(0, len(verses), 10):
        window = verses[i:i + 10]
        if not window:
            continue
        ch_first = window[0]["chapter"]
        ch_last  = window[-1]["chapter"]
        v_first  = window[0]["verse"]
        v_last   = window[-1]["verse"]
        text     = " ".join(v["text"] for v in window)

        if ch_first == ch_last:
            ref = (f"{book_name} {ch_first}" if v_first == 1
                   else f"{book_name} {ch_first}:{v_first}-{v_last}")
        else:
            ref = f"{book_name} {ch_first}:{v_first}-{ch_last}:{v_last}"

        chunks.append({
            "text":       text,
            "reference":  ref,
            "chapter":    str(ch_first),  # TEXT column in schema
            "word_count": len(text.split()),
        })
    return chunks


# ── Embed ─────────────────────────────────────────────────────────────────────

def get_voyage_client():
    import voyageai
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise SystemExit("VOYAGE_API_KEY not set in environment.")
    return voyageai.Client(api_key=key)


def embed_batch(client, texts: list[str]) -> list[list[float]]:
    all_embs = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        for attempt in range(MAX_RETRIES):
            try:
                res = client.embed(batch, model=EMBED_MODEL, input_type="document")
                all_embs.extend(res.embeddings)
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(10)
                else:
                    raise
        time.sleep(0.1)
    return all_embs


# ── Per-book migration ────────────────────────────────────────────────────────

def migrate_book(
    conn,
    voyage,
    text_id: int,
    book_name: str,
    book_nr: int,
    abbrev: str,
    dry_run: bool = False,
) -> dict:
    """Migrate one book to v10 chunks. Returns stats dict."""
    book_data = _fetch_json(f"{GETBIBLE_BASE}/{abbrev}/{book_nr}.json")
    if book_data is None:
        return {"book": book_name, "status": "fetch_failed", "old": 0, "new": 0}

    chunks = chunk_v10(book_data, book_name)
    if not chunks:
        return {"book": book_name, "status": "no_chunks", "old": 0, "new": 0}

    # Count existing chunks
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
        old_count = cur.fetchone()["n"]

    if dry_run:
        return {"book": book_name, "status": "dry_run", "old": old_count, "new": len(chunks)}

    # Embed new chunks
    try:
        embeddings = embed_batch(voyage, [c["text"] for c in chunks])
    except Exception as e:
        return {"book": book_name, "status": f"embed_failed: {e}", "old": old_count, "new": 0}

    # Replace chunks in a transaction
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))

        for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            emb_np = np.array(emb, dtype=np.float32)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO document_chunks
                        (text_id, chunk_index, chunk_text, reference, chapter,
                         section, word_count, entity_ids, language, tradition,
                         corpus_code, collection, is_verse)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'en','christianity',%s,'Bible',TRUE)
                    RETURNING id
                """, (
                    text_id, idx, chunk["text"], chunk["reference"],
                    chunk["chapter"], None, chunk["word_count"], None,
                    abbrev,
                ))
                chunk_id = cur.fetchone()["id"]
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                    (chunk_id, emb_np),
                )

        conn.commit()
        return {"book": book_name, "status": "ok", "old": old_count, "new": len(chunks)}

    except Exception as e:
        conn.rollback()
        return {"book": book_name, "status": f"write_failed: {e}", "old": old_count, "new": 0}


# ── Main ──────────────────────────────────────────────────────────────────────

def dry_run_no_db(targets: list[dict], book_start: int, book_end: int) -> None:
    """Dry run without DB — fetches from getbible.net and shows projected chunk counts."""
    print("  (No DATABASE_URL set — showing projected counts only, no existing-chunk comparison)\n")
    grand_new = 0
    for trans in targets:
        abbrev = trans["abbrev"]
        code   = trans["code"]
        print(f"\n-- {code} ({abbrev}) --")
        trans_new = 0
        for book_nr, book_name in ALL_BOOKS:
            if not (book_start <= book_nr <= book_end):
                continue
            book_data = _fetch_json(f"{GETBIBLE_BASE}/{abbrev}/{book_nr}.json")
            if book_data is None:
                print(f"  [SKIP] {book_name}")
                continue
            chunks = chunk_v10(book_data, book_name)
            print(f"  {book_name:<22} → {len(chunks):>4} v10 chunks")
            trans_new += len(chunks)
            time.sleep(0.2)
        print(f"  Subtotal: {trans_new} chunks")
        grand_new += trans_new
    print(f"\n{'='*65}")
    print(f"Projected total: {grand_new:,} v10 chunks across {len(targets)} translation(s)")
    print(f"(current production: ~1,153 chapter chunks per translation)")
    print(f"{'='*65}\n")


def main():
    parser = argparse.ArgumentParser(description="Migrate Bible chunks to v10 (10-verse) format")
    parser.add_argument("--dry-run",     action="store_true", help="Preview only, no DB writes")
    parser.add_argument("--translation", help="Single translation abbrev: asv | kjv | web | ylt")
    parser.add_argument("--all",         action="store_true", help="Migrate all 4 translations")
    parser.add_argument("--books",       help="Book number range e.g. 1-5 or 23-23 (1-based, inclusive)")
    parser.add_argument("--db-url",      help="Override DATABASE_URL (or set it in .env)")
    args = parser.parse_args()

    if not args.dry_run and not args.translation and not args.all:
        parser.print_help()
        print("\n[Error] Specify --dry-run, --translation <abbrev>, or --all")
        sys.exit(1)

    if args.all:
        targets = TRANSLATIONS
    elif args.translation:
        targets = [t for t in TRANSLATIONS if t["abbrev"] == args.translation]
        if not targets:
            print(f"[Error] Unknown translation '{args.translation}'. Choose: asv, kjv, web, ylt")
            sys.exit(1)
    else:
        targets = TRANSLATIONS  # dry-run shows all

    book_start, book_end = 1, 66
    if args.books:
        parts = args.books.split("-")
        book_start = int(parts[0])
        book_end   = int(parts[1]) if len(parts) > 1 else book_start

    mode = "DRY RUN" if args.dry_run else "LIVE MIGRATION"
    print(f"\n{'='*65}")
    print(f"Bible v10 Migration — {mode}")
    print(f"Translations: {[t['abbrev'] for t in targets]}")
    print(f"Books: {book_start}–{book_end}")
    print(f"{'='*65}\n")

    db_url = args.db_url or os.environ.get("DATABASE_URL")

    # Dry run without DB: just show projected counts from getbible.net
    if args.dry_run and not db_url:
        dry_run_no_db(targets, book_start, book_end)
        return

    if not db_url:
        raise SystemExit(
            "DATABASE_URL not set. Add it to .env or pass --db-url postgresql://..."
        )

    from db import get_conn
    conn   = get_conn(db_url)
    voyage = get_voyage_client() if not args.dry_run else None

    grand_old = grand_new = 0

    for trans in targets:
        abbrev = trans["abbrev"]
        code   = trans["code"]
        print(f"\n-- {code} ({abbrev}) --")

        with conn.cursor() as cur:
            cur.execute("""
                SELECT ct.id, ct.title_english, ct.external_id
                FROM canon_texts ct
                JOIN source_corpora sc ON sc.id = ct.corpus_id
                WHERE sc.code = %s
                ORDER BY ct.external_id
            """, (code,))
            books = cur.fetchall()

        if not books:
            print(f"  No books found for corpus '{code}' — skipping")
            continue

        for book in books:
            try:
                book_nr = int(book["external_id"].split("-")[-1])
            except (ValueError, IndexError):
                print(f"  [SKIP] Cannot parse book_nr from {book['external_id']}")
                continue

            if not (book_start <= book_nr <= book_end):
                continue

            result = migrate_book(
                conn, voyage,
                text_id=book["id"],
                book_name=book["title_english"],
                book_nr=book_nr,
                abbrev=abbrev,
                dry_run=args.dry_run,
            )

            status_sym = "OK" if result["status"] in ("ok", "dry_run") else "!!"
            print(f"  {status_sym} {result['book']:<22} {result['old']:>5} → {result['new']:>5} chunks"
                  + (f"  [{result['status']}]" if result["status"] not in ("ok", "dry_run") else ""))
            grand_old += result["old"]
            grand_new += result["new"]
            time.sleep(0.2)

    print(f"\n{'='*65}")
    print(f"Total: {grand_old:,} old chunks → {grand_new:,} new chunks")
    if args.dry_run:
        print("(No changes made — dry run only)")
    print(f"{'='*65}\n")

    conn.close()


if __name__ == "__main__":
    main()
