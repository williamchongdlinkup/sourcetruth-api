# -*- coding: utf-8 -*-
"""Generate retrieval eval queries for Sanskrit Classical corpus (Ramayana, Kalidasa)."""
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
CORPORA = ["sanskrit-classical"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval benchmark for Sanskrit classical literature
(Ramayana by Griffith, Shakuntala by Ryder — Public Domain translations).

Below is a passage from "{source}" ({reference}).
Write EXACTLY two questions a Sanskrit scholar or student of Indian literature might ask:
1. NATURAL: A direct question this passage answers. Reference character names, events, or themes.
2. PARAPHRASE: Same information need, different phrasing.

Rules:
- Questions answerable from this specific passage.
- Reference names (Rama, Sita, Ravana, Kalidasa, Shakuntala, Dushyanta) or themes.
- English output only. No quotation marks.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage from {source} ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    {"query": "How does Valmiki describe Rama's character and virtues in the Ramayana?", "type": "ramayana_rama"},
    {"query": "What happens when Rama, Sita, and Lakshmana are exiled to the forest?", "type": "ramayana_exile"},
    {"query": "How does the Ramayana describe Ravana's abduction of Sita?", "type": "ramayana_abduction"},
    {"query": "What role does Hanuman play in the Ramayana's search for Sita?", "type": "ramayana_hanuman"},
    {"query": "How is the battle between Rama and Ravana described in the Yuddha Kanda?", "type": "ramayana_battle"},
    {"query": "What is the nature of dharma (righteousness) as portrayed in the Ramayana?", "type": "ramayana_dharma"},
    {"query": "How does the Ramayana describe the forest of Dandaka and its sages?", "type": "ramayana_forest"},
    {"query": "What events occur in the Bala Kanda (childhood book) of the Ramayana?", "type": "ramayana_childhood"},
    {"query": "How does Kalidasa portray the love between Dushyanta and Shakuntala?", "type": "kalidasa_love"},
    {"query": "What is the dramatic climax of Kalidasa's Shakuntala play?", "type": "kalidasa_climax"},
    {"query": "How does Kalidasa use nature imagery in his poetry?", "type": "kalidasa_nature"},
    {"query": "What is the role of the curse and its reversal in Shakuntala?", "type": "kalidasa_curse"},
    {"query": "How do Indian epics portray the relationship between devotion and duty?", "type": "cross_devotion"},
    {"query": "What themes of separation and reunion appear in Sanskrit literature?", "type": "cross_separation"},
    {"query": "How does Sanskrit epic literature describe cosmic battles between gods and demons?", "type": "cross_battle"},
    {"query": "How is the forest portrayed as a spiritual space in Indian literature?", "type": "cross_forest"},
    {"query": "What do Sanskrit epics say about the proper qualities of a king?", "type": "cross_kingship"},
    {"query": "What do Sanskrit texts say about modern technology and smartphones?", "type": "negative"},
    {"query": "Which Sanskrit epic describes the invention of democracy?", "type": "negative"},
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
                              'corpus': 'sanskrit-classical', 'chunk_id': None, 'reference': None},
                             ensure_ascii=False) + '\n')
        total += 1
    out.close(); conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
