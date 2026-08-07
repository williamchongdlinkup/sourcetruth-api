# -*- coding: utf-8 -*-
"""
Generate query set for Christian corpus retrieval evaluation.
Covers: KJV Bible (kjv).

Samples document_chunks, uses Claude Haiku to produce (natural, paraphrase)
English query pairs, plus hand-crafted realistic queries.

Output: eval_christianity/queries.jsonl
Run:    python eval_christianity/generate_queries.py
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

CHRISTIAN_CORPORA  = ["kjv", "bible-web", "bible-asv", "bible-ylt", "christian-theology"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for the King James Bible,
chunked at the chapter level.

Below is a passage from {book}, {reference} ({testament}).
Write EXACTLY two questions in English that a Christian scholar, theologian, or Bible student might ask:
1. NATURAL: A direct question this specific passage answers. Reference the book, chapter, or theme.
2. PARAPHRASE: The same information need, rephrased with different wording and no shared key phrases.

Rules:
- Questions must be answerable from this specific passage.
- Reference the book name, chapter number, or topic explicitly.
- Do NOT use the phrase "this passage" or "the text."
- English output only.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    # Theology / doctrine
    {"query": "What does the Bible say about the resurrection of the dead?", "corpus": "kjv", "type": "theology"},
    {"query": "How does the Gospel of John describe the nature of Jesus as the Word of God?", "corpus": "kjv", "type": "theology"},
    {"query": "What does Paul teach about justification by faith in Romans?", "corpus": "kjv", "type": "theology"},
    {"query": "What is the Sermon on the Mount and what does it teach?", "corpus": "kjv", "type": "teaching"},
    # Ethics / morality
    {"query": "What does Proverbs say about the importance of wisdom?", "corpus": "kjv", "type": "ethics"},
    {"query": "What commandments did Jesus give about loving your neighbor?", "corpus": "kjv", "type": "ethics"},
    {"query": "How does the book of James describe the relationship between faith and works?", "corpus": "kjv", "type": "ethics"},
    # Creation / prophecy / apocalypse
    {"query": "How does Genesis describe the creation of the world?", "corpus": "kjv", "type": "creation"},
    {"query": "What does the book of Revelation say about the end of the world?", "corpus": "kjv", "type": "prophecy"},
    {"query": "What messianic prophecies appear in Isaiah?", "corpus": "kjv", "type": "prophecy"},
    # Prayer / Psalms
    {"query": "What does Psalm 23 say about God as a shepherd?", "corpus": "kjv", "type": "poetry"},
    {"query": "How do the Psalms express lament and trust in God?", "corpus": "kjv", "type": "poetry"},
    # NT narrative
    {"query": "What miracles did Jesus perform according to the Gospel of Mark?", "corpus": "kjv", "type": "narrative"},
    {"query": "What is the parable of the prodigal son about?", "corpus": "kjv", "type": "teaching"},
    {"query": "How does the Gospel of Luke describe the birth of Jesus?", "corpus": "kjv", "type": "narrative"},
    # Cross-testament
    {"query": "What does the New Testament say about the fulfillment of the Law?", "corpus": "kjv", "type": "cross"},
    {"query": "How does Paul reference Abraham as an example of faith?", "corpus": "kjv", "type": "cross"},
    {"query": "What does the book of Hebrews say about Jesus as high priest?", "corpus": "kjv", "type": "theology"},
    # Augustine Confessions (new)
    {"query": "How does Augustine describe his spiritual restlessness before conversion in the Confessions?", "corpus": "christian-theology", "type": "augustine_confessions"},
    {"query": "What does Augustine say about God's role in his intellectual journey in the Confessions?", "corpus": "christian-theology", "type": "augustine_confessions"},
    {"query": "How does Augustine describe his time in Carthage and his struggles with desire?", "corpus": "christian-theology", "type": "augustine_confessions"},
    {"query": "What is Augustine's famous prayer 'our heart is restless until it rests in Thee'?", "corpus": "christian-theology", "type": "augustine_confessions"},
    # Augustine City of God (new)
    {"query": "What does Augustine say about the two cities — the City of God and the City of Man?", "corpus": "christian-theology", "type": "augustine_city"},
    {"query": "How does Augustine respond to the accusation that Christianity caused the fall of Rome?", "corpus": "christian-theology", "type": "augustine_city"},
    {"query": "What does Augustine argue about providence and God's sovereignty in history?", "corpus": "christian-theology", "type": "augustine_city"},
    # Apostolic Fathers (new)
    {"query": "What does the Didache say about baptism and the Eucharist in early Christianity?", "corpus": "christian-theology", "type": "apostolic_fathers"},
    {"query": "How does Ignatius of Antioch describe the role of the bishop in the early church?", "corpus": "christian-theology", "type": "apostolic_fathers"},
    {"query": "What does Clement of Rome say about church order and authority in 1 Clement?", "corpus": "christian-theology", "type": "apostolic_fathers"},
    # Cross-corpus (Bible + Patristics)
    {"query": "How do the Church Fathers interpret the Sermon on the Mount?", "corpus": "christian-theology", "type": "cross_patristics"},
    {"query": "What do Christian theologians say about the nature of evil and original sin?", "corpus": "christian-theology", "type": "cross_theology"},
    # Negative controls
    {"query": "What does the Bible say about artificial intelligence?", "corpus": "kjv", "type": "negative"},
    {"query": "Which Bible verse discusses the internet?", "corpus": "kjv", "type": "negative"},
]


def connect():
    url = os.environ.get("POOLER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


THEOLOGY_PROMPT = """\
You are building a retrieval benchmark for early Christian theological writings
(Augustine Confessions, Augustine City of God, Apostolic Fathers — Lightfoot PD translations).

Below is a passage from "{source}" ({reference}).
Write EXACTLY two questions a theology student or Christian historian might ask:
1. NATURAL: A direct question this passage answers. Reference the author or work.
2. PARAPHRASE: The same need, rephrased with different vocabulary.

Rules:
- Questions must be answerable from this specific passage.
- Reference authors, works, or theological concepts (grace, providence, original sin, church, martyrdom, etc.).
- English output only.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage from {source} ({reference}):
{text}
"""


def main():
    import psycopg2.extras
    conn   = connect()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    out    = open(OUT, 'w', encoding='utf-8')
    total  = 0

    for corpus in CHRISTIAN_CORPORA:
        cur = conn.cursor()
        cur.execute("""
            SELECT dc.id, dc.chunk_text, dc.reference, dc.chapter,
                   ct.title_english AS book, ct.collection AS testament
            FROM document_chunks dc
            JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = %s
            ORDER BY random()
            LIMIT %s
        """, (corpus, SAMPLES_PER_CORPUS))
        rows = cur.fetchall()
        print(f"{corpus}: sampling {len(rows)} chunks for synthetic queries")

        is_theology = corpus == "christian-theology"
        for row in rows:
            if is_theology:
                prompt = THEOLOGY_PROMPT.format(
                    source    = (row['book'] or '').split(' — ')[0],
                    reference = row['reference'] or row['chapter'] or '',
                    text      = (row['chunk_text'] or '')[:1200],
                )
            else:
                prompt = QUERY_PROMPT.format(
                    book      = row['book'],
                    reference = row['reference'] or row['chapter'] or '',
                    testament = row['testament'] or 'Bible',
                    text      = (row['chunk_text'] or '')[:1200],
                )
            for attempt in range(3):
                try:
                    msg = client.messages.create(
                        model  = "claude-haiku-4-5-20251001",
                        max_tokens = 256,
                        messages   = [{"role": "user", "content": prompt}],
                    )
                    raw = msg.content[0].text.strip()
                    m   = re.search(r'\{.*\}', raw, re.DOTALL)
                    if not m:
                        raise ValueError(f"No JSON found: {raw[:80]}")
                    parsed = json.loads(m.group())
                    for qtype in ('natural', 'paraphrase'):
                        q = parsed.get(qtype, '').strip()
                        if q:
                            record = {
                                'query':      q,
                                'query_type': f'synthetic_{qtype}',
                                'corpus':     corpus,
                                'chunk_id':   row['id'],
                                'reference':  row['reference'],
                            }
                            out.write(json.dumps(record, ensure_ascii=False) + '\n')
                            total += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(3)
                    else:
                        print(f"  [WARN] Failed for chunk {row['id']}: {e}")

    # Realistic queries
    for rq in REALISTIC_QUERIES:
        record = {
            'query':      rq['query'],
            'query_type': f"realistic_{rq['type']}",
            'corpus':     rq['corpus'],
            'chunk_id':   None,
            'reference':  None,
        }
        out.write(json.dumps(record, ensure_ascii=False) + '\n')
        total += 1

    out.close()
    conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
