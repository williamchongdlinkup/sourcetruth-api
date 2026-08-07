# -*- coding: utf-8 -*-
"""
Retrieval evaluation for the Hindu corpus (Bhagavad Gita + Upanishads).

Run: python eval_hindu/score.py
Prereq: python eval_hindu/generate_queries.py
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
import psycopg2.extras
import voyageai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

HERE = Path(__file__).resolve().parent

HINDU_CORPORA = ["bhagavad-gita", "upanishads", "yoga-sutras"]
EMBED_MODEL   = "voyage-multilingual-2"
KS    = [1, 3, 5, 10, 15]
DEPTH = 50
N_ART  = 8
N_PARA = 3


def connect():
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def load_matrix(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT ce.chunk_id, dc.text_id, ce.embedding::text, dc.token_count, sc.code
        FROM chunk_embeddings ce
        JOIN document_chunks dc ON dc.id = ce.chunk_id
        JOIN canon_texts ct ON ct.id = dc.text_id
        JOIN source_corpora sc ON sc.id = ct.corpus_id
        WHERE sc.code = ANY(%s)
    """, (HINDU_CORPORA,))
    rows = cur.fetchall()
    if not rows:
        raise SystemExit("No embeddings found for Hindu corpora. Check ingestion.")

    mat        = np.stack([np.array(json.loads(r['embedding']), dtype=np.float32) for r in rows])
    chunk_ids  = np.array([r['chunk_id']    for r in rows], dtype=np.int64)
    text_ids   = np.array([r['text_id']     for r in rows], dtype=np.int64)
    tok_lens   = np.array([r['token_count'] or 0 for r in rows], dtype=np.int32)
    corpus_arr = np.array([r['code']        for r in rows])

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat  /= np.where(norms > 0, norms, 1.0)

    counts = defaultdict(int)
    for c in corpus_arr:
        counts[c] += 1
    print(f"Loaded {mat.shape[0]:,} vectors — {dict(counts)}")
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
        """, (HINDU_CORPORA, query, query, depth))
        return [(r['id'], r['text_id']) for r in cur.fetchall()]
    except Exception:
        return []


def dense_retrieve(qv, mat, chunk_ids, text_ids, depth):
    sims = mat @ qv
    top  = np.argsort(-sims)[:depth]
    return [(int(chunk_ids[i]), int(text_ids[i])) for i in top]


def dense_retrieve_filtered(qv, mat, chunk_ids, text_ids, mask, depth):
    sims = mat @ qv
    sims[~mask] = -1.0
    top = np.argsort(-sims)[:depth]
    return [(int(chunk_ids[i]), int(text_ids[i])) for i in top if mask[i]]


def rrf_fuse(dense_list, fts_list, k=60):
    scores: dict[int, float] = defaultdict(float)
    tid_map: dict[int, int]  = {}
    for rank, (cid, tid) in enumerate(dense_list):
        scores[cid] += 1.0 / (k + rank + 1)
        tid_map[cid] = tid
    for rank, (cid, tid) in enumerate(fts_list):
        scores[cid] += 1.0 / (k + rank + 1)
        tid_map[cid] = tid
    return sorted(((cid, tid_map[cid]) for cid in scores), key=lambda x: -scores[x[0]])


def mmr_rerank(qv, results, mat, chunk_ids, depth, lam=0.7):
    if len(results) <= 1:
        return results[:depth]
    idx_map = {int(chunk_ids[i]): i for i in range(len(chunk_ids))}
    valid = [(cid, tid) for cid, tid in results if cid in idx_map]
    if not valid:
        return results[:depth]
    cand_vecs = np.stack([mat[idx_map[cid]] for cid, _ in valid])
    q_sims    = (cand_vecs @ qv).tolist()
    selected, sel_vecs, remaining = [], [], list(range(len(valid)))
    while remaining and len(selected) < depth:
        if not sel_vecs:
            best_i = max(remaining, key=lambda i: q_sims[i])
        else:
            sel_mat = np.stack(sel_vecs)
            best_i  = max(remaining,
                          key=lambda i: lam * q_sims[i] - (1-lam) * float(np.max(sel_mat @ cand_vecs[i])))
        selected.append(best_i)
        sel_vecs.append(cand_vecs[best_i])
        remaining.remove(best_i)
    return [valid[i] for i in selected]


def text_dedup(results, depth):
    seen, out = set(), []
    for cid, tid in results:
        if tid not in seen:
            seen.add(tid)
            out.append((cid, tid))
        if len(out) >= depth:
            break
    return out


def two_level(qv, mat, chunk_ids, text_ids, n_art, n_para):
    sims = mat @ qv
    art_scores: dict[int, float] = defaultdict(float)
    for i, s in enumerate(sims):
        tid = int(text_ids[i])
        if s > art_scores[tid]:
            art_scores[tid] = float(s)
    top_arts = sorted(art_scores, key=lambda t: -art_scores[t])[:n_art]
    result = []
    for tid in top_arts:
        mask = text_ids == tid
        sub  = dense_retrieve_filtered(qv, mat, chunk_ids, text_ids, mask, n_para)
        result.extend(sub)
    return result


def compute_metrics(results, gold_chunk, gold_text, ks):
    chunk_hit = {k: int(gold_chunk in [c for c, _ in results[:k]]) for k in ks}
    text_hit  = {k: int(gold_text  in [t for _, t in results[:k]]) for k in ks}

    def _rr(items, gold, ki):
        for rank, item in enumerate(items, 1):
            if item[ki] == gold:
                return 1.0 / rank
        return 0.0

    def _ndcg(items, gold, ki, k):
        return sum(1.0/math.log2(r+2) for r, it in enumerate(items[:k]) if it[ki] == gold)

    return {'c_hit': chunk_hit, 't_hit': text_hit,
            'c_mrr': _rr(results, gold_chunk, 0), 't_mrr': _rr(results, gold_text, 1),
            'c_ndcg10': _ndcg(results, gold_chunk, 0, 10),
            't_ndcg10': _ndcg(results, gold_text, 1, 10)}


def embed_query_vec(client, text: str) -> np.ndarray:
    for attempt in range(3):
        try:
            res = client.embed([text], model=EMBED_MODEL, input_type='query')
            v = np.array(res.embeddings[0], dtype=np.float32)
            n = np.linalg.norm(v)
            return v / n if n else v
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                raise


def main():
    queries_path = HERE / "queries.jsonl"
    if not queries_path.exists():
        raise SystemExit(f"Run generate_queries.py first.")

    queries   = [json.loads(l) for l in queries_path.read_text(encoding='utf-8').splitlines() if l.strip()]
    synthetic = [q for q in queries if q['query_type'].startswith('synthetic') and q.get('chunk_id')]
    realistic = [q for q in queries if q['query_type'].startswith('realistic')]
    print(f"Loaded {len(synthetic)} synthetic, {len(realistic)} realistic queries.")

    conn   = connect()
    client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    mat, chunk_ids, text_ids, tok_lens, corpus_arr = load_matrix(conn)

    SYSTEMS = ['fts_only', 'dense', 'hybrid', 'dense_mmr', 'dense_text_dedup', 'dense_two_level']
    agg = {s: defaultdict(list) for s in SYSTEMS}
    agg['per_corpus'] = {c: defaultdict(list) for c in HINDU_CORPORA}

    for qi, q in enumerate(synthetic):
        if qi % 10 == 0:
            print(f"  Synthetic {qi+1}/{len(synthetic)} ...", flush=True)
        gold_chunk = q['chunk_id']
        cur = conn.cursor()
        cur.execute("SELECT text_id FROM document_chunks WHERE id = %s", (gold_chunk,))
        row = cur.fetchone()
        if not row:
            continue
        gold_text = row['text_id']
        qv = embed_query_vec(client, q['query'])

        dense_res = dense_retrieve(qv, mat, chunk_ids, text_ids, DEPTH)
        fts_res   = fts_retrieve(conn, q['query'], DEPTH)
        hybrid_r  = rrf_fuse(dense_res, fts_res)
        mmr_r     = mmr_rerank(qv, dense_res, mat, chunk_ids, DEPTH)
        dedup_r   = text_dedup(dense_res, DEPTH)
        two_r     = two_level(qv, mat, chunk_ids, text_ids, N_ART, N_PARA)

        for sys_name, res in [('fts_only', fts_res), ('dense', dense_res),
                               ('hybrid', hybrid_r), ('dense_mmr', mmr_r),
                               ('dense_text_dedup', dedup_r), ('dense_two_level', two_r)]:
            m = compute_metrics(res, gold_chunk, gold_text, KS)
            for k in KS:
                agg[sys_name][f'c_r{k}'].append(m['c_hit'][k])
                agg[sys_name][f't_r{k}'].append(m['t_hit'][k])
            agg[sys_name]['c_mrr'].append(m['c_mrr'])
            agg[sys_name]['c_ndcg10'].append(m['c_ndcg10'])

        corp = q.get('corpus', 'bhagavad-gita')
        m_d  = compute_metrics(dense_res, gold_chunk, gold_text, KS)
        for k in KS:
            agg['per_corpus'][corp][f'c_r{k}'].append(m_d['c_hit'][k])
            agg['per_corpus'][corp][f't_r{k}'].append(m_d['t_hit'][k])

    def avg(lst): return round(sum(lst)/len(lst), 4) if lst else 0.0

    lines = ["# Hindu Retrieval Evaluation — Synthetic Track\n"]
    lines.append(f"N = {len(synthetic)} queries | Pool = {mat.shape[0]:,} chunks\n")
    lines.append("\n## System comparison\n")
    lines.append("| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in SYSTEMS:
        d = agg[s]
        lines.append(f"| {s} | {avg(d['c_r1'])} | {avg(d['c_r5'])} | {avg(d['c_r10'])} | "
                     f"{avg(d['c_mrr'])} | {avg(d['c_ndcg10'])} | {avg(d['t_r5'])} |")
    lines.append("\n## Per-corpus (Dense)\n")
    lines.append("| Corpus | n | C-R@1 | C-R@5 | C-MRR | C-nDCG@10 |")
    lines.append("|---|---|---|---|---|---|")
    for corp in HINDU_CORPORA:
        d = agg['per_corpus'].get(corp, {})
        n = len(d.get('c_r1', []))
        lines.append(f"| {corp} | {n} | {avg(d.get('c_r1',[]))} | {avg(d.get('c_r5',[]))} | "
                     f"{avg(d.get('c_mrr',[]))} | {avg(d.get('c_ndcg10',[]))} |")

    scorecard = '\n'.join(lines)
    (HERE / "scorecard.md").write_text(scorecard, encoding='utf-8')
    print("\n" + scorecard)

    pool = []
    for q in realistic:
        qv        = embed_query_vec(client, q['query'])
        dense_res = dense_retrieve(qv, mat, chunk_ids, text_ids, DEPTH)
        dedup_r   = text_dedup(dense_res, DEPTH)
        two_r     = two_level(qv, mat, chunk_ids, text_ids, N_ART, N_PARA)
        mmr_r     = mmr_rerank(qv, dense_res, mat, chunk_ids, DEPTH)
        hybrid_r  = rrf_fuse(dense_res, fts_retrieve(conn, q['query'], DEPTH))
        fts_r     = fts_retrieve(conn, q['query'], DEPTH)

        cids = list({cid for res in [dense_res, dedup_r, two_r, mmr_r, hybrid_r, fts_r]
                         for cid, _ in res[:5]})
        cand_rows: dict[int, dict] = {}
        if cids:
            cur = conn.cursor()
            ph  = ','.join(['%s'] * len(cids))
            cur.execute(f"""
                SELECT dc.id, dc.chunk_text, dc.reference, ct.title_english AS source
                FROM document_chunks dc
                JOIN canon_texts ct ON ct.id = dc.text_id
                WHERE dc.id IN ({ph})
            """, cids)
            cand_rows = {r['id']: dict(r) for r in cur.fetchall()}

        def fmt(res):
            return [{'chunk_id': cid, 'text_id': tid,
                     'text':      cand_rows.get(cid, {}).get('chunk_text', ''),
                     'reference': cand_rows.get(cid, {}).get('reference', ''),
                     'source':    cand_rows.get(cid, {}).get('source', '')}
                    for cid, tid in res[:5]]

        pool.append({'query': q['query'], 'type': q['query_type'],
                     'results': {'dense': fmt(dense_res), 'dense_text_dedup': fmt(dedup_r),
                                 'dense_two_level': fmt(two_r), 'dense_mmr': fmt(mmr_r),
                                 'hybrid': fmt(hybrid_r), 'fts': fmt(fts_r)}})

    (HERE / "realistic_pool.json").write_text(
        json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    print(f"\nWrote scorecard.md and realistic_pool.json ({len(pool)} realistic).")
    conn.close()


if __name__ == '__main__':
    main()
