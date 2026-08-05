"""
Hybrid search: pgvector (cosine) + PostgreSQL FTS + RRF fusion + entity boost.

Same RRF pattern as the TCM tool, adapted for PostgreSQL and multi-tradition filtering.
"""

from __future__ import annotations

import re
from typing import Optional

from embed import embed_query

RRF_K          = 60
VECTOR_WEIGHT  = 0.7
KEYWORD_WEIGHT = 0.3
ENTITY_BOOST   = 0.25
CANDIDATE_POOL = 60


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
        top_k: int = 8,
        query_vec: list[float] | None = None,
    ) -> list[dict]:

        if query_vec is None:
            query_vec = embed_query(query)

        # ── Build filter clauses ──────────────────────────────────────────────
        filter_parts: list[str] = []
        filter_vals: list = []

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
                ce.embedding <=> %s::vector AS distance,
                ROW_NUMBER() OVER (ORDER BY ce.embedding <=> %s::vector) AS vec_rank
            FROM chunk_embeddings ce
            JOIN document_chunks dc ON dc.id = ce.chunk_id
            JOIN canon_texts ct ON ct.id = dc.text_id
            WHERE TRUE {filter_sql}
            ORDER BY distance
            LIMIT %s
        """
        vec_params = [query_vec, query_vec] + filter_vals + [CANDIDATE_POOL]

        with self.conn.cursor() as cur:
            cur.execute(vec_sql, vec_params)
            vec_rows = cur.fetchall()

        vector_map: dict[int, dict] = {
            r['chunk_id']: {'vec_rank': r['vec_rank'], 'distance': float(r['distance'])}
            for r in vec_rows
        }

        # ── FTS keyword search ────────────────────────────────────────────────
        words = [w for w in re.sub(r'[^\w\s]', ' ', query).split() if len(w) >= 2]
        keyword_map: dict[int, dict] = {}

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
                WHERE dc.chunk_fts @@ to_tsquery('simple', %s)
                {filter_sql}
                ORDER BY kw_score DESC
                LIMIT %s
            """
            kw_params = [tsquery, tsquery, tsquery] + filter_vals + [CANDIDATE_POOL]

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

        # Entity boost
        if entity_ids:
            entity_set = set(entity_ids)
            for chunk_id, row in {**vector_map, **keyword_map}.items():
                pass   # entity_ids on chunk retrieved below; boost applied after

        top_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:top_k * 2]

        if not top_ids:
            return []

        # ── Fetch full chunk data ─────────────────────────────────────────────
        ph = ','.join(['%s'] * len(top_ids))
        with self.conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    dc.id,
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

        # Compose results with entity boost applied
        results = []
        entity_set = set(entity_ids or [])

        for chunk_id in top_ids:
            if chunk_id not in rows:
                continue
            row = dict(rows[chunk_id])
            score = rrf_scores[chunk_id]

            chunk_entity_ids = row.get('entity_ids') or []
            if entity_set and entity_set.intersection(set(chunk_entity_ids)):
                score += ENTITY_BOOST

            row['score'] = round(score, 6)
            row['vec_score'] = round(
                vector_map.get(chunk_id, {}).get('distance', 1.0), 4
            )
            results.append(row)

        results.sort(key=lambda r: r['score'], reverse=True)
        return results[:top_k]
