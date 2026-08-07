# -*- coding: utf-8 -*-
"""
LLM-as-judge for Christian corpus realistic queries.
Reads realistic_pool.json from score.py, judges relevance at chunk and source level.
Output: judge_results.json, judge_report.md
Run:    python eval_christianity/judge.py
Prereq: python eval_christianity/score.py
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

HERE = Path(__file__).resolve().parent

JUDGE_MODEL = "claude-haiku-4-5-20251001"
ALL_SYS     = ("dense", "dense_text_dedup", "dense_two_level", "dense_mmr", "hybrid", "fts")

RELEVANCE_PROMPT = """\
You are evaluating a retrieval system for Christian texts
(King James Bible, WEB, ASV, YLT translations; Augustine Confessions and City of God; Apostolic Fathers).

Query: {query}

Retrieved passage from "{source}", {reference}:
---
{text}
---

Rate the relevance of this passage to the query on a 0–2 scale:
  2 = Highly relevant — directly addresses or answers the query
  1 = Partially relevant — related topic but only partial answer
  0 = Not relevant — off-topic

Respond with ONLY a JSON object: {{"score": <0|1|2>, "reason": "<one sentence>"}}
"""

SOURCE_RELEVANCE_PROMPT = """\
You are evaluating a retrieval system for the King James Bible.

Query: {query}

The system retrieved the book "{source}" (testament: {collection}).
A representative excerpt:
---
{text}
---

Rate whether this biblical book as a whole is relevant to the query on a 0–2 scale:
  2 = Highly relevant — this book directly addresses the query topic
  1 = Partially relevant — related but only partially covers the query
  0 = Not relevant

Respond with ONLY a JSON object: {{"score": <0|1|2>, "reason": "<one sentence>"}}
"""

FAILURE_PROMPT = """\
You evaluated a retrieval system for the KJV Bible query: "{query}"

Top-5 results from Dense semantic retrieval:
{results}

Write a 3–5 sentence analysis:
1. Whether the system found good answers, partial answers, or failed.
2. Which book(s)/chapters were retrieved and if they were appropriate.
3. If it failed, what the correct passage/book should have been.
"""


def ndcg_at_5(scores: list[int]) -> float:
    dcg = sum(s / math.log2(i + 2) for i, s in enumerate(scores[:5]))
    ideal_scores = sorted(scores, reverse=True)[:5]
    idcg = sum(s / math.log2(i + 2) for i, s in enumerate(ideal_scores))
    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def judge_passage(client, query: str, text: str, reference: str, source: str) -> int:
    prompt = RELEVANCE_PROMPT.format(
        query=query, text=text[:800], reference=reference or '', source=source or ''
    )
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=JUDGE_MODEL, max_tokens=128,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text.strip()
            m   = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                return int(json.loads(m.group()).get('score', 0))
        except Exception:
            if attempt < 2:
                time.sleep(3)
    return 0


def judge_source(client, query: str, text: str, source: str, collection: str) -> int:
    prompt = SOURCE_RELEVANCE_PROMPT.format(
        query=query, text=text[:800], source=source or '', collection=collection or ''
    )
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=JUDGE_MODEL, max_tokens=128,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text.strip()
            m   = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                return int(json.loads(m.group()).get('score', 0))
        except Exception:
            if attempt < 2:
                time.sleep(3)
    return 0


def main():
    pool_path = HERE / "realistic_pool.json"
    if not pool_path.exists():
        raise SystemExit("Run score.py first.")

    pool   = json.loads(pool_path.read_text(encoding='utf-8'))
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    results_by_sys = {s: [] for s in ALL_SYS}
    judged_queries = []

    positives = [q for q in pool if 'negative' not in q.get('type', '')]
    print(f"Judging {len(positives)} positive queries across {len(ALL_SYS)} systems ...")

    for qi, q_pool in enumerate(positives):
        query = q_pool['query']
        print(f"\n[{qi+1}/{len(positives)}] {query[:70]}")

        # Pool all unique candidates
        all_candidates: dict[int, dict] = {}
        for sys_name in ALL_SYS:
            for item in q_pool['results'].get(sys_name, []):
                cid = item['chunk_id']
                if cid and cid not in all_candidates:
                    all_candidates[cid] = item

        # Judge each candidate once
        chunk_scores: dict[int, int] = {}
        source_scores: dict[str, int] = {}
        for cid, item in all_candidates.items():
            chunk_scores[cid] = judge_passage(
                client, query, item.get('text', ''), item.get('reference', ''), item.get('source', '')
            )
            src = item.get('source', '')
            if src and src not in source_scores:
                source_scores[src] = judge_source(client, query, item.get('text', ''), src, '')
            print(f"  chunk {cid} ({item.get('source','')[:20]}): {chunk_scores[cid]}")

        # Compute per-system nDCG@5
        sys_chunk_ndcg  = {}
        sys_source_ndcg = {}
        for sys_name in ALL_SYS:
            items = q_pool['results'].get(sys_name, [])[:5]
            c_scores = [chunk_scores.get(it['chunk_id'], 0) for it in items]
            s_scores = [source_scores.get(it.get('source', ''), 0) for it in items]
            sys_chunk_ndcg[sys_name]  = ndcg_at_5(c_scores)
            sys_source_ndcg[sys_name] = ndcg_at_5(s_scores)
            results_by_sys[sys_name].append(sys_chunk_ndcg[sys_name])

        judged_queries.append({
            'query':       query,
            'type':        q_pool.get('type', ''),
            'chunk_ndcg5': sys_chunk_ndcg,
            'source_ndcg5':sys_source_ndcg,
        })

    # Aggregate
    def avg(lst): return round(sum(lst)/len(lst), 4) if lst else 0.0

    print("\n\n## Realistic Track — LLM Judge Results (Christianity / KJV)\n")
    print(f"{'System':<22} {'Chunk nDCG@5':>14} {'Source nDCG@5':>14}")
    print("-" * 52)
    summary = []
    for s in ALL_SYS:
        c_ndcg = avg(results_by_sys[s])
        s_ndcg = avg([q['source_ndcg5'].get(s, 0) for q in judged_queries])
        print(f"{s:<22} {c_ndcg:>14.4f} {s_ndcg:>14.4f}")
        summary.append({'system': s, 'chunk_ndcg5': c_ndcg, 'source_ndcg5': s_ndcg})

    # Save outputs
    (HERE / "judge_results.json").write_text(
        json.dumps({'summary': summary, 'per_query': judged_queries}, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )

    report = ["# Christianity Retrieval Evaluation — Realistic Track (LLM Judge)\n"]
    report.append(f"N = {len(positives)} positive queries | Judge: {JUDGE_MODEL}\n")
    report.append("\n| System | Chunk nDCG@5 | Source nDCG@5 |")
    report.append("|---|---|---|")
    for s_row in summary:
        report.append(f"| {s_row['system']} | {s_row['chunk_ndcg5']} | {s_row['source_ndcg5']} |")
    (HERE / "judge_report.md").write_text('\n'.join(report), encoding='utf-8')
    print(f"\nWrote judge_results.json and judge_report.md")


if __name__ == '__main__':
    main()
