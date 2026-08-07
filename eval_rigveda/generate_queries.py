# -*- coding: utf-8 -*-
"""Generate retrieval eval queries for Rigveda corpus (Griffith 1896)."""
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
CORPORA = ["rigveda"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval benchmark for the Rigveda
(Griffith English translation, 1896; hymns to Vedic deities).

Below is a passage ({reference}).
Write EXACTLY two questions a Vedic scholar, Sanskrit student, or Hindu practitioner might ask:
1. NATURAL: A question this passage answers about a deity, ritual, or cosmic principle.
2. PARAPHRASE: Same information need, different wording.

Rules:
- Questions must be answerable from this passage.
- Reference the deity (Agni, Indra, Varuna, Soma, etc.), the hymn theme, or the mandala.
- English output only.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    {"query": "What are the hymns to Agni (fire deity) in the Rigveda and what do they teach?", "type": "agni"},
    {"query": "How does the Rigveda describe Indra's victory over the dragon Vritra?", "type": "indra_vritra"},
    {"query": "What does the Rigveda say about Varuna as the god of cosmic order (Rita)?", "type": "varuna_rita"},
    {"query": "How is Soma described and praised in the Rigveda hymns?", "type": "soma"},
    {"query": "What is the Purusha Sukta and what does it teach about the cosmic being?", "type": "purusha_sukta"},
    {"query": "How does the Rigveda describe Ushas, the goddess of dawn?", "type": "ushas"},
    {"query": "What does the Nasadiya Sukta (creation hymn) say about the origin of the universe?", "type": "nasadiya"},
    {"query": "How are the Ashvins (divine twins) described in Rigveda hymns?", "type": "ashvins"},
    {"query": "What role does Surya (sun god) play in the Rigveda's cosmology?", "type": "surya"},
    {"query": "How does the Rigveda describe the sacrifice (yajna) and its cosmic significance?", "type": "yajna"},
    {"query": "What hymns in the Rigveda address the concept of Rita (cosmic order/truth)?", "type": "rita"},
    {"query": "How does the Rigveda portray Vishnu's three strides across the universe?", "type": "vishnu"},
    {"query": "What does the Rigveda teach about the relationship between humans and the gods?", "type": "human_divine"},
    {"query": "How is Rudra (ancestor of Shiva) described in the Rigveda?", "type": "rudra"},
    {"query": "What does the Rigveda say about Mitra and Varuna as guardians of cosmic law?", "type": "mitra_varuna"},
    {"query": "How do Rigveda hymns describe the afterlife or realm of the ancestors (Pitrs)?", "type": "afterlife"},
    {"query": "Which hymn of the Rigveda describes the invention of the steam engine?", "type": "negative"},
    {"query": "What does the Rigveda say about democracy and voting rights?", "type": "negative"},
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
        rows.append({"chunk_id": None, "corpus": "rigveda",
                     "reference": None, "natural": q["query"],
                     "paraphrase": q["query"], "type": q["type"]})

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} queries → {OUT}")


if __name__ == "__main__":
    main()
