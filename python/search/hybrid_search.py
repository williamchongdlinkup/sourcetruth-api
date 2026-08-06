"""
Hybrid search: pgvector (cosine) + PostgreSQL FTS + RRF fusion + entity boost.

Post-processing modes (selected per corpus from retrieval eval results 2026-08-05):
  'text_dedup' — one chunk per source text; best for Buddhist canon + English Hadith
  'mmr'        — Maximal Marginal Relevance; best for Quran (reduces cross-surah redundancy)
  'none'       — raw RRF order (fallback)

FTS is automatically skipped for non-English corpus languages — confirmed 0.000 contribution
for Arabic, Pali, Sanskrit, Classical Chinese, and English Hadith (question-answer mismatch
under 'simple' tokenizer; not a missing-column issue).
"""

from __future__ import annotations

import json
import re
from typing import Literal, Optional

import numpy as np

from embed import embed_query

RRF_K          = 60
VECTOR_WEIGHT  = 0.7
KEYWORD_WEIGHT = 0.3
ENTITY_BOOST   = 0.25
CANDIDATE_POOL = 60

# Languages where FTS produces real keyword signal
FTS_LANGUAGES = {'en'}

# Corpora excluded from unscoped searches — require explicit corpus_codes=["gretil"] to access.
# GRETIL Sanskrit: 18.4% C-R@5 confirmed intrinsic (voyage-multilingual-2 cannot align IAST
# Sanskrit with English queries), unchanged between full pool and isolated eval (2026-08-05).
OPT_IN_ONLY_CORPORA = {'gretil'}

# Tradition → optimal rerank mode from retrieval eval
TRADITION_RERANK: dict[str, Literal['mmr', 'text_dedup', 'none']] = {
    'islam':          'text_dedup',
    'theravada':      'text_dedup',
    'pre-sectarian':  'text_dedup',
    'early-buddhist': 'text_dedup',
    'sarvastivada':   'text_dedup',
    'mahasanghika':   'text_dedup',
    'dharmaguptaka':  'text_dedup',
    'judaism':        'text_dedup',
    'christianity':   'text_dedup',   # KJV chapter chunks — one result per book (provisional 2026-08-06)
    'hinduism':       'mmr',           # BG + Upanishads — MMR wins realistic judge 0.932 vs dense 0.877 (2026-08-06)
    'hellenism':      'text_dedup',   # Greek philosophy — text_dedup wins judge 0.9178 vs dense 0.870 (2026-08-07)
}

# Corpus code → optimal rerank mode (more specific than tradition; takes priority)
CORPUS_RERANK: dict[str, Literal['mmr', 'text_dedup', 'none']] = {
    'quran':              'mmr',          # Dense+MMR wins on chunk nDCG@5 (0.692)
    'sahih-bukhari':      'text_dedup',
    'sahih-muslim':       'text_dedup',   # isnad variants make text_dedup the correct mode
    'tanakh-jps1917':     'text_dedup',   # one result per book/chapter
    'mishnah-silverstein': 'text_dedup',  # one result per tractate
    'kjv':               'text_dedup',    # provisional — confirm after eval
    'bhagavad-gita':     'mmr',            # MMR wins LLM judge 0.9324 (2026-08-07)
    'upanishads':        'mmr',            # MMR wins LLM judge 0.9324 (2026-08-07)
    'greek-philosophy':  'text_dedup',    # text_dedup wins LLM judge 0.9178 (2026-08-07)
}


def _infer_rerank(
    traditions: list[str] | None,
    languages: list[str] | None,
    corpus_codes: list[str] | None,
) -> Literal['mmr', 'text_dedup', 'none']:
    """Pick the best-by-eval rerank mode. Corpus codes take priority over traditions."""
    if corpus_codes and len(set(corpus_codes)) == 1:
        mode = CORPUS_RERANK.get(corpus_codes[0])
        if mode:
            return mode
    if traditions and len(set(traditions)) == 1:
        return TRADITION_RERANK.get(traditions[0], 'text_dedup')
    return 'text_dedup'


def _infer_use_fts(
    traditions: list[str] | None,
    languages: list[str] | None,
    corpus_codes: list[str] | None,
) -> bool:
    """Return True only if the scoped corpus benefits from FTS.

    FTS confirmed zero-contribution corpora (2026-08-05/06 eval):
    - Non-English: quran (Arabic), gretil (Sanskrit), sc-data-lzh (Chinese),
      suttacentral (Pali/Chinese)
    - English Q&A mismatch (question-form query vs declarative text, 'simple'
      tokenizer AND logic, no stemming): sahih-bukhari, sahih-muslim,
      tanakh-jps1917, mishnah-silverstein

    Corpus codes are checked first (most specific) before tradition or language.
    """
    NON_FTS_CORPORA = {
        'quran', 'gretil', 'sc-data-lzh', 'suttacentral',
        'sahih-bukhari', 'sahih-muslim',
        'tanakh-jps1917', 'mishnah-silverstein',  # English but Q&A vocab mismatch (2026-08-06)
        'kjv', 'bhagavad-gita', 'upanishads', 'greek-philosophy',  # provisional (2026-08-06)
    }
    if corpus_codes:
        return not set(corpus_codes).issubset(NON_FTS_CORPORA)
    if traditions:
        NON_FTS_TRADITIONS = {'pre-sectarian', 'theravada', 'judaism'}
        if set(traditions).issubset(NON_FTS_TRADITIONS):
            return False
    if languages:
        return bool(set(languages) & FTS_LANGUAGES)
    return True


def _mmr_rerank(
    query_vec: list[float],
    results: list[dict],
    conn,
    top_k: int,
    lam: float = 0.7,
) -> list[dict]:
    """Re-rank with Maximal Marginal Relevance (lam=0.7 → 70% relevance, 30% diversity)."""
    if len(results) <= 1:
        return results

    chunk_ids = [r['id'] for r in results]
    ph = ','.join(['%s'] * len(chunk_ids))
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT chunk_id, embedding::text FROM chunk_embeddings WHERE chunk_id IN ({ph})",
            chunk_ids,
        )
        emb_rows = {r['chunk_id']: r['embedding'] for r in cur.fetchall()}

    cand_vecs, valid = [], []
    for r in results:
        emb_text = emb_rows.get(r['id'])
        if emb_text:
            cand_vecs.append(np.array(json.loads(emb_text), dtype=np.float32))
            valid.append(r)

    if not valid:
        return results[:top_k]

    qv       = np.array(query_vec, dtype=np.float32)
    cand_mat = np.stack(cand_vecs)
    q_sims   = (cand_mat @ qv).tolist()
    selected, sel_vecs, remaining = [], [], list(range(len(valid)))

    while remaining and len(selected) < top_k:
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

    return [valid[i] for i in selected]


def _text_dedup(results: list[dict], top_k: int) -> list[dict]:
    """Keep the highest-scoring chunk per source text (canon_texts.id)."""
    seen, out = set(), []
    for r in results:
        tid = r.get('text_id') or r.get('external_id')
        if tid not in seen:
            seen.add(tid)
            out.append(r)
        if len(out) >= top_k:
            break
    return out


class HybridSearch:

    def __init__(self, conn):
        self.conn = conn

    def search(
        self,
        query: str,
        entity_ids: list[int] | None = None,
        traditions: list[str] | None = None,
        languages: list[str] | None = None,
        collections: list[str] | None = None,
        corpus_codes: list[str] | None = None,
        top_k: int = 8,
        query_vec: list[float] | None = None,
        rerank: Literal['auto', 'mmr', 'text_dedup', 'none'] = 'auto',
    ) -> list[dict]:
        """Search with optional scope filtering and post-processing.

        Scope parameters (can be combined):
          corpus_codes — exact corpus code, e.g. ['sahih-bukhari', 'quran']
          traditions   — tradition name, e.g. ['theravada', 'islam']
          languages    — ISO 639-3 code, e.g. ['en', 'ar', 'pi']

        corpus_codes is the most precise scope and takes priority for rerank selection.
        rerank='auto' applies the best strategy from the 2026-08-05 retrieval evaluation.
        """
        if query_vec is None:
            query_vec = embed_query(query)

        if rerank == 'auto':
            rerank = _infer_rerank(traditions, languages, corpus_codes)
        use_fts = _infer_use_fts(traditions, languages, corpus_codes)

        pool = CANDIDATE_POOL if rerank == 'none' else max(CANDIDATE_POOL, top_k * 4)

        # ── Build filter clauses ──────────────────────────────────────────────
        filter_parts: list[str] = []
        filter_vals: list = []

        if corpus_codes:
            ph = ','.join(['%s'] * len(corpus_codes))
            filter_parts.append(f"sc.code IN ({ph})")
            filter_vals.extend(corpus_codes)
        elif OPT_IN_ONLY_CORPORA:
            opt_in = sorted(OPT_IN_ONLY_CORPORA)
            ph = ','.join(['%s'] * len(opt_in))
            filter_parts.append(f"sc.code NOT IN ({ph})")
            filter_vals.extend(opt_in)

        if traditions:
            ph = ','.join(['%s'] * len(traditions))
            filter_parts.append(f"ct.tradition IN ({ph})")
            filter_vals.extend(traditions)

        if languages:
            ph = ','.join(['%s'] * len(languages))
            filter_parts.append(f"ct.language IN ({ph})")
            filter_vals.extend(languages)

        if collections:
            ph = ','.join(['%s'] * len(collections))
            filter_parts.append(f"ct.collection IN ({ph})")
            filter_vals.extend(collections)

        filter_sql = ('AND ' + ' AND '.join(filter_parts)) if filter_parts else ''

        # ── Vector search ─────────────────────────────────────────────────────
        vec_sql = f"""
            SELECT
                dc.id AS chunk_id,
                dc.text_id,
                ce.embedding <=> %s::vector AS distance,
                ROW_NUMBER() OVER (ORDER BY ce.embedding <=> %s::vector) AS vec_rank
            FROM chunk_embeddings ce
            JOIN document_chunks dc ON dc.id = ce.chunk_id
            JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE TRUE {filter_sql}
            ORDER BY distance
            LIMIT %s
        """
        vec_params = [query_vec, query_vec] + filter_vals + [pool]

        with self.conn.cursor() as cur:
            cur.execute(vec_sql, vec_params)
            vec_rows = cur.fetchall()

        vector_map: dict[int, dict] = {
            r['chunk_id']: {
                'vec_rank': r['vec_rank'],
                'distance': float(r['distance']),
                'text_id':  r['text_id'],
            }
            for r in vec_rows
        }

        # ── FTS keyword search (skip for non-English / zero-contribution corpora) ──
        keyword_map: dict[int, dict] = {}

        if use_fts:
            words = [w for w in re.sub(r'[^\w\s]', ' ', query).split() if len(w) >= 2]
            if words:
                tsquery = ' | '.join(words)
                fts_sql = f"""
                    SELECT
                        dc.id AS chunk_id,
                        ts_rank(dc.chunk_fts, to_tsquery('simple', %s)) AS kw_score,
                        ROW_NUMBER() OVER (
                            ORDER BY ts_rank(dc.chunk_fts, to_tsquery('simple', %s)) DESC
                        ) AS kw_rank
                    FROM document_chunks dc
                    JOIN canon_texts ct ON ct.id = dc.text_id
                    JOIN source_corpora sc ON sc.id = ct.corpus_id
                    WHERE dc.chunk_fts @@ to_tsquery('simple', %s)
                    {filter_sql}
                    ORDER BY kw_score DESC
                    LIMIT %s
                """
                kw_params = [tsquery, tsquery, tsquery] + filter_vals + [pool]
                try:
                    with self.conn.cursor() as cur:
                        cur.execute(fts_sql, kw_params)
                        kw_rows = cur.fetchall()
                    keyword_map = {
                        r['chunk_id']: {'kw_rank': r['kw_rank'], 'kw_score': float(r['kw_score'])}
                        for r in kw_rows
                    }
                except Exception:
                    self.conn.rollback()

        # ── RRF fusion ────────────────────────────────────────────────────────
        all_ids = set(vector_map) | set(keyword_map)
        rrf_scores: dict[int, float] = {}

        for chunk_id in all_ids:
            vec_score = VECTOR_WEIGHT * (
                1 / (RRF_K + vector_map[chunk_id]['vec_rank'])
                if chunk_id in vector_map else 0
            )
            kw_score = KEYWORD_WEIGHT * (
                1 / (RRF_K + keyword_map[chunk_id]['kw_rank'])
                if chunk_id in keyword_map else 0
            )
            rrf_scores[chunk_id] = vec_score + kw_score

        top_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:pool]

        if not top_ids:
            return []

        # ── Fetch full chunk data ─────────────────────────────────────────────
        ph = ','.join(['%s'] * len(top_ids))
        with self.conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    dc.id,
                    dc.text_id,
                    dc.chunk_text,
                    dc.reference,
                    dc.chapter,
                    dc.section,
                    dc.entity_ids,
                    ct.external_id,
                    ct.title_english,
                    ct.title_original,
                    ct.title_pali,
                    ct.tradition,
                    ct.language,
                    ct.collection,
                    ct.translator,
                    ct.url,
                    sc.code AS corpus_code,
                    sc.name AS corpus_name
                FROM document_chunks dc
                JOIN canon_texts ct ON ct.id = dc.text_id
                JOIN source_corpora sc ON sc.id = ct.corpus_id
                WHERE dc.id IN ({ph})
            """, top_ids)
            rows = {r['id']: r for r in cur.fetchall()}

        # Compose results with entity boost
        entity_set = set(entity_ids or [])
        results = []
        for chunk_id in top_ids:
            if chunk_id not in rows:
                continue
            row = dict(rows[chunk_id])
            score = rrf_scores[chunk_id]
            chunk_entity_ids = row.get('entity_ids') or []
            if entity_set and entity_set.intersection(set(chunk_entity_ids)):
                score += ENTITY_BOOST
            row['score']     = round(score, 6)
            row['vec_score'] = round(vector_map.get(chunk_id, {}).get('distance', 1.0), 4)
            results.append(row)

        results.sort(key=lambda r: r['score'], reverse=True)

        # ── Post-processing ───────────────────────────────────────────────────
        if rerank == 'mmr':
            results = _mmr_rerank(query_vec, results, self.conn, top_k)
        elif rerank == 'text_dedup':
            results = _text_dedup(results, top_k)
        else:
            results = results[:top_k]

        return results
