# -*- coding: utf-8 -*-
"""Generate retrieval eval queries for Classical Chinese corpus."""
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
CORPORA = ["classical-chinese"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval benchmark for Classical Chinese philosophy
(Confucius Analects, Tao Te Ching, Shih King, Art of War — James Legge / Lionel Giles PD translations).

Below is a passage from "{source}" ({reference}).
Write EXACTLY two questions a philosophy student or scholar might ask:
1. NATURAL: A direct question this passage answers. Reference the author, work, or concept.
2. PARAPHRASE: The same information need, rephrased differently.

Rules:
- Questions must be answerable from this specific passage.
- Reference author names, work titles, or concepts (ren/benevolence, Tao, li/ritual, wu-wei, strategy).
- English output only. No quotation marks.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage from {source} ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    {"query": "What does Confucius say about the qualities of a junzi (gentleman/superior man)?", "type": "confucius_junzi"},
    {"query": "How does the Analects describe the relationship between ren (benevolence) and ritual?", "type": "confucius_ren"},
    {"query": "What is Confucius's teaching about filial piety and respect for parents?", "type": "confucius_filial"},
    {"query": "How does Confucius describe the ideal ruler and good governance?", "type": "confucius_ruler"},
    {"query": "What does Confucius say about the importance of learning and self-cultivation?", "type": "confucius_learning"},
    {"query": "How does the Tao Te Ching describe the nature of the Tao (Way)?", "type": "tao_nature"},
    {"query": "What does Laozi say about wu-wei (non-action) as a principle of governance?", "type": "tao_wuwei"},
    {"query": "How does the Tao Te Ching describe the relationship between opposites like hard and soft?", "type": "tao_opposites"},
    {"query": "What is the Tao Te Ching's teaching about water as a metaphor for the Tao?", "type": "tao_water"},
    {"query": "What does Sun Tzu say about the importance of knowing your enemy?", "type": "sunzi_enemy"},
    {"query": "How does Sun Tzu describe the five essential qualities of a general?", "type": "sunzi_general"},
    {"query": "What is Sun Tzu's teaching on deception as a military strategy?", "type": "sunzi_deception"},
    {"query": "What are the nine variations of tactics described in the Art of War?", "type": "sunzi_tactics"},
    {"query": "How does the Shih King portray nature and seasonal cycles in its odes?", "type": "shih_nature"},
    {"query": "What themes of love and courtship appear in the Book of Poetry?", "type": "shih_love"},
    {"query": "How do Confucian and Taoist philosophies differ in their approach to society?", "type": "cross_confucian_tao"},
    {"query": "What Chinese philosophical texts discuss the nature of virtue and moral character?", "type": "cross_virtue"},
    {"query": "How is political leadership described across different classical Chinese texts?", "type": "cross_leadership"},
    {"query": "What do Chinese philosophers say about cryptocurrency and modern finance?", "type": "negative"},
    {"query": "Which classical Chinese text discusses quantum computing?", "type": "negative"},
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
            FROM document_chunks dc JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = %s ORDER BY random() LIMIT %s
        """, (corpus, SAMPLES_PER_CORPUS))
        rows = cur.fetchall()
        print(f"{corpus}: sampling {len(rows)} chunks")
        for row in rows:
            source = (row['source_title'] or '').split(' — ')[0]
            prompt = QUERY_PROMPT.format(source=source, reference=row['reference'] or '',
                                          text=(row['chunk_text'] or '')[:1200])
            for attempt in range(3):
                try:
                    msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=256,
                                                  messages=[{"role": "user", "content": prompt}])
                    raw = msg.content[0].text.strip()
                    m   = re.search(r'\{.*\}', raw, re.DOTALL)
                    if not m: raise ValueError(f"No JSON: {raw[:80]}")
                    parsed = json.loads(m.group())
                    for qtype in ('natural', 'paraphrase'):
                        q = parsed.get(qtype, '').strip()
                        if q:
                            out.write(json.dumps({'query': q, 'query_type': f'synthetic_{qtype}',
                                                  'corpus': corpus, 'chunk_id': row['id'],
                                                  'reference': row['reference']}, ensure_ascii=False) + '\n')
                            total += 1
                    break
                except Exception as e:
                    if attempt < 2: time.sleep(3)
                    else: print(f"  [WARN] chunk {row['id']}: {e}")

    for rq in REALISTIC_QUERIES:
        out.write(json.dumps({'query': rq['query'], 'query_type': f"realistic_{rq['type']}",
                              'corpus': 'classical-chinese', 'chunk_id': None, 'reference': None},
                             ensure_ascii=False) + '\n')
        total += 1
    out.close(); conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
