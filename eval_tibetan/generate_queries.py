# -*- coding: utf-8 -*-
"""Generate retrieval eval queries for Tibetan Buddhist corpus."""
from __future__ import annotations
import json, os, random, re, sys, time
from pathlib import Path
import anthropic, psycopg2, psycopg2.extras
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
HERE = Path(__file__).resolve().parent
OUT  = HERE / "queries.jsonl"
random.seed(42)
CORPORA = ["tibetan-buddhist"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval benchmark for Tibetan Buddhist texts
(Bardo Thodol / Tibetan Book of the Dead, Evans-Wentz translation, 1927).

Below is a passage ({reference}).
Write EXACTLY two questions a Buddhist scholar, practitioner, or student of consciousness might ask:
1. NATURAL: A direct question this passage answers about the bardo, death, or liberation.
2. PARAPHRASE: Same meaning, different vocabulary.

Rules:
- Questions must be answerable from this passage.
- Reference bardo states, consciousness, liberation, or specific teachings.
- English output only. No quotation marks in questions.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    {"query": "What happens to consciousness immediately after death according to the Tibetan Book of the Dead?", "type": "death_consciousness"},
    {"query": "What are the different bardo states described in Tibetan Buddhist teachings?", "type": "bardo_states"},
    {"query": "How should a dying person be guided through the bardo experience?", "type": "guidance_dying"},
    {"query": "What is the Clear Light that appears at the moment of death in Tibetan Buddhist teaching?", "type": "clear_light"},
    {"query": "How does the Bardo Thodol describe the peaceful and wrathful deities encountered after death?", "type": "deities_bardo"},
    {"query": "What is the teaching on recognizing the nature of mind during the bardo of dying?", "type": "nature_of_mind"},
    {"query": "How does the Tibetan Book of the Dead describe the Dharma-Dhatu body of light?", "type": "dharmakaya"},
    {"query": "What preparations does Tibetan Buddhism recommend for the moment of death?", "type": "death_preparation"},
    {"query": "How does the Bardo Thodol describe rebirth and the choosing of a new incarnation?", "type": "rebirth"},
    {"query": "What role does the lama or guru play in guiding the consciousness after death?", "type": "lama_role"},
    {"query": "What sounds and lights does the dying person encounter in the bardo states?", "type": "sounds_lights"},
    {"query": "How does Tibetan Buddhist teaching explain the karma that determines rebirth?", "type": "karma_rebirth"},
    {"query": "What is the significance of the 49-day period after death in Tibetan Buddhism?", "type": "49_days"},
    {"query": "How does the Bardo Thodol describe the experience of the Samboghakaya state?", "type": "sambhogakaya"},
    {"query": "What meditation practices help one recognize the bardo states and achieve liberation?", "type": "meditation_liberation"},
    {"query": "How does the Tibetan Book of the Dead describe the journey through the Sidpa Bardo?", "type": "sidpa_bardo"},
    {"query": "What does the Tibetan Book of the Dead say about smartphones and social media?", "type": "negative"},
    {"query": "Which passage describes the stock market crash of 1929?", "type": "negative"},
]


def connect():
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def sample_chunks(conn, corpus, n):
    cur = conn.cursor()
    cur.execute("""
        SELECT dc.id, dc.chunk_text AS text, dc.reference, dc.chapter, sc.code
        FROM document_chunks dc
        JOIN canon_texts ct ON ct.id = dc.text_id
        JOIN source_corpora sc ON sc.id = ct.corpus_id
        WHERE sc.code = %s AND dc.word_count > 40
        ORDER BY random() LIMIT %s
    """, (corpus, n))
    return cur.fetchall()


def gen_pair(client, row):
    prompt = QUERY_PROMPT.format(
        reference=row["reference"] or row["chapter"] or "",
        text=row["text"][:1200],
    )
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                d = json.loads(m.group())
                if d.get("natural") and d.get("paraphrase"):
                    return d
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
    return None


def main():
    conn   = connect()
    client = anthropic.Anthropic()
    rows: list[dict] = []

    for corpus in CORPORA:
        chunks = sample_chunks(conn, corpus, SAMPLES_PER_CORPUS)
        print(f"{corpus}: {len(chunks)} chunks sampled")
        for row in chunks:
            pair = gen_pair(client, row)
            if pair:
                rows.append({"chunk_id": row["id"], "corpus": corpus,
                             "reference": row["reference"] or "",
                             "natural": pair["natural"],
                             "paraphrase": pair["paraphrase"],
                             "type": "synthetic"})
            time.sleep(0.3)

    for q in REALISTIC_QUERIES:
        rows.append({"chunk_id": None, "corpus": "tibetan-buddhist",
                     "reference": None, "natural": q["query"],
                     "paraphrase": q["query"], "type": q["type"]})

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} queries → {OUT}")


if __name__ == "__main__":
    main()
