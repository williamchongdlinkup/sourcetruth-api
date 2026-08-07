"""
Ingestion orchestrator — run any combination of corpus pipelines.

Usage:
  python ingestion/pipeline.py --source suttacentral --nikayas mn dn
  python ingestion/pipeline.py --source quran
  python ingestion/pipeline.py --source hadith
  python ingestion/pipeline.py --source tanakh
  python ingestion/pipeline.py --source mishnah
  python ingestion/pipeline.py --source kjv
  python ingestion/pipeline.py --source bhagavad-gita
  python ingestion/pipeline.py --source upanishads
  python ingestion/pipeline.py --source greek-philosophy
  python ingestion/pipeline.py --source classical-latin
  python ingestion/pipeline.py --source classical-chinese
  python ingestion/pipeline.py --source sanskrit-classical
  python ingestion/pipeline.py --source sikh
  python ingestion/pipeline.py --source zoroastrian
  python ingestion/pipeline.py --source all
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description='CanonRAG ingestion orchestrator')
    SOURCES = [
        'suttacentral', '84000', 'cbeta', 'bdrc', 'gretil',
        'quran', 'hadith', 'tanakh', 'mishnah', 'kjv',
        'bhagavad-gita', 'upanishads', 'greek-philosophy',
        'classical-latin', 'classical-chinese', 'sanskrit-classical',
        'sikh', 'zoroastrian',
        'bible-web', 'bible-asv', 'bible-ylt', 'rigveda', 'all',
    ]
    parser.add_argument('--source', required=True, choices=SOURCES, help='Corpus to ingest')
    parser.add_argument('--nikayas', nargs='+', default=None,
                        help='For suttacentral: which nikayas (mn, dn, sn, an, kn)')
    parser.add_argument('--skip-clone', action='store_true',
                        help='Skip clone/download step (suttacentral, 84000, gretil)')
    parser.add_argument('--force', action='store_true',
                        help='Re-embed and overwrite already-ingested texts')
    args = parser.parse_args()

    if args.source in ('suttacentral', 'all'):
        from ingestion.suttacentral import run as sc_run
        sc_run(nikayas=args.nikayas, skip_clone=args.skip_clone, force=args.force)

    if args.source in ('84000', 'all'):
        from ingestion.eighty4000 import run as e84_run
        e84_run(force=args.force, skip_clone=args.skip_clone)

    if args.source in ('cbeta', 'all'):
        from ingestion.cbeta import run as cbeta_run
        nikayas = args.nikayas or []
        cbeta_run(
            force=args.force,
            skip_clone=args.skip_clone,
            db_url=os.getenv('DATABASE_URL_3'),
            target_texts=nikayas if nikayas and nikayas != ['all'] else None,
            all_texts=(not nikayas or nikayas == ['all']),
        )

    if args.source in ('bdrc', 'all'):
        print('[INFO] BDRC ingestion not yet implemented (see ingestion/bdrc_scope.py). Skipping.')

    if args.source in ('gretil', 'all'):
        from ingestion.gretil import run as gretil_run
        gretil_run(force=args.force, skip_dl=args.skip_clone,
                   db_url=os.getenv('DATABASE_URL_2'))

    if args.source in ('quran', 'all'):
        from ingestion.quran import run as quran_run
        quran_run(force=args.force)

    if args.source in ('hadith', 'all'):
        from ingestion.hadith import run as hadith_run
        hadith_run(force=args.force)

    if args.source in ('tanakh', 'all'):
        from ingestion.tanakh import run as tanakh_run
        tanakh_run(force=args.force)

    if args.source in ('mishnah', 'all'):
        from ingestion.mishnah import run as mishnah_run
        mishnah_run(force=args.force)

    if args.source in ('kjv', 'all'):
        from ingestion.kjv import run as kjv_run
        kjv_run(force=args.force)

    if args.source in ('bhagavad-gita', 'all'):
        from ingestion.bhagavad_gita import run as bg_run
        bg_run(force=args.force)

    if args.source in ('upanishads', 'all'):
        from ingestion.upanishads import run as up_run
        up_run(force=args.force)

    if args.source in ('greek-philosophy', 'all'):
        from ingestion.greek_philosophy import run as greek_run
        greek_run(force=args.force)

    if args.source in ('classical-latin', 'all'):
        from ingestion.classical_latin import run as latin_run
        latin_run(force=args.force)

    if args.source in ('classical-chinese', 'all'):
        from ingestion.classical_chinese import run as chinese_run
        chinese_run(force=args.force)

    if args.source in ('sanskrit-classical', 'all'):
        from ingestion.sanskrit_classical import run as sanskrit_run
        sanskrit_run(force=args.force)

    if args.source in ('sikh', 'all'):
        from ingestion.sikh_scriptures import run as sikh_run
        sikh_run(force=args.force)

    if args.source in ('zoroastrian', 'all'):
        from ingestion.zoroastrian import run as zoro_run
        zoro_run(force=args.force)

    if args.source in ('bible-web', 'bible-asv', 'bible-ylt', 'all'):
        trans = []
        for t in ['web', 'asv', 'ylt']:
            if args.source in (f'bible-{t}', 'all'):
                trans.append(t)
        if trans:
            from ingestion.bible_multi import run as bible_run
            bible_run(translations=trans, force=args.force)

    if args.source in ('rigveda', 'all'):
        from ingestion.rigveda import run as rigveda_run
        rigveda_run(force=args.force)


if __name__ == '__main__':
    main()
