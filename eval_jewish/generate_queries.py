# -*- coding: utf-8 -*-
"""
Generate query set for Jewish corpus retrieval evaluation.
Covers: Tanakh (JPS 1917, tanakh-jps1917) and Mishnah (Silverstein, mishnah-silverstein).

Samples document_chunks per corpus, uses Claude Haiku to produce
(natural, paraphrase) English query pairs, and appends hand-crafted
realistic queries covering Tanakh, Mishnah, and cross-corpus topics.

Output: eval_jewish/queries.jsonl
Run:    python eval_jewish/generate_queries.py
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import anthropic
import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

HERE = Path(__file__).resolve().parent
OUT  = HERE / "queries.jsonl"

random.seed(42)

JEWISH_CORPORA     = ["tanakh-jps1917", "mishnah-silverstein"]
SAMPLES_PER_CORPUS = 25     # 25 × 2 corpora × 2 queries each = 100 synthetic queries

TANAKH_QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for the Tanakh (Hebrew Bible),
using the JPS 1917 English translation, chunked at the chapter level.

Below is a passage from {book}, {reference}.
Write EXACTLY two questions IN ENGLISH that a scholar or student of scripture might ask:
1. NATURAL: A direct question that this specific passage answers. Reference the book, chapter, or topic.
2. PARAPHRASE: The same information need, rephrased with different wording and no shared key phrases.

Rules:
- Questions must be answerable from this specific passage, not from general biblical knowledge.
- Reference the specific book, chapter, or topic — not "the passage" or "this text."
- Use transliterated Hebrew terms where helpful (e.g., Torah, Mitzvot, Shabbat, Covenant, Chesed).
- English output only. No quotation marks around questions.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""

MISHNAH_QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for the Mishnah corpus
(Silverstein English translation with Bartenura commentary, chunked one mishnah per chunk).

Below is a mishnah from tractate {tractate} ({reference}).
Write EXACTLY two questions IN ENGLISH that a student of Jewish law or a curious reader might ask:
1. NATURAL: A direct question that this specific mishnah answers. Be specific about the topic or ruling.
2. PARAPHRASE: The same information need, rephrased with different wording and no shared key phrases.

Rules:
- Questions must be answerable from this specific mishnah, not from general halakhic knowledge.
- Reference the specific topic, legal ruling, or practice — not "the mishnah" or "this passage."
- Use relevant Hebrew terms where helpful (e.g., Shabbat, Kashrut, Eruv, Beit Din, Tefillah, Tzedakah).
- English output only. No quotation marks around questions.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Mishnah text ({reference}):
{text}
"""

# ── Realistic queries ─────────────────────────────────────────────────────────
REALISTIC = [
    # Torah: Creation / covenant (2)
    {"id": "real-t-01", "category": "tanakh_doctrinal",
     "query": "What does Genesis say about the creation of the world and the unique role of humanity within it?"},
    {"id": "real-t-02", "category": "tanakh_doctrinal",
     "query": "How is the covenant between God and Abraham described in the Torah, and what does it require of Abraham's descendants?"},
    # Torah: Revelation / law (2)
    {"id": "real-t-03", "category": "tanakh_doctrinal",
     "query": "What does Exodus say about the theophany at Mount Sinai and the content of the Ten Commandments?"},
    {"id": "real-t-04", "category": "tanakh_doctrinal",
     "query": "How does the Torah describe the sanctity of the Sabbath — where is it commanded and what does observance entail?"},
    # Prophets: Messianic / ethical (3)
    {"id": "real-t-05", "category": "tanakh_prophets",
     "query": "How does the book of Isaiah describe the future redemption of Israel and the prophetic vision of peace at the end of days?"},
    {"id": "real-t-06", "category": "tanakh_prophets",
     "query": "What does Jeremiah say about the new covenant that God will establish with the house of Israel?"},
    {"id": "real-t-07", "category": "tanakh_prophets",
     "query": "How does the prophet Amos describe God's demand for social justice and his critique of Israel's hollow religious observance?"},
    # Writings: Wisdom / poetry (3)
    {"id": "real-t-08", "category": "tanakh_writings",
     "query": "What does the book of Psalms say about God's protection of the righteous and the experience of divine nearness?"},
    {"id": "real-t-09", "category": "tanakh_writings",
     "query": "What does Proverbs teach about wisdom — its source, its value, and the qualities of a truly wise person?"},
    {"id": "real-t-10", "category": "tanakh_writings",
     "query": "How does the book of Job explore the problem of innocent suffering and challenge conventional views of divine justice?"},
    # Mishnah: Prayer / Shabbat / ethics (3)
    {"id": "real-m-01", "category": "mishnah_law",
     "query": "What does the Mishnah teach about the recitation of the Shema — when must it be said and what constitutes valid fulfillment?"},
    {"id": "real-m-02", "category": "mishnah_law",
     "query": "How does the Mishnah define the thirty-nine main categories of labor forbidden on the Sabbath?"},
    {"id": "real-m-03", "category": "mishnah_ethics",
     "query": "What does Pirkei Avot teach about Torah study, acquiring a teacher, and the qualities of a person who fears sin?"},
    # Mishnah: Civil law (2)
    {"id": "real-m-04", "category": "mishnah_law",
     "query": "How does the Mishnah describe the obligation to return lost property — to whom must it be returned and under what conditions?"},
    {"id": "real-m-05", "category": "mishnah_law",
     "query": "What does the Mishnah say about ona'ah (price fraud or overcharging) and the halakhic principles governing fair market dealings?"},
    # Cross-corpus (3)
    {"id": "real-x-01", "category": "cross_corpus",
     "query": "What does Jewish scripture teach about honoring one's parents — both the biblical commandment and how the Mishnah interprets it?"},
    {"id": "real-x-02", "category": "cross_corpus",
     "query": "How do the Tanakh and the Mishnah together address the obligation to care for the poor, the widow, and the stranger?"},
    {"id": "real-x-03", "category": "cross_corpus",
     "query": "What does Jewish tradition teach about repentance (teshuvah) — in both the prophetic writings and Mishnaic halakha?"},
    # Negative / out-of-scope (2)
    {"id": "real-neg-01", "category": "negative",
     "query": "What does the Tanakh say about nuclear energy, artificial intelligence, and the ethics of modern technology?",
     "note": "out-of-scope — anachronistic topics not in the biblical corpus"},
    {"id": "real-neg-02", "category": "negative",
     "query": "What Buddhist and Islamic teachings about the nature of God appear in the Tanakh or Mishnah?",
     "note": "out-of-scope — Buddhist and Islamic texts are not in this Jewish corpus"},
]


def connect() -> psycopg2.extensions.connection:
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    return psycopg2.connect(url)


def sample_chunks_for_corpus(conn, corpus_code: str, n: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT dc.id, dc.text_id, dc.chunk_text, dc.reference, dc.chapter, dc.section,
               ct.title_english, ct.title_original, ct.collection, ct.tradition, ct.language,
               sc.code AS corpus_code
        FROM document_chunks dc
        JOIN canon_texts ct ON ct.id = dc.text_id
        JOIN source_corpora sc ON sc.id = ct.corpus_id
        WHERE sc.code = %s
          AND dc.token_count >= 40
        ORDER BY RANDOM()
        LIMIT %s
    """, (corpus_code, n))
    rows = cur.fetchall()
    return [{
        "chunk_id":       r[0],
        "text_id":        r[1],
        "chunk_text":     r[2],
        "reference":      r[3] or "",
        "chapter":        r[4] or "",
        "section":        r[5] or "",
        "title_english":  r[6] or "",
        "title_original": r[7] or "",
        "collection":     r[8] or "",
        "tradition":      r[9],
        "language":       r[10],
        "corpus_code":    r[11],
    } for r in rows]


def generate_pair(client: anthropic.Anthropic, chunk: dict) -> tuple[str, str] | None:
    corpus = chunk.get("corpus_code", "")
    if corpus == "tanakh-jps1917":
        book   = chunk["title_english"] or chunk["collection"] or "Unknown Book"
        prompt = TANAKH_QUERY_PROMPT.format(
            book=book,
            reference=chunk["reference"],
            text=chunk["chunk_text"][:1500],
        )
    else:  # mishnah-silverstein
        tractate = chunk["title_english"] or chunk["chapter"] or "Unknown Tractate"
        prompt   = MISHNAH_QUERY_PROMPT.format(
            tractate=tractate,
            reference=chunk["reference"],
            text=chunk["chunk_text"][:1500],
        )

    for attempt in range(5):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            m   = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                raise ValueError("no JSON in response")
            obj = json.loads(m.group())
            return obj["natural"].strip(), obj["paraphrase"].strip()
        except anthropic.RateLimitError:
            wait = 20 * (attempt + 1)
            print(f"    rate limit — waiting {wait}s")
            time.sleep(wait)
        except Exception as e:
            if attempt < 4:
                time.sleep(3)
            else:
                print(f"    failed after 5 attempts: {e}")
                return None


def main() -> None:
    conn   = connect()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    all_samples: list[dict] = []
    for corpus_code in JEWISH_CORPORA:
        samples = sample_chunks_for_corpus(conn, corpus_code, SAMPLES_PER_CORPUS)
        print(f"  {corpus_code}: sampled {len(samples)} chunks")
        all_samples.extend(samples)

    random.shuffle(all_samples)
    print(f"Total: {len(all_samples)} chunks across {len(JEWISH_CORPORA)} corpora\n")

    queries = []
    for i, s in enumerate(all_samples):
        label = s["title_english"] or s["collection"]
        print(f"  [{i+1:03d}/{len(all_samples)}] {s['corpus_code']:<22} "
              f"{s['reference']:<22} {label[:28]}", flush=True)
        pair = generate_pair(client, s)
        if pair is None:
            continue
        nat, para = pair
        base = {
            "track":          "synthetic",
            "gold_chunk_ids": [s["chunk_id"]],
            "gold_text_ids":  [s["text_id"]],
            "tradition":      s["tradition"],
            "corpus_code":    s["corpus_code"],
            "title":          s["title_english"],
            "book":           s["chapter"],
            "reference":      s["reference"],
            "section":        s["section"],
            "collection":     s["collection"],
            "source_snippet": s["chunk_text"][:120],
            "needs_judge":    False,
        }
        queries.append({"id": f"syn-{i+1:03d}n", "mode": "natural",    "query": nat,  **base})
        queries.append({"id": f"syn-{i+1:03d}p", "mode": "paraphrase", "query": para, **base})
        time.sleep(0.3)

    for r in REALISTIC:
        queries.append({
            "id":          r["id"],
            "track":       "realistic",
            "category":    r.get("category"),
            "query":       r["query"],
            "needs_judge": True,
            "note":        r.get("note"),
        })

    conn.close()
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    syn  = sum(1 for q in queries if q["track"] == "synthetic")
    real = sum(1 for q in queries if q["track"] == "realistic")
    print(f"\nWrote {len(queries)} queries → {OUT}")
    print(f"  synthetic : {syn} ({syn // 2} chunk pairs across {len(JEWISH_CORPORA)} corpora)")
    print(f"  realistic : {real} (Tanakh + Mishnah + cross-corpus + negative, needs LLM judge)")


if __name__ == "__main__":
    main()
