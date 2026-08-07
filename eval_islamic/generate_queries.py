# -*- coding: utf-8 -*-
"""
Generate query set for Islamic corpus retrieval evaluation.
Covers: quran (Arabic), sahih-bukhari, sahih-muslim, sunan-abu-dawood, rumi-masnavi.

Output: eval_islamic/queries.jsonl
Run:    python eval_islamic/generate_queries.py
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

# Hadith corpora are English — use English query generation
HADITH_CORPORA = ["sahih-bukhari", "sahih-muslim", "sunan-abu-dawood"]
# Rumi is English poetry
SUFI_CORPORA   = ["rumi-masnavi"]
# Quran is Arabic — synthetic queries not practical (cross-lingual); use only realistic
SAMPLES_HADITH = 40
SAMPLES_SUFI   = 20

HADITH_PROMPT = """\
You are building a retrieval benchmark for Hadith collections
(Sahih al-Bukhari, Sahih Muslim, Sunan Abu Dawood — English translations, Unlicense).

Below is a hadith from {source} ({reference}).
Write EXACTLY two questions a Muslim scholar or student might use to find this text:
1. NATURAL: A direct question this hadith answers. Reference the collection, narrator, or topic.
2. PARAPHRASE: The same need, rephrased with different vocabulary.

Rules:
- Questions must be answerable from this specific hadith.
- Reference Islamic concepts (prayer, fasting, zakat, hadith chain, sunnah, etc.) where relevant.
- English output only.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Hadith from {source} ({reference}):
{text}
"""

SUFI_PROMPT = """\
You are building a retrieval benchmark for Rumi's Masnavi
(R.A. Nicholson 1925-26 English translation, Public Domain).

Below is a passage from the Masnavi ({reference}).
Write EXACTLY two questions a Sufi scholar or spiritual seeker might ask:
1. NATURAL: A direct question this passage answers. Reference Rumi, the Masnavi, or Sufi concepts.
2. PARAPHRASE: The same information need rephrased differently.

Rules:
- Questions must be answerable from this specific passage.
- Reference Sufi concepts (love, annihilation/fana, the reed flute, divine union, etc.) where relevant.
- English output only.
- Output ONLY: {{"natural": "...", "paraphrase": "..."}}

Passage ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    # Quran (Arabic — realistic only, no synthetic)
    {"query": "What does the Quran say about the oneness of God (tawhid) in Surah Al-Ikhlas?", "corpus": "quran", "type": "quran_tawhid"},
    {"query": "How does the Quran describe the Day of Judgment and accountability?", "corpus": "quran", "type": "quran_judgment"},
    {"query": "What does Surah Al-Fatiha say about guidance and worship?", "corpus": "quran", "type": "quran_fatiha"},
    {"query": "What does the Quran say about believers and their reward in paradise?", "corpus": "quran", "type": "quran_paradise"},
    {"query": "How does the Quran describe the creation of humans?", "corpus": "quran", "type": "quran_creation"},
    # Bukhari
    {"query": "What hadith does al-Bukhari report about the importance of intention in deeds?", "corpus": "sahih-bukhari", "type": "bukhari_niyyah"},
    {"query": "What does Sahih al-Bukhari say about the five pillars of Islam?", "corpus": "sahih-bukhari", "type": "bukhari_pillars"},
    {"query": "How is the Prophet Muhammad described performing the daily prayers in Bukhari?", "corpus": "sahih-bukhari", "type": "bukhari_prayer"},
    {"query": "What does al-Bukhari report about the Prophet's advice on treating neighbors?", "corpus": "sahih-bukhari", "type": "bukhari_neighbors"},
    # Muslim
    {"query": "What does Sahih Muslim say about the definition and conditions of faith (iman)?", "corpus": "sahih-muslim", "type": "muslim_iman"},
    {"query": "How does Sahih Muslim describe the obligations of fasting during Ramadan?", "corpus": "sahih-muslim", "type": "muslim_fasting"},
    {"query": "What hadith in Muslim discusses the rights of Muslims over one another?", "corpus": "sahih-muslim", "type": "muslim_rights"},
    # Abu Dawood (new)
    {"query": "What does Sunan Abu Dawood say about the etiquette of prayer and ablution?", "corpus": "sunan-abu-dawood", "type": "abudawud_prayer"},
    {"query": "How does Abu Dawood report on the Prophet's conduct in times of conflict?", "corpus": "sunan-abu-dawood", "type": "abudawud_conduct"},
    {"query": "What hadiths in Abu Dawood discuss the treatment of women and family relations?", "corpus": "sunan-abu-dawood", "type": "abudawud_family"},
    {"query": "What does Sunan Abu Dawood say about funeral rites and burial practices?", "corpus": "sunan-abu-dawood", "type": "abudawud_funeral"},
    {"query": "How does Abu Dawood describe the Prophet's dietary rules and food etiquette?", "corpus": "sunan-abu-dawood", "type": "abudawud_food"},
    # Rumi (new)
    {"query": "How does Rumi use the image of the reed flute to describe spiritual longing?", "corpus": "rumi-masnavi", "type": "rumi_reed"},
    {"query": "What does Rumi say about the nature of divine love and union with God?", "corpus": "rumi-masnavi", "type": "rumi_love"},
    {"query": "How does Rumi describe the story of the merchant and the parrot in the Masnavi?", "corpus": "rumi-masnavi", "type": "rumi_story"},
    {"query": "What is Rumi's teaching on the role of the spiritual master (shaykh) in Sufi practice?", "corpus": "rumi-masnavi", "type": "rumi_shaykh"},
    {"query": "How does Rumi describe the stages of the soul's journey toward God?", "corpus": "rumi-masnavi", "type": "rumi_soul"},
    # Cross-corpus
    {"query": "How do Hadith and Sufi poetry differ in their descriptions of divine love?", "corpus": "rumi-masnavi", "type": "cross_sufi_hadith"},
    {"query": "What is the Islamic concept of tawakkul (trust in God) across different text traditions?", "corpus": "sahih-bukhari", "type": "cross_tawakkul"},
    # Negatives
    {"query": "What does the Quran say about democracy and modern political systems?", "corpus": "quran", "type": "negative"},
    {"query": "Which hadith discusses cryptocurrency trading?", "corpus": "sahih-bukhari", "type": "negative"},
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

    # Synthetic: Hadith corpora
    for corpus in HADITH_CORPORA:
        cur = conn.cursor()
        cur.execute("""
            SELECT dc.id, dc.chunk_text, dc.reference, ct.title_english AS source_title
            FROM document_chunks dc JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = %s ORDER BY random() LIMIT %s
        """, (corpus, SAMPLES_HADITH))
        rows = cur.fetchall()
        print(f"{corpus}: sampling {len(rows)} chunks for synthetic queries")

        for row in rows:
            source = (row['source_title'] or '').split(' — ')[0]
            prompt = HADITH_PROMPT.format(
                source    = source,
                reference = row['reference'] or '',
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

    # Synthetic: Rumi/Sufi
    for corpus in SUFI_CORPORA:
        cur = conn.cursor()
        cur.execute("""
            SELECT dc.id, dc.chunk_text, dc.reference, ct.title_english AS source_title
            FROM document_chunks dc JOIN canon_texts ct ON ct.id = dc.text_id
            JOIN source_corpora sc ON sc.id = ct.corpus_id
            WHERE sc.code = %s ORDER BY random() LIMIT %s
        """, (corpus, SAMPLES_SUFI))
        rows = cur.fetchall()
        print(f"{corpus}: sampling {len(rows)} chunks for synthetic queries")

        for row in rows:
            prompt = SUFI_PROMPT.format(
                reference = row['reference'] or '',
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

    # Realistic queries
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


if __name__ == '__main__':
    main()
