# -*- coding: utf-8 -*-
"""
Generate retrieval eval queries for Classical Latin corpus.
Covers: classical-latin (Caesar, Virgil, Lucretius, Tacitus, Cicero)
Output: eval_latin/queries.jsonl
Run:    python eval_latin/generate_queries.py
"""
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

CORPORA = ["classical-latin"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval benchmark for Classical Latin literature
(Caesar, Virgil, Lucretius, Tacitus, Cicero — 19th-century Public Domain translations).

Below is a passage from "{source}" ({reference}).
Write EXACTLY two questions a classics scholar, philosophy student, or history student might ask:
1. NATURAL: A direct question this passage answers. Reference author, work, or concept.
2. PARAPHRASE: The same information need, rephrased differently.

Rules:
- Questions must be answerable from this specific passage.
- Reference author names, work titles, or themes (virtue, fate, Roman politics, nature, Stoicism).
- English output only. No quotation marks around questions.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage from {source} ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    # Caesar
    {"query": "How does Caesar describe the geography and tribes of Gaul at the start of his commentaries?", "type": "caesar_gaul"},
    {"query": "What was Caesar's strategy for dealing with the Helvetii tribe in the Gallic Wars?", "type": "caesar_helvetii"},
    {"query": "How does Caesar portray himself as a leader and general in De Bello Gallico?", "type": "caesar_leadership"},
    # Virgil
    {"query": "How does Virgil describe the fall of Troy in the Aeneid?", "type": "virgil_troy"},
    {"query": "What role do the gods play in guiding Aeneas in the Aeneid?", "type": "virgil_gods"},
    {"query": "How does Dido's love for Aeneas lead to her tragic end in the Aeneid?", "type": "virgil_dido"},
    {"query": "What is the prophetic vision of Rome's future that Anchises reveals to Aeneas in the underworld?", "type": "virgil_rome"},
    # Lucretius
    {"query": "What does Lucretius say about the nature of atoms and the composition of matter?", "type": "lucretius_atoms"},
    {"query": "How does Lucretius argue that death should not be feared?", "type": "lucretius_death"},
    {"query": "What is Lucretius's Epicurean account of the origin of the universe and life?", "type": "lucretius_origin"},
    # Tacitus
    {"query": "How does Tacitus portray the character of Emperor Tiberius?", "type": "tacitus_tiberius"},
    {"query": "What does Tacitus say about the political intrigues at the court of Tiberius?", "type": "tacitus_politics"},
    # Cicero
    {"query": "What does Cicero argue about the nature of moral duty in De Officiis?", "type": "cicero_duty"},
    {"query": "How does Cicero define the relationship between honesty and expediency in his ethics?", "type": "cicero_honesty"},
    {"query": "What does Cicero say about the importance of friendship in Roman life?", "type": "cicero_friendship"},
    # Cross-author
    {"query": "How do Roman authors discuss the concept of fate and divine will in human affairs?", "type": "cross_fate"},
    {"query": "What do Latin authors say about virtue and the good life?", "type": "cross_virtue"},
    {"query": "How is Roman civic duty portrayed across different Latin works?", "type": "cross_civic"},
    # Negatives
    {"query": "What do Latin authors say about quantum physics and computing?", "type": "negative"},
    {"query": "Which Roman writer described the invention of printing presses?", "type": "negative"},
]


def connect():
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def main():
    conn   = connect()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    out    = open(OUT, 'w', encoding='utf-8')
    total  = 0

    for corpus in CORPORA:
        cur = conn.cursor()
        cur.execute("""
            SELECT dc.id, dc.chunk_text, dc.reference, ct.title_english AS source_title
            FROM document_chunks dc
            JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = %s
            ORDER BY random() LIMIT %s
        """, (corpus, SAMPLES_PER_CORPUS))
        rows = cur.fetchall()
        print(f"{corpus}: sampling {len(rows)} chunks")

        for row in rows:
            source = (row['source_title'] or '').split(' — ')[0]
            prompt = QUERY_PROMPT.format(
                source=source, reference=row['reference'] or '',
                text=(row['chunk_text'] or '')[:1200],
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
                            out.write(json.dumps({'query': q, 'query_type': f'synthetic_{qtype}',
                                                  'corpus': corpus, 'chunk_id': row['id'],
                                                  'reference': row['reference']},
                                                 ensure_ascii=False) + '\n')
                            total += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(3)
                    else:
                        print(f"  [WARN] chunk {row['id']}: {e}")

    for rq in REALISTIC_QUERIES:
        out.write(json.dumps({'query': rq['query'], 'query_type': f"realistic_{rq['type']}",
                              'corpus': 'classical-latin', 'chunk_id': None, 'reference': None},
                             ensure_ascii=False) + '\n')
        total += 1

    out.close()
    conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
