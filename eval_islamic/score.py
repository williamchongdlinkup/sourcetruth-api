# -*- coding: utf-8 -*-
"""Retrieval evaluation for the Islamic corpus. Run: python eval_islamic/score.py"""
from __future__ import annotations
import json, math, os, sys, time
from collections import defaultdict
from pathlib import Path
import numpy as np, psycopg2, psycopg2.extras, voyageai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
HERE = Path(__file__).resolve().parent
CORPORA = ["quran", "sahih-bukhari", "sahih-muslim", "sunan-abu-dawood", "rumi-masnavi"]
EMBED_MODEL = "voyage-multilingual-2"
KS = [1, 3, 5, 10, 15]; DEPTH = 50; N_ART = 8; N_PARA = 3


def connect():
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def load_matrix(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT ce.chunk_id, dc.text_id, ce.embedding::text, dc.token_count, sc.code, ct.title_english AS src
        FROM chunk_embeddings ce JOIN document_chunks dc ON dc.id = ce.chunk_id
        JOIN canon_texts ct ON ct.id = dc.text_id JOIN source_corpora sc ON sc.id = ct.corpus_id
        WHERE sc.code = ANY(%s)
    """, (CORPORA,))
    rows = cur.fetchall()
    if not rows: raise SystemExit("No embeddings found. Check ingestion.")
    mat       = np.stack([np.array(json.loads(r['embedding']), dtype=np.float32) for r in rows])
    chunk_ids = np.array([r['chunk_id'] for r in rows], dtype=np.int64)
    text_ids  = np.array([r['text_id']  for r in rows], dtype=np.int64)
    src_arr   = np.array([r['src'] for r in rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat  /= np.where(norms > 0, norms, 1.0)
    counts = defaultdict(int)
    for r in rows: counts[r['code']] += 1
    print(f"Loaded {mat.shape[0]:,} vectors — {dict(counts)}")
    return mat, chunk_ids, text_ids, src_arr


def fts_retrieve(conn, query, depth):
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT dc.id, dc.text_id FROM document_chunks dc
            JOIN canon_texts ct ON ct.id = dc.text_id JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = ANY(%s) AND dc.chunk_fts @@ websearch_to_tsquery('simple', %s)
            ORDER BY ts_rank_cd(dc.chunk_fts, websearch_to_tsquery('simple', %s)) DESC LIMIT %s
        """, (CORPORA, query, query, depth))
        return [(r['id'], r['text_id']) for r in cur.fetchall()]
    except Exception: return []


def dense_retrieve(qv, mat, chunk_ids, text_ids, depth):
    top = np.argsort(-(mat @ qv))[:depth]
    return [(int(chunk_ids[i]), int(text_ids[i])) for i in top]


def rrf_fuse(dense_list, fts_list, k=60):
    scores = defaultdict(float); tid_map = {}
    for rank, (cid, tid) in enumerate(dense_list): scores[cid] += 1/(k+rank+1); tid_map[cid]=tid
    for rank, (cid, tid) in enumerate(fts_list):   scores[cid] += 1/(k+rank+1); tid_map[cid]=tid
    return sorted(((c, tid_map[c]) for c in scores), key=lambda x: -scores[x[0]])


def mmr_rerank(qv, results, mat, chunk_ids, depth, lam=0.7):
    if len(results) <= 1: return results[:depth]
    idx_map = {int(chunk_ids[i]): i for i in range(len(chunk_ids))}
    valid = [(c, t) for c, t in results if c in idx_map]
    if not valid: return results[:depth]
    cand_vecs = np.stack([mat[idx_map[c]] for c, _ in valid]); q_sims = (cand_vecs @ qv).tolist()
    selected, sel_vecs, remaining = [], [], list(range(len(valid)))
    while remaining and len(selected) < depth:
        if not sel_vecs: best_i = max(remaining, key=lambda i: q_sims[i])
        else:
            sel_mat = np.stack(sel_vecs)
            best_i  = max(remaining, key=lambda i: lam*q_sims[i] - (1-lam)*float(np.max(sel_mat @ cand_vecs[i])))
        selected.append(best_i); sel_vecs.append(cand_vecs[best_i]); remaining.remove(best_i)
    return [valid[i] for i in selected]


def text_dedup(results, depth):
    seen, out = set(), []
    for c, t in results:
        if t not in seen: seen.add(t); out.append((c, t))
        if len(out) >= depth: break
    return out


def two_level(qv, mat, chunk_ids, text_ids, n_art, n_para):
    sims = mat @ qv
    art_scores = defaultdict(float)
    for i, s in enumerate(sims):
        tid = int(text_ids[i])
        if s > art_scores[tid]: art_scores[tid] = float(s)
    top_arts = sorted(art_scores, key=lambda t: -art_scores[t])[:n_art]
    result = []
    for tid in top_arts:
        mask = text_ids == tid; sims2 = mat @ qv; sims2[~mask] = -1.0
        top = np.argsort(-sims2)[:n_para]
        result.extend([(int(chunk_ids[i]), int(text_ids[i])) for i in top if mask[i]])
    return result


def compute_metrics(results, gold_chunk, gold_text, ks):
    c_hit = {k: int(gold_chunk in [c for c, _ in results[:k]]) for k in ks}
    t_hit = {k: int(gold_text  in [t for _, t in results[:k]]) for k in ks}
    def _rr(items, gold, ki):
        for r, it in enumerate(items, 1):
            if it[ki] == gold: return 1.0/r
        return 0.0
    def _ndcg(items, gold, ki, k):
        return sum(1.0/math.log2(r+2) for r, it in enumerate(items[:k]) if it[ki] == gold)
    return {'c_hit': c_hit, 't_hit': t_hit,
            'c_mrr': _rr(results, gold_chunk, 0), 't_mrr': _rr(results, gold_text, 1),
            'c_ndcg10': _ndcg(results, gold_chunk, 0, 10), 't_ndcg10': _ndcg(results, gold_text, 1, 10)}


def embed_query_vec(client, text):
    for attempt in range(3):
        try:
            res = client.embed([text], model=EMBED_MODEL, input_type='query')
            v = np.array(res.embeddings[0], dtype=np.float32); n = np.linalg.norm(v); return v/n if n else v
        except Exception:
            if attempt < 2: time.sleep(5)
            else: raise


def main():
    qpath = HERE / "queries.jsonl"
    if not qpath.exists(): raise SystemExit("Run generate_queries.py first.")
    queries   = [json.loads(l) for l in qpath.read_text(encoding='utf-8').splitlines() if l.strip()]
    synthetic = [q for q in queries if q['query_type'].startswith('synthetic') and q.get('chunk_id')]
    realistic = [q for q in queries if q['query_type'].startswith('realistic')]
    print(f"Loaded {len(synthetic)} synthetic, {len(realistic)} realistic queries.")

    conn   = connect()
    client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    mat, chunk_ids, text_ids, src_arr = load_matrix(conn)

    SYSTEMS = ['fts_only', 'dense', 'hybrid', 'dense_mmr', 'dense_text_dedup', 'dense_two_level']
    agg = {s: defaultdict(list) for s in SYSTEMS}

    for qi, q in enumerate(synthetic):
        if qi % 20 == 0: print(f"  Synthetic {qi+1}/{len(synthetic)} ...", flush=True)
        gold_chunk = q['chunk_id']
        cur = conn.cursor(); cur.execute("SELECT text_id FROM document_chunks WHERE id = %s", (gold_chunk,))
        row = cur.fetchone()
        if not row: continue
        gold_text = row['text_id']; qv = embed_query_vec(client, q['query'])
        dense_res = dense_retrieve(qv, mat, chunk_ids, text_ids, DEPTH)
        fts_res   = fts_retrieve(conn, q['query'], DEPTH)
        for sys_name, res in [
            ('fts_only', fts_res), ('dense', dense_res),
            ('hybrid', rrf_fuse(dense_res, fts_res)),
            ('dense_mmr', mmr_rerank(qv, dense_res, mat, chunk_ids, DEPTH)),
            ('dense_text_dedup', text_dedup(dense_res, DEPTH)),
            ('dense_two_level', two_level(qv, mat, chunk_ids, text_ids, N_ART, N_PARA)),
        ]:
            m = compute_metrics(res, gold_chunk, gold_text, KS)
            for k in KS: agg[sys_name][f'c_r{k}'].append(m['c_hit'][k]); agg[sys_name][f't_r{k}'].append(m['t_hit'][k])
            agg[sys_name]['c_mrr'].append(m['c_mrr']); agg[sys_name]['c_ndcg10'].append(m['c_ndcg10'])

    def avg(lst): return round(sum(lst)/len(lst), 4) if lst else 0.0
    lines = [f"# Islamic Corpus Retrieval Evaluation — Synthetic Track\nN = {len(synthetic)} | Pool = {mat.shape[0]:,}\n\n| System | C-R@1 | C-R@5 | C-R@10 | C-MRR | C-nDCG@10 | T-R@5 |", "|---|---|---|---|---|---|---|"]
    for s in SYSTEMS:
        d = agg[s]
        lines.append(f"| {s} | {avg(d['c_r1'])} | {avg(d['c_r5'])} | {avg(d['c_r10'])} | {avg(d['c_mrr'])} | {avg(d['c_ndcg10'])} | {avg(d['t_r5'])} |")
    scorecard = '\n'.join(lines); (HERE / "scorecard.md").write_text(scorecard, encoding='utf-8'); print("\n" + scorecard)

    pool = []
    for q in realistic:
        qv        = embed_query_vec(client, q['query'])
        dense_res = dense_retrieve(qv, mat, chunk_ids, text_ids, DEPTH)
        dr2 = text_dedup(dense_res, DEPTH); tr2 = two_level(qv, mat, chunk_ids, text_ids, N_ART, N_PARA)
        mr2 = mmr_rerank(qv, dense_res, mat, chunk_ids, DEPTH); fr2 = rrf_fuse(dense_res, fts_retrieve(conn, q['query'], DEPTH))
        cids = list({c for res in [dense_res, dr2, tr2, mr2, fr2, fts_retrieve(conn, q['query'], DEPTH)] for c, _ in res[:5]})
        cand_rows = {}
        if cids:
            cur = conn.cursor(); ph = ','.join(['%s']*len(cids))
            cur.execute(f"SELECT dc.id, dc.chunk_text, dc.reference, ct.title_english AS source FROM document_chunks dc JOIN canon_texts ct ON ct.id = dc.text_id WHERE dc.id IN ({ph})", cids)
            cand_rows = {r['id']: dict(r) for r in cur.fetchall()}
        def fmt(res):
            return [{'chunk_id': c, 'text_id': t, 'text': cand_rows.get(c, {}).get('chunk_text',''),
                     'reference': cand_rows.get(c, {}).get('reference',''), 'source': cand_rows.get(c, {}).get('source','')} for c, t in res[:5]]
        pool.append({'query': q['query'], 'type': q['query_type'],
                     'results': {'dense': fmt(dense_res), 'dense_text_dedup': fmt(dr2),
                                 'dense_two_level': fmt(tr2), 'dense_mmr': fmt(mr2),
                                 'hybrid': fmt(fr2), 'fts': fmt(fts_retrieve(conn, q['query'], DEPTH))}})
    (HERE / "realistic_pool.json").write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\nWrote scorecard.md and realistic_pool.json ({len(pool)} realistic).")
    conn.close()


if __name__ == '__main__':
    main()
