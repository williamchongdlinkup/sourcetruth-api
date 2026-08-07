# -*- coding: utf-8 -*-
"""
Generate query set for Hindu corpus retrieval evaluation.
Covers: Bhagavad Gita (bhagavad-gita) and Principal Upanishads (upanishads).

Output: eval_hindu/queries.jsonl
Run:    python eval_hindu/generate_queries.py
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

HINDU_CORPORA      = ["bhagavad-gita", "upanishads", "yoga-sutras"]
SAMPLES_PER_CORPUS = 25

BG_QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for the Bhagavad Gita
(Edwin Arnold's "The Song Celestial", 1885 English translation).

Below is a passage from {reference}.
Write EXACTLY two questions that a student of Indian philosophy or Hindu scripture might ask:
1. NATURAL: A direct question this passage answers. Reference the Adhyaya, speaker (Krishna/Arjuna), or concept.
2. PARAPHRASE: The same information need, rephrased with different vocabulary.

Rules:
- Questions must be answerable from this specific passage.
- Reference Adhyaya numbers, speaker names, or Sanskrit concepts (dharma, karma, yoga, atman, brahman).
- English output only.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""

UPANISHADS_QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for the Principal Upanishads
(Max Müller SBE translations, 1879-1884).

Below is a passage from {reference}.
Write EXACTLY two questions a student of Vedanta or Indian philosophy might ask:
1. NATURAL: A direct question this passage answers.
2. PARAPHRASE: The same information need, rephrased with different vocabulary.

Rules:
- Questions must be answerable from this specific passage.
- Reference the Upanishad name, Brahman, Atman, Maya, moksha, Self, or specific concepts.
- English output only.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    # Bhagavad Gita topics
    {"query": "What does Krishna teach about performing one's duty without attachment to results?", "corpus": "bhagavad-gita", "type": "gita_karma"},
    {"query": "How does the Bhagavad Gita describe the eternal, imperishable nature of the soul?", "corpus": "bhagavad-gita", "type": "gita_atman"},
    {"query": "What does Krishna say about the three paths to liberation in the Bhagavad Gita?", "corpus": "bhagavad-gita", "type": "gita_yoga"},
    {"query": "How does Arjuna's despondency at the battlefield lead to Krishna's teachings?", "corpus": "bhagavad-gita", "type": "gita_narrative"},
    {"query": "What does the Bhagavad Gita say about devotion (bhakti) as a path to God?", "corpus": "bhagavad-gita", "type": "gita_bhakti"},
    {"query": "What is Krishna's description of the imperishable Brahman and the nature of the supreme?", "corpus": "bhagavad-gita", "type": "gita_brahman"},
    {"query": "How does the Bhagavad Gita describe a person of steady wisdom (sthitaprajna)?", "corpus": "bhagavad-gita", "type": "gita_wisdom"},
    # Upanishads topics
    {"query": "What is the Upanishadic concept of Brahman as the ultimate reality?", "corpus": "upanishads", "type": "upanishad_brahman"},
    {"query": "How do the Upanishads describe the relationship between the individual self (Atman) and universal Brahman?", "corpus": "upanishads", "type": "upanishad_atman"},
    {"query": "What does the Chandogya Upanishad teach about the syllable Om as the essence of the universe?", "corpus": "upanishads", "type": "upanishad_om"},
    {"query": "How do the Upanishads describe the nature of consciousness and the self in deep sleep?", "corpus": "upanishads", "type": "upanishad_consciousness"},
    {"query": "What do the Upanishads say about moksha or liberation from the cycle of rebirth?", "corpus": "upanishads", "type": "upanishad_moksha"},
    {"query": "What is the teaching of 'Tat tvam asi' (That thou art) in the Chandogya Upanishad?", "corpus": "upanishads", "type": "upanishad_identity"},
    # Cross-corpus
    {"query": "How do both the Bhagavad Gita and the Upanishads describe the eternal self or Atman?", "corpus": "bhagavad-gita", "type": "cross"},
    {"query": "What is the concept of dharma in Hindu philosophical texts?", "corpus": "bhagavad-gita", "type": "cross"},
    # Yoga Sutras (new)
    {"query": "What does Patanjali say about the definition of yoga in the Yoga Sutras?", "corpus": "yoga-sutras", "type": "yoga_definition"},
    {"query": "How does Patanjali describe the eight limbs of yoga (ashtanga)?", "corpus": "yoga-sutras", "type": "yoga_ashtanga"},
    {"query": "What is samadhi according to the Yoga Sutras of Patanjali?", "corpus": "yoga-sutras", "type": "yoga_samadhi"},
    {"query": "How does Patanjali explain the fluctuations of the mind (chitta vritti) in the Yoga Sutras?", "corpus": "yoga-sutras", "type": "yoga_mind"},
    {"query": "What does Patanjali say about the obstacles to yoga practice?", "corpus": "yoga-sutras", "type": "yoga_obstacles"},
    {"query": "How does the Yoga Sutras describe pratyahara (withdrawal of the senses)?", "corpus": "yoga-sutras", "type": "yoga_pratyahara"},
    {"query": "What are the siddhis or supernatural powers described in the Yoga Sutras?", "corpus": "yoga-sutras", "type": "yoga_siddhis"},
    # Upanishads expanded (new)
    {"query": "What does the Brihadaranyaka Upanishad teach about the nature of Brahman?", "corpus": "upanishads", "type": "upanishad_brihadaranyaka"},
    {"query": "How does the Katha Upanishad describe the journey of the self after death?", "corpus": "upanishads", "type": "upanishad_katha"},
    {"query": "What does the Mundaka Upanishad say about the two kinds of knowledge?", "corpus": "upanishads", "type": "upanishad_mundaka"},
    {"query": "How does the Taittiriya Upanishad describe the five sheaths or koshas of the self?", "corpus": "upanishads", "type": "upanishad_taittiriya"},
    # Negatives
    {"query": "What do the Vedic texts say about blockchain technology?", "corpus": "bhagavad-gita", "type": "negative"},
    {"query": "Which Upanishad discusses democracy and modern government?", "corpus": "upanishads", "type": "negative"},
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

    YOGA_QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for the Yoga Sutras of Patanjali
(Charles Johnston 1912 translation, "The Book of the Spiritual Man").

Below is a passage from {reference}.
Write EXACTLY two questions a student of yoga philosophy or Indian spirituality might ask:
1. NATURAL: A direct question this passage answers.
2. PARAPHRASE: The same information need, rephrased with different vocabulary.

Rules:
- Questions must be answerable from this specific passage.
- Reference Patanjali, yoga philosophy, or specific Sanskrit concepts (samadhi, dharana, pratyahara, chitta, etc.).
- English output only.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""
    PROMPTS = {
        "bhagavad-gita": BG_QUERY_PROMPT,
        "upanishads":    UPANISHADS_QUERY_PROMPT,
        "yoga-sutras":   YOGA_QUERY_PROMPT,
    }

    for corpus in HINDU_CORPORA:
        cur = conn.cursor()
        cur.execute("""
            SELECT dc.id, dc.chunk_text, dc.reference, dc.chapter,
                   ct.title_english AS source_title
            FROM document_chunks dc
            JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = %s
            ORDER BY random()
            LIMIT %s
        """, (corpus, SAMPLES_PER_CORPUS))
        rows = cur.fetchall()
        print(f"{corpus}: sampling {len(rows)} chunks for synthetic queries")

        prompt_template = PROMPTS.get(corpus, BG_QUERY_PROMPT)

        for row in rows:
            prompt = prompt_template.format(
                reference    = row['reference'] or row['chapter'] or row['source_title'] or corpus,
                text         = (row['chunk_text'] or '')[:1200],
            )
            for attempt in range(3):
                try:
                    msg = client.messages.create(
                        model=JUDGE_MODEL if False else "claude-haiku-4-5-20251001",
                        max_tokens=256,
                        messages=[{"role": "user", "content": prompt}],
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
            'corpus':     rq['corpus'],
            'chunk_id':   None,
            'reference':  None,
        }, ensure_ascii=False) + '\n')
        total += 1

    out.close()
    conn.close()
    print(f"\nWrote {total} queries to {OUT}")


JUDGE_MODEL = "claude-haiku-4-5-20251001"

if __name__ == '__main__':
    main()
