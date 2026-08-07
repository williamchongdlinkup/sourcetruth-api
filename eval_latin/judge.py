# -*- coding: utf-8 -*-
"""
LLM-as-judge for Classical Latin corpus realistic queries.
Run: python eval_latin/judge.py
Prereq: python eval_latin/score.py
"""
from __future__ import annotations
import json, math, os, re, sys, time
from pathlib import Path
import anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
HERE        = Path(__file__).resolve().parent
JUDGE_MODEL = "claude-haiku-4-5-20251001"
ALL_SYS     = ("dense", "dense_text_dedup", "dense_two_level", "dense_mmr", "hybrid", "fts")

RELEVANCE_PROMPT = """\
You are evaluating a retrieval system for Classical Latin literature
(Caesar, Virgil, Lucretius, Tacitus, Cicero — 19th-century PD translations).

Query: {query}

Retrieved passage from "{source}", {reference}:
---
{text}
---

Rate relevance on a 0–2 scale:
  2 = Highly relevant — directly addresses or answers the query
  1 = Partially relevant — related topic but does not fully answer
  0 = Not relevant

Respond with ONLY: {{"score": <0|1|2>, "reason": "<one sentence>"}}
"""

SOURCE_RELEVANCE_PROMPT = """\
You are evaluating a retrieval system for Classical Latin literature.
Query: {query}
Source: "{source}"
Representative excerpt:
---
{text}
---
Rate whether this source text is relevant on a 0–2 scale:
  2 = Highly relevant    1 = Partially relevant    0 = Not relevant
Respond with ONLY: {{"score": <0|1|2>, "reason": "<one sentence>"}}
"""


def ndcg_at_5(scores):
    dcg  = sum(s / math.log2(i + 2) for i, s in enumerate(scores[:5]))
    idcg = sum(s / math.log2(i + 2) for i, s in enumerate(sorted(scores, reverse=True)[:5]))
    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def judge_passage(client, query, text, reference, source):
    prompt = RELEVANCE_PROMPT.format(query=query, text=text[:800], reference=reference or '', source=source or '')
    for attempt in range(3):
        try:
            msg = client.messages.create(model=JUDGE_MODEL, max_tokens=128, messages=[{"role": "user", "content": prompt}])
            m = re.search(r'\{.*\}', msg.content[0].text, re.DOTALL)
            if m: return int(json.loads(m.group()).get('score', 0))
        except Exception:
            if attempt < 2: time.sleep(3)
    return 0


def judge_source(client, query, text, source):
    prompt = SOURCE_RELEVANCE_PROMPT.format(query=query, text=text[:800], source=source or '')
    for attempt in range(3):
        try:
            msg = client.messages.create(model=JUDGE_MODEL, max_tokens=128, messages=[{"role": "user", "content": prompt}])
            m = re.search(r'\{.*\}', msg.content[0].text, re.DOTALL)
            if m: return int(json.loads(m.group()).get('score', 0))
        except Exception:
            if attempt < 2: time.sleep(3)
    return 0


def main():
    pool_path = HERE / "realistic_pool.json"
    if not pool_path.exists(): raise SystemExit("Run score.py first.")
    pool   = json.loads(pool_path.read_text(encoding='utf-8'))
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    results_by_sys = {s: [] for s in ALL_SYS}
    judged_queries = []
    positives = [q for q in pool if 'negative' not in q.get('type', '')]
    print(f"Judging {len(positives)} positive queries ...")

    for qi, q_pool in enumerate(positives):
        query = q_pool['query']
        print(f"\n[{qi+1}/{len(positives)}] {query[:70]}")
        all_candidates = {}
        for sys_name in ALL_SYS:
            for item in q_pool['results'].get(sys_name, []):
                cid = item['chunk_id']
                if cid and cid not in all_candidates: all_candidates[cid] = item
        chunk_scores  = {}
        source_scores = {}
        for cid, item in all_candidates.items():
            chunk_scores[cid] = judge_passage(client, query, item.get('text', ''), item.get('reference', ''), item.get('source', ''))
            src = item.get('source', '').split(' — ')[0]
            if src and src not in source_scores:
                source_scores[src] = judge_source(client, query, item.get('text', ''), src)
            print(f"  {item.get('source','')[:35]}: {chunk_scores[cid]}")
        sys_chunk = {}; sys_source = {}
        for sys_name in ALL_SYS:
            items    = q_pool['results'].get(sys_name, [])[:5]
            c_scores = [chunk_scores.get(it['chunk_id'], 0) for it in items]
            s_scores = [source_scores.get(it.get('source', '').split(' — ')[0], 0) for it in items]
            sys_chunk[sys_name]  = ndcg_at_5(c_scores)
            sys_source[sys_name] = ndcg_at_5(s_scores)
            results_by_sys[sys_name].append(sys_chunk[sys_name])
        judged_queries.append({'query': query, 'type': q_pool.get('type', ''),
                                'chunk_ndcg5': sys_chunk, 'source_ndcg5': sys_source})

    def avg(lst): return round(sum(lst)/len(lst), 4) if lst else 0.0
    print("\n\n## Classical Latin — LLM Judge Results\n")
    print(f"{'System':<22} {'Chunk nDCG@5':>14} {'Source nDCG@5':>14}")
    print("-" * 52)
    summary = []
    for s in ALL_SYS:
        c  = avg(results_by_sys[s])
        s2 = avg([q['source_ndcg5'].get(s, 0) for q in judged_queries])
        print(f"{s:<22} {c:>14.4f} {s2:>14.4f}")
        summary.append({'system': s, 'chunk_ndcg5': c, 'source_ndcg5': s2})

    (HERE / "judge_results.json").write_text(json.dumps({'summary': summary, 'per_query': judged_queries}, indent=2, ensure_ascii=False), encoding='utf-8')
    report = ["# Classical Latin Retrieval Evaluation — Realistic Track (LLM Judge)\n",
              f"N = {len(positives)} positive queries | Judge: {JUDGE_MODEL}\n",
              "\n| System | Chunk nDCG@5 | Source nDCG@5 |", "|---|---|---|"]
    for row in summary:
        report.append(f"| {row['system']} | {row['chunk_ndcg5']} | {row['source_ndcg5']} |")
    (HERE / "judge_report.md").write_text('\n'.join(report), encoding='utf-8')
    print(f"\nWrote judge_results.json and judge_report.md")


if __name__ == '__main__':
    main()
