# -*- coding: utf-8 -*-
"""
LLM-as-judge for Jewish corpus realistic queries.

Reads realistic_pool.json (produced by score.py), pools top-5 results from all
retrieval systems, judges each candidate for relevance at chunk and source text
level using Claude Haiku, then aggregates per-system nDCG@5 with qualitative analysis.

Output: judge_results.json, judge_report.md
Run:    python eval_jewish/judge.py
Prereq: python eval_jewish/score.py
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

RELEVANCE_PROMPT = """\
You are evaluating a retrieval system for a Jewish scriptural corpus
(Tanakh / Hebrew Bible in JPS 1917 English translation, and Mishnah in Silverstein English translation).

Query: {query}

Retrieved passage (from "{source}"{ref_part}):
---
{text}
---

Rate the relevance of this passage to the query on a 0–2 scale:
  2 = Highly relevant — directly addresses or strongly answers the query
  1 = Partially relevant — related topic but does not directly answer
  0 = Not relevant — off-topic

Respond with ONLY a JSON object: {{"score": <0|1|2>, "reason": "<one sentence>"}}
"""

SOURCE_RELEVANCE_PROMPT = """\
You are evaluating a retrieval system for a Jewish scriptural corpus
(Tanakh / Hebrew Bible and Mishnah).

Query: {query}

The system retrieved source text "{source}" (collection: {collection}).
A representative excerpt:
---
{text}
---

Rate whether this source text (book / tractate) as a whole is relevant
to the query on a 0–2 scale:
  2 = Highly relevant — directly addresses the query topic
  1 = Partially relevant — related but only partially covers the query
  0 = Not relevant — does not address the query

Respond with ONLY a JSON object: {{"score": <0|1|2>, "reason": "<one sentence>"}}
"""

FAILURE_PROMPT = """\
You evaluated a retrieval system for the Jewish corpus query: "{query}"

The top-5 results from the best system (Dense semantic retrieval) were:
{results}

Write a concise analysis (3–5 sentences) covering:
1. Whether the system found good answers, partial answers, or failed.
2. What error type occurred if retrieval failed — topic absent from corpus, \
wrong source retrieved, granularity mismatch (e.g. chapter vs. verse), \
vocabulary mismatch, or cross-corpus gap (Tanakh vs. Mishnah).
3. Any notable pattern (e.g. Tanakh results surfaced for a Mishnah query, \
or vice versa; whether cross-corpus queries pulled both corppora).

Be specific. Do not repeat the question.
"""


def call_claude(client: anthropic.Anthropic, prompt: str) -> str:
    for attempt in range(4):
        try:
            msg = client.messages.create(
                model=JUDGE_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except anthropic.RateLimitError:
            time.sleep(10 * (attempt + 1))
        except Exception as e:
            if attempt < 3:
                time.sleep(3)
            else:
                raise
    return ""


def judge_passage(client, query, source, reference, text) -> tuple[int, str]:
    ref_part = f", {reference}" if reference else ""
    prompt   = RELEVANCE_PROMPT.format(
        query=query, source=source, ref_part=ref_part, text=text[:800],
    )
    try:
        raw = call_claude(client, prompt)
        m   = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("no JSON")
        obj = json.loads(m.group())
        return int(obj["score"]), str(obj.get("reason", ""))
    except Exception as e:
        return 0, f"judge error: {e}"


def judge_source(client, query, source, collection, text) -> tuple[int, str]:
    prompt = SOURCE_RELEVANCE_PROMPT.format(
        query=query, source=source, collection=collection or "unknown", text=text[:600],
    )
    try:
        raw = call_claude(client, prompt)
        m   = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("no JSON")
        obj = json.loads(m.group())
        return int(obj["score"]), str(obj.get("reason", ""))
    except Exception as e:
        return 0, f"judge error: {e}"


def analyze_failure(client, query, results) -> str:
    result_text = "\n".join(
        f"{i+1}. [{r.get('collection','?')} | {r.get('title','')}] "
        f"{r.get('reference','')} — {r['text'][:200]}…"
        for i, r in enumerate(results[:5])
    )
    prompt = FAILURE_PROMPT.format(query=query, results=result_text)
    try:
        return call_claude(client, prompt)
    except Exception as e:
        return f"analysis error: {e}"


def ndcg_at_k(scores: list[int], k: int) -> float:
    dcg  = sum(s / math.log2(i + 2) for i, s in enumerate(scores[:k]))
    idcg = sum(2 / math.log2(i + 2) for i in range(min(sum(1 for s in scores if s > 0), k)))
    return round(dcg / idcg, 4) if idcg else 0.0


def main() -> None:
    pool_path = HERE / "realistic_pool.json"
    if not pool_path.exists():
        raise SystemExit("realistic_pool.json not found — run score.py first.")

    pool_data = json.loads(pool_path.read_text(encoding="utf-8"))
    client    = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    ALL_SYS       = ("dense", "dense_text_dedup", "dense_two_level", "dense_mmr", "hybrid", "fts")
    judge_results = {}

    for qid, data in pool_data.items():
        query    = data["query"]
        category = data.get("category", "")
        print(f"\n── {qid} [{category}]: {query[:70]}")

        seen, pool_candidates = set(), []
        for sys_name in ALL_SYS:
            for r in data["results"].get(sys_name, [])[:5]:
                if r["chunk_id"] not in seen:
                    seen.add(r["chunk_id"])
                    pool_candidates.append({**r, "first_seen_in": sys_name})

        judged = []
        for c in pool_candidates:
            source = c.get("title", "") or c.get("collection", "")
            score, reason = judge_passage(
                client, query,
                source,
                c.get("reference", ""),
                c["text"],
            )
            judged.append({**c, "relevance": score, "reason": reason})
            print(f"  chunk [{score}] {source[:40]} {c.get('reference','')} — {reason[:50]}")
            time.sleep(0.3)

        judged_by_cid = {j["chunk_id"]: j["relevance"] for j in judged}
        sys_scores = {}
        for sys_name in ALL_SYS:
            ranked = [judged_by_cid.get(r["chunk_id"], 0)
                      for r in data["results"].get(sys_name, [])[:5]]
            sys_scores[sys_name] = ndcg_at_k(ranked, 5)

        seen_txt, txt_candidates = set(), []
        for sys_name in ALL_SYS:
            for r in data["results"].get(sys_name, [])[:5]:
                tid = r["text_id"]
                if tid not in seen_txt:
                    seen_txt.add(tid)
                    txt_candidates.append({**r, "first_seen_in": sys_name})

        txt_judged = []
        for c in txt_candidates:
            source     = c.get("title", "") or c.get("collection", "")
            collection = c.get("collection", "")
            score, reason = judge_source(
                client, query, source, collection, c["text"],
            )
            txt_judged.append({**c, "relevance": score, "reason": reason})
            print(f"  source [{score}] {source[:40]} ({collection[:20]}) — {reason[:50]}")
            time.sleep(0.3)

        judged_by_tid = {j["text_id"]: j["relevance"] for j in txt_judged}
        txt_sys_scores = {}
        for sys_name in ALL_SYS:
            seen_s, txt_ranked = set(), []
            for r in data["results"].get(sys_name, []):
                tid = r["text_id"]
                if tid not in seen_s:
                    seen_s.add(tid)
                    txt_ranked.append(judged_by_tid.get(tid, 0))
                if len(txt_ranked) >= 5:
                    break
            txt_sys_scores[sys_name] = ndcg_at_k(txt_ranked, 5)

        dense_top5 = data["results"].get("dense", [])[:5]
        analysis   = analyze_failure(client, query, dense_top5)

        judge_results[qid] = {
            "query":    query,
            "category": category,
            "note":     data.get("note"),
            "is_negative": category == "negative",
            "candidates_judged":     len(judged),
            "sys_ndcg5":             sys_scores,
            "txt_sys_ndcg5":         txt_sys_scores,
            "judged_candidates":     judged,
            "txt_judged_candidates": txt_judged,
            "analysis":              analysis,
        }
        time.sleep(0.5)

    positive = [v for v in judge_results.values() if not v["is_negative"]]
    sys_agg, txt_agg = {}, {}
    for sys_name in ALL_SYS:
        chunk_vals = [v["sys_ndcg5"].get(sys_name, 0) for v in positive]
        txt_vals   = [v["txt_sys_ndcg5"].get(sys_name, 0) for v in positive]
        sys_agg[sys_name] = round(sum(chunk_vals) / len(chunk_vals), 4) if chunk_vals else 0.0
        txt_agg[sys_name] = round(sum(txt_vals)   / len(txt_vals),   4) if txt_vals   else 0.0

    output = {
        "aggregate_chunk_ndcg5": sys_agg,
        "aggregate_txt_ndcg5":   txt_agg,
        "per_query":             judge_results,
    }
    (HERE / "judge_results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_judge_report(output, HERE)

    print(f"\n── Chunk nDCG@5 | Source nDCG@5  (realistic, n={len(positive)} positive queries)")
    for sys_name in ALL_SYS:
        print(f"   {sys_name:<22} chunk={sys_agg[sys_name]:.4f}  source={txt_agg[sys_name]:.4f}")
    print("\nWrote judge_results.json  judge_report.md")


def _write_judge_report(output, out_dir):
    chunk_agg = output["aggregate_chunk_ndcg5"]
    txt_agg   = output["aggregate_txt_ndcg5"]
    pq        = output["per_query"]
    ALL_SYS   = ("dense", "dense_text_dedup", "dense_two_level", "dense_mmr", "hybrid", "fts")
    LABELS    = {
        "dense":            "Dense",
        "dense_text_dedup": "Dense (text-dedup)",
        "dense_two_level":  "Dense Two-Level",
        "dense_mmr":        "Dense+MMR",
        "hybrid":           "Hybrid RRF",
        "fts":              "FTS-only",
    }
    lines = [
        "## Jewish Corpus — Realistic Query Track: LLM-as-Judge Results",
        "",
        "**Chunk nDCG@5**: passage-level relevance (0–2 scale per chunk, nDCG@5).  ",
        "**Source nDCG@5**: source text (book/tractate) relevance judged independently (same scale).",
        "",
        "### Aggregate nDCG@5",
        "| System | Chunk nDCG@5 | Source nDCG@5 | Source−Chunk |",
        "|---|--:|--:|--:|",
    ]
    for sys_name in ALL_SYS:
        p = chunk_agg.get(sys_name, 0)
        a = txt_agg.get(sys_name, 0)
        lines.append(f"| {LABELS[sys_name]} | {p:.4f} | {a:.4f} | {a - p:+.4f} |")
    lines += ["", "### Per-query results", ""]

    for qid, d in pq.items():
        tag = " *(negative / out-of-scope)*" if d["is_negative"] else ""
        lines += [
            f"#### {qid}: {d['query']}{tag}",
            f"*Category: {d['category']}*  |  **Analysis**: {d.get('analysis', '')}",
            "",
            "| System | Chunk nDCG@5 | Source nDCG@5 |",
            "|---|--:|--:|",
        ]
        for sys_name in ALL_SYS:
            p = d["sys_ndcg5"].get(sys_name, 0)
            a = d.get("txt_sys_ndcg5", {}).get(sys_name, 0)
            lines.append(f"| {LABELS[sys_name]} | {p:.4f} | {a:.4f} |")
        lines += ["", "Top-5 Dense candidates:", ""]
        for c in d.get("judged_candidates", [])[:5]:
            ref    = c.get("reference", "")
            source = c.get("title", "") or c.get("collection", "")
            coll   = c.get("collection", "")
            lines.append(
                f"- [**{c['relevance']}**] *{source[:50]}*"
                f"{' (' + ref + ')' if ref else ''}"
                f" [{coll}]"
                f" — {c['text'][:120]}…"
            )
        lines.append("")

    (out_dir / "judge_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
