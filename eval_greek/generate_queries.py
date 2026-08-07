# -*- coding: utf-8 -*-
"""
Generate query set for Greek Classical Philosophy corpus retrieval evaluation.
Covers: greek-philosophy (Marcus Aurelius, Epictetus, Plato, Aristotle).

Output: eval_greek/queries.jsonl
Run:    python eval_greek/generate_queries.py
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
import psycopg2.extras
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

HERE = Path(__file__).resolve().parent
OUT  = HERE / "queries.jsonl"

random.seed(42)

GREEK_CORPORA      = ["greek-philosophy"]
SAMPLES_PER_CORPUS = 60

QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for Greek classical philosophy,
covering Marcus Aurelius, Epictetus, Plato, and Aristotle.

Below is a passage from "{source}" ({reference}).
Write EXACTLY two questions a philosophy student, Stoic practitioner, or classics scholar might ask:
1. NATURAL: A direct question this passage answers. Reference the author, work, or concept.
2. PARAPHRASE: The same information need, rephrased with different vocabulary.

Rules:
- Questions must be answerable from this specific passage.
- Reference author names, work titles, or philosophical concepts (virtue, eudaimonia, logos, Forms, etc.).
- English output only. No quotation marks around the question.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage from {source} ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    # Marcus Aurelius / Stoicism
    {"query": "What does Marcus Aurelius say about the impermanence of life and accepting fate?", "type": "stoic_fate"},
    {"query": "How does Marcus Aurelius advise dealing with anger and difficult people?", "type": "stoic_anger"},
    {"query": "What is Marcus Aurelius's teaching about living according to nature and reason?", "type": "stoic_nature"},
    {"query": "What does the Meditations of Marcus Aurelius say about the nature of the rational soul?", "type": "stoic_soul"},
    # Epictetus
    {"query": "What is Epictetus's doctrine about what is within our control and what is not?", "type": "epictetus_control"},
    {"query": "How does Epictetus describe the path to freedom through philosophy?", "type": "epictetus_freedom"},
    {"query": "What does the Enchiridion say about the proper response to external misfortune?", "type": "epictetus_misfortune"},
    # Plato
    {"query": "How does Plato's Apology depict Socrates defending his philosophical mission?", "type": "plato_apology"},
    {"query": "What does the Phaedo say about the immortality of the soul and the afterlife?", "type": "plato_immortality"},
    {"query": "How does Socrates argue that death should not be feared by a philosopher?", "type": "plato_death"},
    {"query": "What is Plato's theory of the Forms and how does it relate to knowledge?", "type": "plato_forms"},
    # Aristotle
    {"query": "How does Aristotle define happiness (eudaimonia) in the Nicomachean Ethics?", "type": "aristotle_eudaimonia"},
    {"query": "What does Aristotle say about the role of virtue in achieving the good life?", "type": "aristotle_virtue"},
    {"query": "How does Aristotle describe the relationship between reason and moral character?", "type": "aristotle_reason"},
    {"query": "What is Aristotle's doctrine of the mean between virtues and vices?", "type": "aristotle_mean"},
    # Plato Republic (new)
    {"query": "What is Plato's allegory of the cave and what does it represent about human knowledge?", "type": "plato_republic"},
    {"query": "How does Plato describe the three parts of the soul in the Republic?", "type": "plato_republic"},
    {"query": "What is the philosopher-king ideal in Plato's Republic?", "type": "plato_republic"},
    {"query": "How does Plato argue that justice in the city mirrors justice in the soul?", "type": "plato_republic"},
    # Plato Symposium (new)
    {"query": "What does Socrates say about the nature of love and its relation to the beautiful in the Symposium?", "type": "plato_symposium"},
    {"query": "How does Diotima describe the ladder of love leading to the Form of Beauty in the Symposium?", "type": "plato_symposium"},
    # Plato Meno/Timaeus (new)
    {"query": "What is Plato's doctrine of recollection (anamnesis) as described in the Meno?", "type": "plato_meno"},
    {"query": "How does Plato describe the creation of the world by the Demiurge in the Timaeus?", "type": "plato_timaeus"},
    # Aristotle Politics (new)
    {"query": "What does Aristotle say about humans being political animals in the Politics?", "type": "aristotle_politics"},
    {"query": "How does Aristotle classify different forms of government in the Politics?", "type": "aristotle_politics"},
    {"query": "What is Aristotle's argument for why slavery is natural in the Politics?", "type": "aristotle_politics"},
    # Aristotle Rhetoric (new)
    {"query": "How does Aristotle define rhetoric and its three modes of persuasion in the Rhetoric?", "type": "aristotle_rhetoric"},
    {"query": "What is Aristotle's account of ethos, pathos, and logos as means of persuasion?", "type": "aristotle_rhetoric"},
    # Cross-author
    {"query": "How do Stoic philosophers compare to Plato in their views on the soul?", "type": "cross_soul"},
    {"query": "What do ancient Greek philosophers say about the examined life and self-knowledge?", "type": "cross_self"},
    {"query": "How do Aristotle and the Stoics differ in their understanding of virtue?", "type": "cross_virtue"},
    {"query": "What do Plato and Aristotle say about the best form of government?", "type": "cross_politics"},
    # Negatives
    {"query": "What do Greek philosophers say about cryptocurrency and financial markets?", "type": "negative"},
    {"query": "Which ancient philosopher wrote about programming computers?", "type": "negative"},
]


def connect():
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def main():
    conn   = connect()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    out    = open(OUT, 'w', encoding='utf-8')
    total  = 0

    for corpus in GREEK_CORPORA:
        cur = conn.cursor()
        cur.execute("""
            SELECT dc.id, dc.chunk_text, dc.reference, dc.chapter,
                   ct.title_english AS source_title, ct.collection
            FROM document_chunks dc
            JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = %s
            ORDER BY random()
            LIMIT %s
        """, (corpus, SAMPLES_PER_CORPUS))
        rows = cur.fetchall()
        print(f"{corpus}: sampling {len(rows)} chunks for synthetic queries")

        for row in rows:
            source = (row['source_title'] or '').split(' — ')[0]  # strip translator
            prompt = QUERY_PROMPT.format(
                source    = source,
                reference = row['reference'] or row['chapter'] or '',
                text      = (row['chunk_text'] or '')[:1200],
            )
            for attempt in range(3):
                try:
                    msg = client.messages.create(
                        model="claude-haiku-4-5-20251001", max_tokens=256,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    raw = msg.content[0].text.strip()
                    m   = re.search(r'\{.*\}', raw, re.DOTALL)
                    if not m:
                        raise ValueError(f"No JSON: {raw[:80]}")
                    parsed = json.loads(m.group())
                    for qtype in ('natural', 'paraphrase'):
                        q = parsed.get(qtype, '').strip()
                        if q:
                            out.write(json.dumps({
                                'query':      q,
                                'query_type': f'synthetic_{qtype}',
                                'corpus':     corpus,
                                'chunk_id':   row['id'],
                                'reference':  row['reference'],
                            }, ensure_ascii=False) + '\n')
                            total += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(3)
                    else:
                        print(f"  [WARN] Failed for chunk {row['id']}: {e}")

    for rq in REALISTIC_QUERIES:
        out.write(json.dumps({
            'query':      rq['query'],
            'query_type': f"realistic_{rq['type']}",
            'corpus':     'greek-philosophy',
            'chunk_id':   None,
            'reference':  None,
        }, ensure_ascii=False) + '\n')
        total += 1

    out.close()
    conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
