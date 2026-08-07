# -*- coding: utf-8 -*-
"""Generate retrieval eval queries for Sikh SGGS corpus."""
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
CORPORA = ["sggs"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval benchmark for Sikh scripture
(Sri Guru Granth Sahib Ji — Gurmukhi text with English romanized transliteration).

Below is a Gurbani passage from the SGGS ({reference}).
Write EXACTLY two questions a Sikh practitioner or scholar might ask:
1. NATURAL: A question this passage answers about Sikh teaching or practice.
2. PARAPHRASE: Same meaning, different words.

Rules:
- Questions answerable from this passage.
- Reference Sikh concepts (Waheguru, Nam/Name, seva, simran, haumai/ego, maya) or the Gurus.
- English output only. No quotation marks.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage from SGGS ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    {"query": "What does the Guru Granth Sahib say about the Name of God (Nam Simran)?", "type": "nam_simran"},
    {"query": "How does the SGGS describe the nature of Waheguru (God)?", "type": "waheguru"},
    {"query": "What is the teaching of the SGGS about haumai (ego) as a barrier to spiritual growth?", "type": "haumai"},
    {"query": "How does the Guru Granth Sahib describe the path of seva (selfless service)?", "type": "seva"},
    {"query": "What does the SGGS teach about maya (illusion) and attachment to the material world?", "type": "maya"},
    {"query": "How is the relationship between the Guru and the disciple (Sikh) described in the SGGS?", "type": "guru_disciple"},
    {"query": "What does the Guru Granth Sahib say about the importance of the Sangat (congregation)?", "type": "sangat"},
    {"query": "How does the SGGS describe death and the soul's journey after death?", "type": "death_soul"},
    {"query": "What is the Sikh teaching on equality of all human beings regardless of caste?", "type": "equality"},
    {"query": "How does the Guru Granth Sahib describe the One God who pervades all creation?", "type": "ik_onkar"},
    {"query": "What does the Japji Sahib teach about the stages of spiritual realization?", "type": "japji"},
    {"query": "How does the SGGS describe the value of truth and honest living?", "type": "truth"},
    {"query": "What teachings about women's equality and dignity appear in the Guru Granth Sahib?", "type": "women"},
    {"query": "How does the SGGS describe the blessing of the Guru's grace (Nadar)?", "type": "grace"},
    {"query": "What does Sikh scripture say about the futility of rituals without inner devotion?", "type": "ritual"},
    {"query": "How does the SGGS describe the concept of Hukam (divine will/order)?", "type": "hukam"},
    {"query": "What does the Guru Granth Sahib say about computers and digital technology?", "type": "negative"},
    {"query": "Which shabad describes the invention of nuclear weapons?", "type": "negative"},
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
                                          text=(row['chunk_text'] or '')[:1000])
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
                              'corpus': 'sggs', 'chunk_id': None, 'reference': None},
                             ensure_ascii=False) + '\n')
        total += 1
    out.close(); conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
