# -*- coding: utf-8 -*-
"""
Generate query set for Western Philosophy + Political Philosophy retrieval evaluation.

Corpora: western-philosophy, political-philosophy

Output: eval_western_philosophy/queries.jsonl
Run:    python eval_western_philosophy/generate_queries.py
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

PHIL_CORPORA       = ["western-philosophy", "political-philosophy"]
SAMPLES_PER_CORPUS = 50

QUERY_PROMPT = """\
You are building a retrieval evaluation benchmark for Western philosophy and political theory,
covering Early Modern philosophy, German Idealism, Nietzsche, and political philosophy
(Hobbes, Locke, Rousseau, Mill, Wollstonecraft, the Federalist Papers, Machiavelli).

Below is a passage from "{source}" ({reference}).
Write EXACTLY two questions a philosophy student, political theorist, or scholar might ask:
1. NATURAL: A direct question this passage answers. Reference the author, work, or concept.
2. PARAPHRASE: The same information need, rephrased with different vocabulary.

Rules:
- Questions must be answerable from this specific passage.
- Reference author names, work titles, or philosophical concepts (reason, will, duty, sovereignty, etc.).
- English output only. No quotation marks around the question.
- Output ONLY a JSON object: {{"natural": "...", "paraphrase": "..."}}

Passage from {source} ({reference}):
{text}
"""

REALISTIC_QUERIES = [
    # Descartes
    {"query": "What is Descartes's method of radical doubt in the Meditations?", "type": "descartes_doubt"},
    {"query": "How does Descartes argue for the existence of the cogito?", "type": "descartes_cogito"},
    {"query": "What does Descartes say about the relationship between mind and body?", "type": "descartes_mind_body"},
    # Locke
    {"query": "How does Locke define natural rights and the state of nature?", "type": "locke_natural_rights"},
    {"query": "What is Locke's argument for government by consent in the Two Treatises?", "type": "locke_consent"},
    {"query": "How does Locke explain the origin of ideas in the Essay Concerning Human Understanding?", "type": "locke_ideas"},
    # Hume
    {"query": "What is Hume's problem of induction and how does he formulate it?", "type": "hume_induction"},
    {"query": "How does Hume argue against miracles in the Enquiry?", "type": "hume_miracles"},
    {"query": "What is Hume's fork between relations of ideas and matters of fact?", "type": "hume_fork"},
    # Spinoza
    {"query": "What does Spinoza mean by 'God or Nature' (Deus sive Natura) in the Ethics?", "type": "spinoza_god"},
    {"query": "How does Spinoza define substance, attributes, and modes in the Ethics?", "type": "spinoza_substance"},
    # Kant
    {"query": "What is Kant's Copernican revolution in philosophy in the Critique of Pure Reason?", "type": "kant_copernican"},
    {"query": "How does Kant distinguish analytic from synthetic judgments?", "type": "kant_analytic_synthetic"},
    {"query": "What is Kant's categorical imperative in the Groundwork?", "type": "kant_categorical_imperative"},
    {"query": "How does Kant argue that time and space are forms of intuition?", "type": "kant_intuition"},
    # Schopenhauer
    {"query": "What is Schopenhauer's concept of the Will as the thing-in-itself?", "type": "schopenhauer_will"},
    {"query": "How does Schopenhauer describe the relationship between suffering and will in the World as Will and Idea?", "type": "schopenhauer_suffering"},
    # Hegel
    {"query": "What is Hegel's dialectical method of thesis, antithesis, and synthesis?", "type": "hegel_dialectic"},
    {"query": "How does Hegel describe the development of philosophy in his Lectures?", "type": "hegel_lectures"},
    # Nietzsche
    {"query": "What does Nietzsche mean by the will to power in Beyond Good and Evil?", "type": "nietzsche_will_to_power"},
    {"query": "How does Nietzsche describe the death of God and its consequences in Zarathustra?", "type": "nietzsche_god"},
    {"query": "What is the Nietzschean distinction between master morality and slave morality in the Genealogy?", "type": "nietzsche_morality"},
    {"query": "How does Nietzsche characterize the Übermensch in Thus Spoke Zarathustra?", "type": "nietzsche_ubermensch"},
    {"query": "What is Nietzsche's critique of Christian morality in the Genealogy of Morals?", "type": "nietzsche_christian"},
    # Plato (additional)
    {"query": "What is the philosophical significance of the chariot allegory in Plato's Phaedrus?", "type": "plato_phaedrus"},
    {"query": "How does Plato's Theaetetus define knowledge and criticize empiricist accounts?", "type": "plato_theaetetus"},
    # Aristotle Metaphysics
    {"query": "How does Aristotle define substance (ousia) in the Metaphysics?", "type": "aristotle_metaphysics"},
    {"query": "What is Aristotle's concept of the unmoved mover in the Metaphysics?", "type": "aristotle_unmoved"},
    # Hobbes
    {"query": "What is Hobbes's description of the state of nature as 'nasty, brutish, and short' in Leviathan?", "type": "hobbes_state_nature"},
    {"query": "How does Hobbes argue for absolute sovereignty in the Leviathan?", "type": "hobbes_sovereignty"},
    # Rousseau
    {"query": "What is Rousseau's concept of the general will in the Social Contract?", "type": "rousseau_general_will"},
    {"query": "How does Rousseau describe the social contract and the origin of legitimate authority?", "type": "rousseau_contract"},
    # Federalist Papers
    {"query": "How does Madison argue for the separation of powers in the Federalist Papers?", "type": "federalist_separation"},
    {"query": "What is Federalist No. 51's argument about checks and balances?", "type": "federalist_51"},
    {"query": "How does Hamilton argue for the importance of the executive branch in Federalist No. 70?", "type": "federalist_70"},
    # Mill
    {"query": "What is Mill's harm principle in On Liberty?", "type": "mill_harm"},
    {"query": "How does Mill defend freedom of speech and expression in On Liberty?", "type": "mill_free_speech"},
    {"query": "What is Mill's argument for utilitarianism as the greatest happiness principle?", "type": "mill_utilitarianism"},
    # Wollstonecraft
    {"query": "How does Wollstonecraft argue for women's rational nature and equal education in the Vindication?", "type": "wollstonecraft_reason"},
    {"query": "What does Wollstonecraft say about the social construction of femininity?", "type": "wollstonecraft_femininity"},
    # Burke
    {"query": "How does Burke criticize the French Revolution in the Reflections?", "type": "burke_revolution"},
    # Machiavelli
    {"query": "What does Machiavelli say about whether it is better to be feared than loved in The Prince?", "type": "machiavelli_fear"},
    {"query": "How does Machiavelli advise a prince to deal with fortune and virtue?", "type": "machiavelli_virtue"},
    # Paine
    {"query": "How does Paine defend the rights of man against Burke's conservatism?", "type": "paine_rights"},
    # Cross-author
    {"query": "How do Kant and Mill differ in their approaches to moral philosophy?", "type": "cross_kant_mill"},
    {"query": "What do Locke, Rousseau, and Hobbes say about the social contract?", "type": "cross_contract"},
    {"query": "How do Plato and Aristotle differ in their accounts of reality and knowledge?", "type": "cross_plato_aristotle"},
    # Negatives
    {"query": "Which Western philosopher wrote about machine learning and neural networks?", "type": "negative"},
    {"query": "What does Nietzsche say about artificial intelligence?", "type": "negative"},
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

    for corpus in PHIL_CORPORA:
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
            source = (row['source_title'] or '').split(' — ')[0]
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
            'corpus':     'western-philosophy',
            'chunk_id':   None,
            'reference':  None,
        }, ensure_ascii=False) + '\n')
        total += 1

    out.close()
    conn.close()
    print(f"\nWrote {total} queries to {OUT}")


if __name__ == '__main__':
    main()
