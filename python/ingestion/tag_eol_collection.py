# -*- coding: utf-8 -*-
"""
Tag end-of-life relevant passages with collection = 'eol' in document_chunks.

The EOL collection is a curated cross-tradition subset for the Singapore EOL Companion
(Meta AI Hackathon 2026). Restricting search to collection='eol' eliminates
cross-tradition contamination from the general index.

Run:
  python ingestion/tag_eol_collection.py --dry-run   # preview counts only
  python ingestion/tag_eol_collection.py             # apply tags
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / '.env')

# ── EOL rules ────────────────────────────────────────────────────────────────
# Full texts: tag ALL chunks belonging to these external_ids
FULL_TEXT_IDS = [
    # Theravada Buddhist
    'mahaparinibbana-sutta',          # Buddha's own death — foundational EOL text
    'dhammapada',                     # impermanence chapters
    'sutta-nipata',                   # Metta Sutta + many EOL passages
    'milindapanha-1',                 # Death of the Arahant section

    # Chinese Mahayana
    'amitabha-sutra',                 # Pure Land deathbed text
    'dizang-sutra-amies',             # Ksitigarbha — for the recently dead

    # Tibetan Buddhist
    'bardo-thodol-evans-wentz',       # Tibetan Book of the Dead — full text

    # Hindu — Upanishads
    'upanishad-isa-upanishad',        # Isa Upanishad — short, on the self at death
    'upanishad-katha-upanishad',      # Katha — Nachiketa meets Yama
    'upanishad-upanishads',           # Brihadaranyaka sections on soul at death
    'upanishad-kena-upanishad',       # Kena — on the self beyond death

    # Jainism
    'uttaradhyayana-sutra',           # Ch.14 right dying; other EOL passages
    'kalpa-sutra',                    # Jain sacred EOL text

    # Bahai
    'hidden-words-arabic-kheirella',  # Hidden Words — soul/death passages throughout
    'hidden-words-persian-haddad',    # Persian Hidden Words

    # Islam — Quran surahs (EOL-specific)
    'quran-002',   # Al-Baqarah — "Inna lillahi wa inna ilayhi raji'un"
    'quran-003',   # Al-Imran — "Every soul shall taste death"
    'quran-036',   # Ya-Sin — recited at funerals
    'quran-039',   # Az-Zumar — "Allah takes the souls at death"
    'quran-056',   # Al-Waqiah — on dying and what follows
    'quran-075',   # Al-Qiyamah (The Resurrection)
    'quran-089',   # Al-Fajr — "O satisfied soul, return to your Lord"

    # Islam — Hadith (Books of Funerals / Jana'iz)
    'sahih-muslim-s11',              # Sahih Muslim — Book of Funerals
    'sunan-abu-dawood-s21',          # Sunan Abu Dawud — Book of Funerals

    # Sikh — GGS sections containing deathbed passages
    'sggs-1-100',     # Japji Sahib (opening prayer, pauris 34-38 on the soul)
    'sggs-201-300',   # Sukhmani Sahib (peace of mind; Ashtpadi 1 and 16)
    'sggs-301-400',   # Raag Asa Ang 349 (Guru Nanak on death)
    'sggs-501-600',   # Raag Gujri Ang 578-579 (Kabir on death)

    # Jewish — Mishnah
    'mishnah-sanhedrin',              # Sanhedrin 4:5 — "Whoever saves a single soul..."
    'mishnah-moed-katan',             # Tractate on mourning period

    # Jewish — Tanakh
    'tanakh-ecclesiastes',            # Eccl 3, 12 — "A time to be born and a time to die"
    'tanakh-lamentations',            # Lamentations — grief

    # Classical Chinese — Taoist (full texts; semantic search handles chapter relevance)
    'zhuangzi-giles',                 # Ch.6/18/22 on death; full text for semantic retrieval
    'tao-te-ching-legge',             # 33 chunks total; Ch.16/33/50/76 are EOL-core
]

# Chapter-level: tag only chunks where dc.chapter IN (chapters)
# Format: (external_id, [chapter_strings])
CHAPTER_RULES = [
    # ── Bible ASV / WEB / YLT (book-number external IDs) ──
    # Psalms — classic deathbed passages
    ('asv-book-019',  ['23', '46', '90', '121', '139']),
    ('web-book-019',  ['23', '46', '90', '121', '139']),
    ('ylt-book-019',  ['23', '46', '90', '121', '139']),
    # John
    ('asv-book-043',  ['11', '14']),
    ('web-book-043',  ['11', '14']),
    ('ylt-book-043',  ['11', '14']),
    # Romans 8
    ('asv-book-045',  ['8']),
    ('web-book-045',  ['8']),
    ('ylt-book-045',  ['8']),
    # 1 Corinthians 15 — resurrection
    ('asv-book-046',  ['15']),
    ('web-book-046',  ['15']),
    ('ylt-book-046',  ['15']),
    # Revelation 21
    ('asv-book-066',  ['21']),
    ('web-book-066',  ['21']),
    ('ylt-book-066',  ['21']),
    # 2 Corinthians 5
    ('asv-book-047',  ['5']),
    ('web-book-047',  ['5']),
    ('ylt-book-047',  ['5']),
    # Lamentations 3
    ('asv-book-025',  ['3']),
    ('web-book-025',  ['3']),
    ('ylt-book-025',  ['3']),

    # ── Bhagavad Gita ──
    ('bg-adhyaya-2',  None),   # None = tag full text (already handled above but using rule for clarity)
    ('bg-adhyaya-8',  None),

    # ── Tanakh — Psalms ──
    ('tanakh-psalms', ['23', '46', '90', '103', '121']),

    # ── KJV Bible (name-based external IDs, not book-number format) ──
    ('kjv-psalms',       ['23', '46', '90', '121', '139']),
    ('kjv-john',         ['11', '14']),
    ('kjv-romans',       ['8']),
    ('kjv-1-corinthians', ['15']),
    ('kjv-revelation',   ['21']),
    ('kjv-2-corinthians', ['5']),
    ('kjv-lamentations', ['3']),
]

# Add BG chapters to FULL_TEXT_IDS
FULL_TEXT_IDS += ['bg-adhyaya-2', 'bg-adhyaya-8']


def run(conn, dry_run: bool) -> None:
    tagged_total = 0

    with conn.cursor() as cur:
        # 1. Full-text tagging
        for ext_id in FULL_TEXT_IDS:
            cur.execute(
                "SELECT id FROM canon_texts WHERE external_id = %s",
                (ext_id,),
            )
            row = cur.fetchone()
            if not row:
                print(f'  [SKIP] {ext_id} — not found in canon_texts')
                continue
            text_id = row['id']
            cur.execute(
                "SELECT COUNT(*) as n FROM document_chunks WHERE text_id = %s",
                (text_id,),
            )
            count = cur.fetchone()['n']
            if count == 0:
                print(f'  [SKIP] {ext_id} — 0 chunks')
                continue
            if not dry_run:
                cur.execute(
                    "UPDATE document_chunks SET collection = 'eol' WHERE text_id = %s",
                    (text_id,),
                )
            print(f'  {"[DRY]" if dry_run else "OK  "} {ext_id:<45} {count:>5} chunks tagged')
            tagged_total += count

        # 2. Chapter-level tagging
        for ext_id, chapters in CHAPTER_RULES:
            if chapters is None:
                continue  # already handled in full-text block
            cur.execute(
                "SELECT id FROM canon_texts WHERE external_id = %s",
                (ext_id,),
            )
            row = cur.fetchone()
            if not row:
                print(f'  [SKIP] {ext_id} — not found')
                continue
            text_id = row['id']
            ph = ','.join(['%s'] * len(chapters))
            cur.execute(
                f"SELECT COUNT(*) as n FROM document_chunks WHERE text_id = %s AND chapter IN ({ph})",
                [text_id] + chapters,
            )
            count = cur.fetchone()['n']
            if count == 0:
                print(f'  [SKIP] {ext_id} ch={chapters} — 0 chunks matched')
                continue
            if not dry_run:
                cur.execute(
                    f"UPDATE document_chunks SET collection = 'eol' WHERE text_id = %s AND chapter IN ({ph})",
                    [text_id] + chapters,
                )
            print(f'  {"[DRY]" if dry_run else "OK  "} {ext_id:<45} ch={str(chapters):<30} {count:>4} chunks')
            tagged_total += count

    if not dry_run:
        conn.commit()

    print(f'\nTotal chunks tagged as eol: {tagged_total:,}')
    if dry_run:
        print('(dry run — no changes written)')


def main():
    parser = argparse.ArgumentParser(description='Tag EOL collection in document_chunks')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no DB writes')
    parser.add_argument('--db-url', help='Override DATABASE_URL')
    args = parser.parse_args()

    db_url = args.db_url or os.environ.get('DATABASE_URL')
    if not db_url:
        raise SystemExit('DATABASE_URL not set.')

    from db import get_conn
    conn = get_conn(db_url)

    mode = 'DRY RUN' if args.dry_run else 'LIVE'
    print(f'\n=== EOL Collection Tagging — {mode} ===\n')
    run(conn, args.dry_run)
    conn.close()


if __name__ == '__main__':
    main()
