# -*- coding: utf-8 -*-
"""
Retrieval evaluation for the Jewish corpus (Tanakh JPS 1917 + Mishnah Silverstein).

System variants:
  fts_only       — PostgreSQL tsvector FTS (English; tested since both corpora are in English)
  dense          — voyage-multilingual-2 cosine similarity (in-process numpy)
  hybrid         — RRF(dense, fts, k=60)
  dense_mmr      — Dense re-ranked with Maximal Marginal Relevance (λ=0.7)
  dense_text_dedup — Dense deduplicated to one chunk per source text (book/tractate)
  dense_two_level  — Two-level: top-N books/tractates × M chunks each

Metrics (K = 1, 3, 5, 10, 15):
  C-Recall@K  — chunk level (exact chunk retrieved)
  T-Recall@K  — source text level (correct book or tractate retrieved)
  MRR, nDCG@10 at both levels

Run: python eval_jewish/score.py
Prereq: python eval_jewish/generate_queries.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2
import voyageai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

HERE = Path(__file__).resolve().parent

JEWISH_CORPORA = ["tanakh-jps1917", "mishnah-silverstein"]
EMBED_MODEL    = "voyage-multilingual-2"
KS    = [1, 3, 5, 10, 15]
DEPTH = 50
N_ART  = 8   # two-level: source texts (books/tractates) to surface
N_PARA = 3   # two-level: chunks per source text


def connect() -> psycopg2.extensions.connection:
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    return psycopg2.connect(url)


def load_matrix(conn):
    import json as _json
    cur = conn.cursor()
    cur.execute("""
        SELECT ce.chunk_id, dc.text_id, ce.embedding::text, dc.token_count, sc.code
        FROM chunk_embeddings ce
        JOIN document_chunks dc ON dc.id = ce.chunk_id
        JOIN canon_texts ct ON ct.id = dc.text_id
        JOIN source_corpora sc ON sc.id = ct.corpus_id
        WHERE sc.code = ANY(%s)
    """, (JEWISH_CORPORA,))
    rows = cur.fetchall()
    if not rows:
        raise SystemExit("No embeddings found for Jewish corpora. Check ingestion.")

    mat        = np.stack([np.array(_json.loads(r[2]), dtype=np.float32) for r in rows])
    chunk_ids  = np.array([r[0] for r in rows], dtype=np.int64)
    text_ids   = np.array([r[1] for r in rows], dtype=np.int64)
    tok_lens   = np.array([r[3] or 0 for r in rows], dtype=np.int32)
    corpus_arr = np.array([r[4] for r in rows])

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat  /= np.where(norms > 0, norms, 1.0)

    corpus_counts = defaultdict(int)
    for c in corpus_arr:
        corpus_counts[c] += 1
    print(f"Loaded {mat.shape[0]:,} vectors ({mat.shape[1]}-dim) — {dict(corpus_counts)}")
    return mat, chunk_ids, text_ids, tok_lens, corpus_arr


def fts_retrieve(conn, query: str, depth: int) -> list[tuple[int, int]]:
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT dc.id, dc.text_id
            FROM document_chunks dc
            JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = ANY(%s)
              AND dc.chunk_fts @@ websearch_to_tsquery('simple', %s)
            ORDER BY ts_rank_cd(dc.chunk_fts, websearch_to_tsquery('simple', %s)) DESC
            LIMIT %s
        """, (JEWISH_CORPORA, query, query, depth))
        return [(r[0], r[1]) for r in cur.fetchall()]
    except Exception:
        return []


def norm_vec(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def dense_retrieve(qv, mat, chunk_ids, text_ids, depth):
    sims = mat @ qv
    top  = np.argsort(-sims)[:depth]
    return [(int(chunk_ids[i]), int(text_ids[i])) for i in top]


def dense_retrieve_filtered(qv, mat, chunk_ids, text_ids, mask, depth):
    sims = mat @ qv
    sims[~mask] = -1.0
    top = np.argsort(-sims)[:depth]
    return [(int(chunk_ids[i]), int(text_ids[i])) for i in top if mask[i]]


def text_dedup(ranked):
    seen, out = set(), []
    for cid, tid in ranked:
        if tid not in seen:
            seen.add(tid)
            out.append((cid, tid))
    return out


def two_level_retrieve(ranked, n_art=N_ART, n_para=N_PARA):
    text_chunks: dict[int, list] = {}
    text_order: list[int] = []
    for cid, tid in ranked:
        if tid not in text_chunks:
            text_chunks[tid] = []
            text_order.append(tid)
        if len(text_chunks[tid]) < n_para:
            text_chunks[tid].append((cid, tid))
    out = []
    for tid in text_order[:n_art]:
        out.extend(text_chunks[tid])
    return out


def build_chunk_idx(chunk_ids):
    return {int(cid): i for i, cid in enumerate(chunk_ids)}


def mmr_rerank(qv, mat, ranked, chunk_idx, depth, lam=0.7):
    known   = [r for r in ranked if r[0] in chunk_idx]
    unknown = [r for r in ranked if r[0] not in chunk_idx]
    if not known:
        return ranked[:depth]
    idxs      = [chunk_idx[cid] for cid, _ in known]
    cand_vecs = mat[idxs]
    q_sims    = (cand_vecs @ qv).tolist()
    selected, sel_vecs, remaining = [], [], list(range(len(known)))
    while remaining and len(selected) < depth:
        if not sel_vecs:
            best_i = max(remaining, key=lambda i: q_sims[i])
        else:
            sel_mat = np.stack(sel_vecs)
            best_i, best_score = None, -1e9
            for i in remaining:
                score = lam * q_sims[i] - (1 - lam) * float(np.max(sel_mat @ cand_vecs[i]))
                if score > best_score:
                    best_score, best_i = score, i
        selected.append(best_i)
        sel_vecs.append(cand_vecs[best_i])
        remaining.remove(best_i)
    return [known[i] for i in selected] + unknown


def build_text_fps(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT dc.id, dc.chunk_text
        FROM document_chunks dc
        JOIN canon_texts ct ON ct.id = dc.text_id
        JOIN source_corpora sc ON sc.id = ct.corpus_id
        WHERE sc.code = ANY(%s)
    """, (JEWISH_CORPORA,))
    return {r[0]: r[1].strip()[:300] for r in cur.fetchall()}


def dedup_ranked(ranked, id_to_fp):
    seen, out = set(), []
    for cid, tid in ranked:
        fp = id_to_fp.get(cid)
        if fp is not None and fp in seen:
            continue
        if fp is not None:
            seen.add(fp)
        out.append((cid, tid))
    return out


def rrf_fuse(dense, fts, depth, k=60):
    scores, text_of = defaultdict(float), {}
    for ranklist in (dense, fts):
        for rank, (cid, tid) in enumerate(ranklist):
            scores[cid] += 1.0 / (k + rank)
            text_of[cid] = tid
    ordered = sorted(scores, key=lambda c: -scores[c])[:depth]
    return [(c, text_of[c]) for c in ordered]


def recall_at_k(ranked_ids, gold, k):
    return 1.0 if any(i in gold for i in ranked_ids[:k]) else 0.0


def mrr(ranked_ids, gold):
    for i, pid in enumerate(ranked_ids):
        if pid in gold:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(ranked_ids, gold, k):
    dcg  = sum(1.0 / math.log2(i + 2) for i, pid in enumerate(ranked_ids[:k]) if pid in gold)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), k)))
    return dcg / idcg if idcg else 0.0


def score_ranked(ranked, gold_chunks, gold_texts):
    chunk_ids = [c for c, _ in ranked]
    text_ids  = [t for _, t in ranked]
    out = {}
    for k in KS:
        out[f"chunk_r{k}"] = recall_at_k(chunk_ids, gold_chunks, k)
        out[f"text_r{k}"]  = recall_at_k(text_ids,  gold_texts,  k)
    out["chunk_mrr"]  = mrr(chunk_ids, gold_chunks)
    out["text_mrr"]   = mrr(text_ids,  gold_texts)
    out["chunk_ndcg"] = ndcg_at_k(chunk_ids, gold_chunks, 10)
    out["text_ndcg"]  = ndcg_at_k(text_ids,  gold_texts,  10)
    return out


def agg(dicts):
    keys = dicts[0].keys()
    return {k: round(sum(d[k] for d in dicts) / len(dicts), 4) for k in keys}


def fetch_texts(conn, ranked, limit=10):
    cur = conn.cursor()
    out = []
    for cid, tid in ranked[:limit]:
        cur.execute("""
            SELECT dc.chunk_text, ct.title_english, ct.collection,
                   dc.reference, dc.chapter, dc.section
            FROM document_chunks dc
            JOIN canon_texts ct ON ct.id = dc.text_id
            WHERE dc.id = %s
        """, (cid,))
        row = cur.fetchone()
        if row:
            out.append({
                "chunk_id":  cid,
                "text_id":   tid,
                "text":      row[0],
                "title":     row[1] or "",
                "collection": row[2] or "",
                "reference": row[3] or "",
                "chapter":   row[4] or "",
                "section":   row[5] or "",
            })
    return out


def main() -> None:
    query_path = HERE / "queries.jsonl"
    if not query_path.exists():
        raise SystemExit("queries.jsonl not found — run generate_queries.py first.")

    queries = [json.loads(l) for l in open(query_path, encoding="utf-8")]
    print(f"Loaded {len(queries)} queries")

    conn = connect()
    mat, chunk_ids, text_ids, tok_lens, corpus_arr = load_matrix(conn)
    id_to_fp  = build_text_fps(conn)
    chunk_idx = build_chunk_idx(chunk_ids)
    print(f"Built fingerprints for {len(id_to_fp):,} chunks")

    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        raise SystemExit("VOYAGE_API_KEY not set in .env")
    vo = voyageai.Client(api_key=api_key)

    def embed_query(text: str) -> np.ndarray:
        for attempt in range(5):
            try:
                r = vo.embed([text], model=EMBED_MODEL, input_type="query")
                return norm_vec(np.array(r.embeddings[0], dtype=np.float32))
            except Exception as e:
                if attempt < 4:
                    time.sleep(4 * (attempt + 1))
                else:
                    raise

    acc       = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    real_pool = {}

    for i, q in enumerate(queries):
        qtext       = q["query"]
        gold_chunks = set(q.get("gold_chunk_ids") or [])
        gold_texts  = set(q.get("gold_text_ids")  or [])
        track       = q["track"]
        corpus_seg  = q.get("corpus_code", "all") if track == "synthetic" else "realistic"
        qid         = q["id"]

        fts_res = dedup_ranked(fts_retrieve(conn, qtext, DEPTH), id_to_fp)

        if not q.get("needs_judge"):
            qv              = embed_query(qtext)
            dense_res       = dedup_ranked(dense_retrieve(qv, mat, chunk_ids, text_ids, DEPTH), id_to_fp)
            hybrid_res      = dedup_ranked(rrf_fuse(dense_res, fts_res, DEPTH), id_to_fp)
            dense_mmr       = mmr_rerank(qv, mat, dense_res, chunk_idx, DEPTH)
            dense_tdedup    = text_dedup(dense_res)
            dense_two_level = two_level_retrieve(dense_res)

            for cfg, ranked in [("fts_only",        fts_res),
                                 ("dense",           dense_res),
                                 ("hybrid",          hybrid_res),
                                 ("dense_mmr",       dense_mmr),
                                 ("dense_text_dedup", dense_tdedup),
                                 ("dense_two_level", dense_two_level)]:
                m = score_ranked(ranked, gold_chunks, gold_texts)
                acc[cfg][track][corpus_seg].append(m)
                acc[cfg][track]["all"].append(m)

            hit = score_ranked(dense_res, gold_chunks, gold_texts)["chunk_r5"]
            print(f"  [{i+1:03d}] {'✓' if hit else '✗'} {qid} ({q.get('reference','')})", flush=True)
            time.sleep(0.15)

        else:
            qv              = embed_query(qtext)
            dense_res       = dedup_ranked(dense_retrieve(qv, mat, chunk_ids, text_ids, DEPTH), id_to_fp)
            hybrid_res      = dedup_ranked(rrf_fuse(dense_res, fts_res, DEPTH), id_to_fp)
            dense_mmr       = mmr_rerank(qv, mat, dense_res, chunk_idx, DEPTH)
            dense_tdedup    = text_dedup(dense_res)
            dense_two_level = two_level_retrieve(dense_res)

            real_pool[qid] = {
                "query":    qtext,
                "category": q.get("category"),
                "note":     q.get("note"),
                "results": {
                    "dense":            fetch_texts(conn, dense_res),
                    "hybrid":           fetch_texts(conn, hybrid_res),
                    "dense_mmr":        fetch_texts(conn, dense_mmr),
                    "dense_text_dedup": fetch_texts(conn, dense_tdedup),
                    "dense_two_level":  fetch_texts(conn, dense_two_level, limit=N_ART * N_PARA),
                    "fts":              fetch_texts(conn, fts_res[:10]),
                },
            }
            print(f"  [{i+1:03d}] JUDGE {qid}", flush=True)
            time.sleep(0.15)

    conn.close()

    scores = {}
    for cfg in acc:
        scores[cfg] = {}
        for track in acc[cfg]:
            scores[cfg][track] = {}
            for seg in acc[cfg][track]:
                dl = acc[cfg][track][seg]
                scores[cfg][track][seg] = {"n": len(dl), **agg(dl)}

    (HERE / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "realistic_pool.json").write_text(
        json.dumps(real_pool, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nScoring done. Writing scorecard…")
    _write_scorecard(scores, HERE)
    print("Outputs: scores.json  realistic_pool.json  scorecard.md")


def _write_scorecard(scores, out_dir):
    lines = ["# Jewish Corpus Retrieval Evaluation — Scorecard", ""]
    lines += [
        "**Corpora**: Tanakh JPS 1917 (1,037 chunks, English) · "
        "Mishnah Silverstein (1,927 chunks, English).",
        f"**Embedding**: {EMBED_MODEL} (1024-dim, unit-normalised).",
        "**Systems**: FTS (tsvector/simple), Dense (cosine), Hybrid (RRF k=60), "
        f"Dense+MMR, Dense (text-dedup), Dense Two-Level ({N_ART}src×{N_PARA}chunk).",
        "**Metrics**: C = chunk level, T = text/book level. Recall@K, MRR, nDCG@10.",
        "",
    ]

    cfgs = ["fts_only", "dense", "hybrid", "dense_mmr", "dense_text_dedup", "dense_two_level"]
    cfg_labels = {
        "fts_only":         "FTS-only",
        "dense":            "Dense",
        "hybrid":           "Hybrid RRF",
        "dense_mmr":        "Dense+MMR",
        "dense_text_dedup": "Dense (text-dedup)",
        "dense_two_level":  f"Dense Two-Level ({N_ART}×{N_PARA})",
    }

    CORPUS_LABELS = {
        "all":                  "Overall (both corpora)",
        "tanakh-jps1917":       "Tanakh (JPS 1917) only",
        "mishnah-silverstein":  "Mishnah (Silverstein) only",
    }

    def _table(seg_key, seg_label):
        lines.append(f"### {seg_label}")
        lines.append(
            "| System | n | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | "
            "T-R@1 | T-R@5 | T-R@10 | T-MRR |"
        )
        lines.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
        for cfg in cfgs:
            r = scores.get(cfg, {}).get("synthetic", {}).get(seg_key)
            if not r:
                continue
            lines.append(
                f"| {cfg_labels[cfg]} | {r['n']} "
                f"| {r['chunk_r1']:.3f} | {r['chunk_r5']:.3f} | {r['chunk_r10']:.3f} "
                f"| {r['chunk_mrr']:.3f} | {r['chunk_ndcg']:.3f} "
                f"| {r['text_r1']:.3f} | {r['text_r5']:.3f} | {r['text_r10']:.3f} "
                f"| {r['text_mrr']:.3f} |"
            )
        lines.append("")

    lines.append("## Track: synthetic (known-item, auto-scored)")
    lines.append("")
    for seg_key in ("all", "tanakh-jps1917", "mishnah-silverstein"):
        _table(seg_key, CORPUS_LABELS[seg_key])

    (out_dir / "scorecard.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
