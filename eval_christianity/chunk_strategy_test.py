# -*- coding: utf-8 -*-
"""
Chunking strategy evaluation for Bible corpora — fully in-memory, no DB required.

Tests 5 chunking strategies against Isaiah, Job, and Matthew using:
  1. Theological probe queries  — known failures (Isaiah 45:7, Job 1-2)
  2. Synthetic queries          — existing eval suite (59 queries from queries.jsonl)

Strategies:
  chapter   — current production baseline (same as bible_multi.py)
  v5        — fixed 5-verse windows
  v10       — fixed 10-verse windows
  v15       — fixed 15-verse windows
  sw10_5    — sliding window (10 verses wide, step 5) ~50% overlap

Metrics:
  Theological: Hit@1, Hit@3, Hit@5 (any chunk from target chapter in top-K)
  Synthetic:   ChRecall@1, ChRecall@3, ChRecall@5, MRR (chapter-level)
  Stats:       n_chunks, avg_words, p25/p50/p75 word count

Usage:
  Set VOYAGE_API_KEY in .env or environment, then:
  python eval_christianity/chunk_strategy_test.py

Output:
  eval_christianity/chunk_strategy_results.json
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

HERE     = Path(__file__).resolve().parent
REPO     = HERE.parent
EMBED_MODEL = "voyage-multilingual-2"
MAX_RETRIES = 3

# ── Books to test ─────────────────────────────────────────────────────────────
# ASV: book numbers match getbible.net numbering (1=Genesis, 23=Isaiah, 18=Job, 40=Matthew)
BOOKS = [
    {"name": "Isaiah",  "nr": 23, "abbrev": "asv"},
    {"name": "Job",     "nr": 18, "abbrev": "asv"},
    {"name": "Matthew", "nr": 40, "abbrev": "asv"},
]

# ── Theological probe queries with ground-truth chapter references ────────────
THEOLOGICAL_QUERIES = [
    # Isaiah 45 probes — target: any chunk containing Isaiah 45 verses
    {
        "query": "Does evil come from God?",
        "target_book": "Isaiah", "target_chapter": 45,
        "tag": "isa45_natural",
    },
    {
        "query": "I form the light and create darkness I make peace and create evil",
        "target_book": "Isaiah", "target_chapter": 45,
        "tag": "isa45_verbatim",
    },
    {
        "query": "God creates calamity and disaster Lord sovereign over darkness",
        "target_book": "Isaiah", "target_chapter": 45,
        "tag": "isa45_paraphrase",
    },
    {
        "query": "Is the Lord the author of evil? Scripture says God makes peace and creates evil",
        "target_book": "Isaiah", "target_chapter": 45,
        "tag": "isa45_theological",
    },
    # Job 1-2 probes — target: any chunk from Job 1 or Job 2
    {
        "query": "God permits Satan to afflict a righteous man with suffering",
        "target_book": "Job", "target_chapter": 1,
        "tag": "job1_natural",
    },
    {
        "query": "Why does God allow evil to happen to an innocent righteous person?",
        "target_book": "Job", "target_chapter": 1,
        "tag": "job1_theodicy",
    },
    {
        "query": "Satan appears before God and is given permission to test Job",
        "target_book": "Job", "target_chapter": 2,
        "tag": "job2_natural",
    },
    {
        "query": "Innocent man loses children and property through divine permission for suffering",
        "target_book": "Job", "target_chapter": 1,
        "tag": "job1_narrative",
    },
]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Verse:
    book: str
    chapter: int
    verse: int
    text: str


@dataclass
class Chunk:
    strategy: str
    verses: list[Verse]
    text: str
    reference: str          # e.g. "Isaiah 45" or "Isaiah 45:1-5"
    book: str
    chapter_range: tuple[int, int]   # (first_chapter, last_chapter) inclusive
    word_count: int = 0

    def __post_init__(self):
        self.word_count = len(self.text.split())

    def contains_chapter(self, book: str, chapter: int) -> bool:
        return (self.book == book and
                self.chapter_range[0] <= chapter <= self.chapter_range[1])


# ── Fetch from getbible.net ───────────────────────────────────────────────────

def fetch_book(abbrev: str, book_nr: int) -> dict | None:
    url = f"https://api.getbible.net/v2/{abbrev}/{book_nr}.json"
    for attempt in range(MAX_RETRIES):
        try:
            r = httpx.get(url, timeout=30.0, follow_redirects=True)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  [retry {attempt+1}] {e}")
                time.sleep(3)
            else:
                print(f"  [ERROR] fetch failed: {url} — {e}")
    return None


def parse_verses(book_data: dict, book_name: str) -> list[Verse]:
    verses = []
    for chap in sorted(book_data.get("chapters", []), key=lambda c: c.get("chapter", 0)):
        ch_num = chap.get("chapter", 0)
        for v in sorted(chap.get("verses", []), key=lambda v: v.get("verse", 0)):
            text = v.get("text", "").strip()
            if text:
                verses.append(Verse(book=book_name, chapter=ch_num,
                                    verse=v.get("verse", 0), text=text))
    return verses


# ── Chunking strategies ───────────────────────────────────────────────────────

def _make_chunk(strategy: str, verses: list[Verse]) -> Chunk:
    text = " ".join(v.text for v in verses)
    book = verses[0].book
    ch_first, ch_last = verses[0].chapter, verses[-1].chapter
    if ch_first == ch_last:
        if verses[0].verse == 1 and verses[-1].verse == verses[-1].verse:
            ref = f"{book} {ch_first}"
        else:
            ref = f"{book} {ch_first}:{verses[0].verse}-{verses[-1].verse}"
    else:
        ref = f"{book} {ch_first}:{verses[0].verse}-{ch_last}:{verses[-1].verse}"
    return Chunk(strategy=strategy, verses=verses, text=text, reference=ref,
                 book=book, chapter_range=(ch_first, ch_last))


def chunk_chapter(verses: list[Verse], book_name: str,
                  min_words: int = 80, target_words: int = 400) -> list[Chunk]:
    """Current production strategy: chapter-level, merge short chapters."""
    by_chapter: dict[int, list[Verse]] = {}
    for v in verses:
        by_chapter.setdefault(v.chapter, []).append(v)

    chapters = sorted(by_chapter.items())
    chunks, buf = [], []

    def flush():
        if buf:
            chunks.append(_make_chunk("chapter", buf))

    for ch_num, ch_verses in chapters:
        ch_words = sum(len(v.text.split()) for v in ch_verses)
        cur_words = sum(len(v.text.split()) for v in buf)
        if buf and cur_words + ch_words > target_words and cur_words >= min_words:
            flush()
            buf = list(ch_verses)
        else:
            buf.extend(ch_verses)
    flush()
    return chunks


def chunk_fixed_verses(verses: list[Verse], n: int, strategy_name: str) -> list[Chunk]:
    """Fixed-size verse windows (no overlap)."""
    chunks = []
    for i in range(0, len(verses), n):
        window = verses[i:i + n]
        if window:
            chunks.append(_make_chunk(strategy_name, window))
    return chunks


def chunk_sliding_window(verses: list[Verse], window: int, step: int,
                         strategy_name: str) -> list[Chunk]:
    """Sliding window with overlap. Step < window gives overlapping chunks."""
    chunks, seen = [], set()
    for i in range(0, len(verses), step):
        window_verses = verses[i:i + window]
        if not window_verses:
            break
        key = (window_verses[0].chapter, window_verses[0].verse,
               window_verses[-1].chapter, window_verses[-1].verse)
        if key not in seen:
            seen.add(key)
            chunks.append(_make_chunk(strategy_name, window_verses))
    return chunks


STRATEGIES = {
    "chapter":  lambda verses, book: chunk_chapter(verses, book),
    "v5":       lambda verses, book: chunk_fixed_verses(verses, 5,  "v5"),
    "v10":      lambda verses, book: chunk_fixed_verses(verses, 10, "v10"),
    "v15":      lambda verses, book: chunk_fixed_verses(verses, 15, "v15"),
    "sw10_5":   lambda verses, book: chunk_sliding_window(verses, 10, 5, "sw10_5"),
}


# ── Embeddings ────────────────────────────────────────────────────────────────

def get_voyage_client():
    import voyageai
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise SystemExit("VOYAGE_API_KEY not set. Add it to .env or environment.")
    return voyageai.Client(api_key=key)


def embed_batch(client, texts: list[str], input_type: str = "document",
                batch_size: int = 25) -> list[list[float]]:
    all_embs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(MAX_RETRIES):
            try:
                res = client.embed(batch, model=EMBED_MODEL, input_type=input_type)
                all_embs.extend(res.embeddings)
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    print(f"  [retry {attempt+1}] embed error: {e}")
                    time.sleep(10)
                else:
                    raise
        time.sleep(0.1)   # gentle rate limit
    return all_embs


def normalise(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    return mat / np.where(norms > 0, norms, 1.0)


# ── Evaluation helpers ────────────────────────────────────────────────────────

def cosine_rank(query_vec: np.ndarray, chunk_mat: np.ndarray) -> np.ndarray:
    sims = chunk_mat @ query_vec
    return np.argsort(-sims)


def chapter_hit(ranked_chunks: list[Chunk], target_book: str,
                target_chapter: int, k: int) -> int:
    for chunk in ranked_chunks[:k]:
        if chunk.contains_chapter(target_book, target_chapter):
            return 1
    return 0


def chapter_rr(ranked_chunks: list[Chunk], target_book: str,
               target_chapter: int) -> float:
    for rank, chunk in enumerate(ranked_chunks, 1):
        if chunk.contains_chapter(target_book, target_chapter):
            return 1.0 / rank
    return 0.0


def chapter_ndcg(ranked_chunks: list[Chunk], target_book: str,
                 target_chapter: int, k: int = 10) -> float:
    return sum(
        1.0 / math.log2(rank + 2)
        for rank, chunk in enumerate(ranked_chunks[:k])
        if chunk.contains_chapter(target_book, target_chapter)
    )


def word_stats(chunks: list[Chunk]) -> dict:
    words = [c.word_count for c in chunks]
    words.sort()
    n = len(words)
    return {
        "count":   n,
        "mean":    round(sum(words) / n, 1) if n else 0,
        "p25":     words[n // 4] if n else 0,
        "median":  words[n // 2] if n else 0,
        "p75":     words[3 * n // 4] if n else 0,
        "max":     max(words) if words else 0,
    }


# ── Load synthetic queries ────────────────────────────────────────────────────

def load_synthetic_queries() -> list[dict]:
    path = HERE / "queries.jsonl"
    if not path.exists():
        print("  [WARN] queries.jsonl not found; skipping synthetic track.")
        return []
    raw = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Keep synthetic queries for our three test books only
    target_books = {"Isaiah", "Job", "Matthew"}
    out = []
    for q in raw:
        if not q.get("query_type", "").startswith("synthetic"):
            continue
        ref = q.get("reference", "")
        book = ref.split()[0] if ref else ""
        if book in target_books:
            try:
                ch = int(ref.split()[1].split(":")[0])
            except (IndexError, ValueError):
                continue
            out.append({
                "query":          q["query"],
                "target_book":    book,
                "target_chapter": ch,
                "reference":      ref,
                "tag":            f"syn_{book.lower()}_ch{ch}",
            })
    print(f"  Loaded {len(out)} synthetic queries (Isaiah/Job/Matthew)")
    return out


# ── Main evaluation ───────────────────────────────────────────────────────────

def evaluate_strategy(
    strategy_name: str,
    chunks: list[Chunk],
    chunk_mat: np.ndarray,
    query_vecs: np.ndarray,
    queries: list[dict],
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict:
    """Run all queries against a strategy's chunk matrix, return metric dict."""
    results: dict[str, Any] = {
        "strategy":   strategy_name,
        "chunk_stats": word_stats(chunks),
        "per_query":  [],
    }

    agg: dict[str, list] = {f"hit@{k}": [] for k in ks}
    agg["mrr"] = []
    agg["ndcg10"] = []

    for qi, q in enumerate(queries):
        qv   = query_vecs[qi]
        order = cosine_rank(qv, chunk_mat)
        ranked = [chunks[i] for i in order]

        hits = {k: chapter_hit(ranked, q["target_book"], q["target_chapter"], k)
                for k in ks}
        rr   = chapter_rr(ranked, q["target_book"], q["target_chapter"])
        nd   = chapter_ndcg(ranked, q["target_book"], q["target_chapter"], 10)

        # Find first hit position for reporting
        first_hit_rank = None
        for rank, chunk in enumerate(ranked[:50], 1):
            if chunk.contains_chapter(q["target_book"], q["target_chapter"]):
                first_hit_rank = rank
                top_chunk_ref = ranked[0].reference
                hit_chunk_ref = chunk.reference
                break

        for k in ks:
            agg[f"hit@{k}"].append(hits[k])
        agg["mrr"].append(rr)
        agg["ndcg10"].append(nd)

        results["per_query"].append({
            "tag":           q["tag"],
            "query":         q["query"][:80],
            "target":        f"{q['target_book']} {q['target_chapter']}",
            "first_hit_rank": first_hit_rank,
            "top1_chunk":    ranked[0].reference if ranked else "—",
            "mrr":           round(rr, 4),
            **{f"hit@{k}": hits[k] for k in ks},
        })

    def avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else 0.0

    results["aggregate"] = {
        **{f"hit@{k}": avg(agg[f"hit@{k}"]) for k in ks},
        "mrr":    avg(agg["mrr"]),
        "ndcg10": avg(agg["ndcg10"]),
        "n":      len(queries),
    }
    return results


def main():
    print("=" * 62)
    print("SourceTruth Chunking Strategy Evaluation")
    print("Books: Isaiah, Job, Matthew  |  Strategies: 5  |  In-memory")
    print("=" * 62)

    # 1. Fetch and parse all books
    all_verses: dict[str, list[Verse]] = {}
    for book_spec in BOOKS:
        name = book_spec["name"]
        print(f"\nFetching {name} ({book_spec['abbrev'].upper()}) ...")
        data = fetch_book(book_spec["abbrev"], book_spec["nr"])
        if data is None:
            raise SystemExit(f"Failed to fetch {name}")
        verses = parse_verses(data, name)
        all_verses[name] = verses
        chs = max(v.chapter for v in verses)
        print(f"  {len(verses)} verses, {chs} chapters")

    # 2. Build chunks per strategy per book
    print("\nBuilding chunks ...")
    all_chunks: dict[str, dict[str, list[Chunk]]] = {s: {} for s in STRATEGIES}
    for strategy_name, strategy_fn in STRATEGIES.items():
        for book_spec in BOOKS:
            name = book_spec["name"]
            chunks = strategy_fn(all_verses[name], name)
            all_chunks[strategy_name][name] = chunks

        combined = [c for book in BOOKS for c in all_chunks[strategy_name][book["name"]]]
        stats = word_stats(combined)
        print(f"  {strategy_name:10s}: {stats['count']:4d} chunks | "
              f"mean={stats['mean']:5.0f}w | p25={stats['p25']}w p50={stats['median']}w p75={stats['p75']}w")

    # 3. Collect all unique chunk texts per strategy for batch embedding
    print("\nEmbedding chunks (Voyage AI) ...")
    client = get_voyage_client()

    strategy_mats: dict[str, tuple[np.ndarray, list[Chunk]]] = {}
    for strategy_name in STRATEGIES:
        combined_chunks = [c for book in BOOKS
                           for c in all_chunks[strategy_name][book["name"]]]
        texts = [c.text for c in combined_chunks]
        print(f"  {strategy_name}: {len(texts)} chunks ...", end=" ", flush=True)
        t0 = time.monotonic()
        embs = embed_batch(client, texts, input_type="document")
        mat  = normalise(np.array(embs, dtype=np.float32))
        strategy_mats[strategy_name] = (mat, combined_chunks)
        print(f"done ({time.monotonic()-t0:.1f}s)")

    # 4. Define and embed all test queries
    print("\nLoading and embedding queries ...")
    theological_queries = THEOLOGICAL_QUERIES
    synthetic_queries   = load_synthetic_queries()
    all_queries         = theological_queries + synthetic_queries

    query_texts = [q["query"] for q in all_queries]
    print(f"  {len(theological_queries)} theological + {len(synthetic_queries)} synthetic = {len(all_queries)} total")
    print("  Embedding queries ...", end=" ", flush=True)
    t0 = time.monotonic()
    q_embs = embed_batch(client, query_texts, input_type="query")
    query_vecs = normalise(np.array(q_embs, dtype=np.float32))
    print(f"done ({time.monotonic()-t0:.1f}s)")

    theo_vecs  = query_vecs[:len(theological_queries)]
    synth_vecs = query_vecs[len(theological_queries):]

    # 5. Evaluate all strategies on both query sets
    print("\nEvaluating ...")
    results = {
        "metadata": {
            "date":       "2026-10-24",
            "embed_model": EMBED_MODEL,
            "books":      [b["name"] for b in BOOKS],
            "strategies": list(STRATEGIES.keys()),
            "n_theological": len(theological_queries),
            "n_synthetic":   len(synthetic_queries),
        },
        "strategies": {},
    }

    for strategy_name, (mat, chunks) in strategy_mats.items():
        print(f"  {strategy_name} ...", end=" ", flush=True)

        theo_res  = evaluate_strategy(
            strategy_name, chunks, mat, theo_vecs, theological_queries)
        synth_res = evaluate_strategy(
            strategy_name, chunks, mat, synth_vecs, synthetic_queries) if len(synthetic_queries) > 0 else {}

        results["strategies"][strategy_name] = {
            "chunk_stats":  theo_res["chunk_stats"],
            "theological":  {
                "aggregate": theo_res["aggregate"],
                "per_query":  theo_res["per_query"],
            },
            "synthetic": {
                "aggregate":   synth_res.get("aggregate", {}),
                "per_query":   synth_res.get("per_query", []),
            } if synth_res else {},
        }

        t = theo_res["aggregate"]
        s = synth_res.get("aggregate", {})
        print(
            f"theo Hit@5={t.get('hit@5',0):.2f} MRR={t.get('mrr',0):.3f} | "
            f"syn Hit@5={s.get('hit@5',0):.2f} MRR={s.get('mrr',0):.3f}"
        )

    # 6. Write results
    out_path = HERE / "chunk_strategy_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written → {out_path}")

    # 7. Print summary table
    print("\n" + "=" * 62)
    print("SUMMARY")
    print(f"{'Strategy':<12} {'Chunks':>6} {'Avg W':>6} "
          f"{'Theo H@1':>9} {'Theo H@5':>9} {'Theo MRR':>9} "
          f"{'Syn H@5':>8} {'Syn MRR':>8}")
    print("-" * 74)
    for sname in STRATEGIES:
        sr   = results["strategies"][sname]
        cs   = sr["chunk_stats"]
        ta   = sr["theological"]["aggregate"]
        sa   = sr["synthetic"].get("aggregate", {})
        print(f"{sname:<12} {cs['count']:>6} {cs['mean']:>6.0f} "
              f"{ta.get('hit@1',0):>9.3f} {ta.get('hit@5',0):>9.3f} {ta.get('mrr',0):>9.3f} "
              f"{sa.get('hit@5',0):>8.3f} {sa.get('mrr',0):>8.3f}")
    print("=" * 62)
    print(f"\nFull results: {out_path}")


if __name__ == "__main__":
    main()
