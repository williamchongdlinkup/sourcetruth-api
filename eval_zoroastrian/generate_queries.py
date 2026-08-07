# -*- coding: utf-8 -*-
"""Generate retrieval eval queries for Zoroastrian / Avesta corpus."""
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
CORPORA = ["avesta"]
SAMPLES_PER_CORPUS = 30  # smaller corpus

QUERY_PROMPT = """\
You are building a retrieval benchmark for Zoroastrian scripture
(Vendidad and Yasna — Darmesteter / Mills 1880-1887 PD translations).

Below is a passage from "{source}" ({reference}).
Write EXACTLY two questions a scholar of Iranian religion or comparative religion might ask:
1. NATURAL: A direct question this passage answers. Reference Zoroastrian concepts or names.
2. PARAPHRASE: Same need, different phrasing.

Rules:
- Questions answerable from this specific passage.
- Reference names (Ahura Mazda, Zarathustra/Zoroaster, Ahriman, Angra Mainyu) or concepts (Asha, Druj, purification).
- English output only. No quotation marks.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage from {source} ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    {"query": "Who is Ahura Mazda and what is his role in Zoroastrian theology?", "type": "ahura_mazda"},
    {"query": "What does the Vendidad say about purification rituals after contact with corpses?", "type": "vendidad_purity"},
    {"query": "How does the Zoroastrian text describe the creation of the world by Ahura Mazda?", "type": "creation"},
    {"query": "What is Asha (truth/righteousness) and how is it contrasted with Druj (lie/evil)?", "type": "asha_druj"},
    {"query": "What are the teachings of Zarathustra (Zoroaster) as recorded in the Gathas?", "type": "zarathustra_gathas"},
    {"query": "What does the Vendidad say about the treatment of the dead and funerary practices?", "type": "vendidad_death"},
    {"query": "How does Zoroastrian scripture describe the conflict between good and evil?", "type": "cosmic_dualism"},
    {"query": "What agricultural and pastoral laws are described in the Vendidad?", "type": "vendidad_agriculture"},
    {"query": "What does the Yasna say about fire as a sacred element in Zoroastrianism?", "type": "yasna_fire"},
    {"query": "How does the Avesta describe the afterlife and judgment of souls?", "type": "afterlife"},
    {"query": "What is the role of the Fravashis (divine spirits) in Zoroastrian belief?", "type": "fravashis"},
    {"query": "How does Zoroastrian scripture describe the Chinvat Bridge and the soul's journey?", "type": "chinvat"},
    {"query": "What purification requirements does the Vendidad set for dealing with pollution?", "type": "vendidad_pollution"},
    {"query": "How does Zarathustra describe his divine revelation and mission in the Gathas?", "type": "gathas_revelation"},
    {"query": "What Zoroastrian prayers and rituals are described in the Yasna liturgy?", "type": "yasna_ritual"},
    {"query": "What do Zoroastrian texts say about the proper care of dogs?", "type": "vendidad_dogs"},
    {"query": "How does the Avesta describe quantum mechanics and subatomic physics?", "type": "negative"},
    {"query": "Which Zoroastrian scripture mentions the internet?", "type": "negative"},
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
                              'corpus': 'avesta', 'chunk_id': None, 'reference': None},
                             ensure_ascii=False) + '\n')
        total += 1
    out.close(); conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
