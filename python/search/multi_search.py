"""
Multi-account fan-out search: queries all Supabase accounts in parallel and
RRF-merges their ranked results into a single unified list.

Each account returns up to top_k * 3 candidates. Global RRF uses per-account
rank (not score) so different score ranges across corpora don't skew the merge.
"""

from __future__ import annotations

import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import get_conn as _get_conn
from embed import embed_query
from .hybrid_search import HybridSearch

RRF_K = 60


class MultiAccountSearch:
    """Fan-out search across multiple Supabase accounts with graceful degradation."""

    def __init__(self, accounts: list[tuple[int, object, str]]):
        """
        accounts: list of (account_num, psycopg2_connection, db_url)
        account_num is 1-based and surfaced in each result as 'account'.
        """
        self._accounts = [
            {'num': num, 'searcher': HybridSearch(conn), 'conn': conn, 'url': url}
            for num, conn, url in accounts
        ]

    def search(
        self,
        query: str,
        entity_ids: list[int] | None = None,
        traditions: list[str] | None = None,
        languages: list[str] | None = None,
        collections: list[str] | None = None,
        corpus_codes: list[str] | None = None,
        top_k: int = 8,
        rerank: str = 'auto',
    ) -> list[dict]:
        candidate_k = top_k * 3  # wider pool per account gives global RRF more signal

        # Embed once — avoids parallel Voyage API calls in the fan-out threads
        query_vec = embed_query(query)

        kwargs = dict(
            query=query,
            query_vec=query_vec,
            entity_ids=entity_ids,
            traditions=traditions,
            languages=languages,
            collections=collections,
            corpus_codes=corpus_codes,
            top_k=candidate_k,
            rerank=rerank,
        )

        per_account: dict[int, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=len(self._accounts)) as executor:
            futures = {
                executor.submit(self._search_account, acc, **kwargs): acc['num']
                for acc in self._accounts
            }
            for fut in as_completed(futures):
                acc_num, results = fut.result()
                per_account[acc_num] = results

        # Global RRF: rank-based fusion across all per-account ranked lists
        rrf_scores: dict[tuple, float] = {}
        result_pool: dict[tuple, dict] = {}

        for acc_num, results in per_account.items():
            for rank, r in enumerate(results, start=1):
                key = (acc_num, r['id'])
                rrf_scores[key] = 1 / (RRF_K + rank)
                result_pool[key] = {**r, 'account': acc_num}

        top_keys = sorted(rrf_scores, key=rrf_scores.__getitem__, reverse=True)[:top_k]

        merged = []
        for key in top_keys:
            r = result_pool[key]
            r['score'] = round(rrf_scores[key], 6)
            merged.append(r)

        return merged

    def _search_account(self, acc: dict, **kwargs) -> tuple[int, list[dict]]:
        try:
            return acc['num'], acc['searcher'].search(**kwargs)
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            # Supabase pooler drops idle connections after ~30s; reconnect and retry once
            try:
                conn = _get_conn(acc['url'])
                acc['conn'] = conn
                acc['searcher'].conn = conn
                return acc['num'], acc['searcher'].search(**kwargs)
            except Exception as e:
                print(f'[multi_search] Account {acc["num"]} reconnect failed: {e}')
                return acc['num'], []
        except Exception as e:
            print(f'[multi_search] Account {acc["num"]} error: {e}')
            return acc['num'], []
