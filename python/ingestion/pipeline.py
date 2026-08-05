"""
Ingestion orchestrator — run any combination of corpus pipelines.

Usage:
  python ingestion/pipeline.py --source suttacentral --nikayas mn dn
  python ingestion/pipeline.py --source suttacentral --nikayas mn dn sn an
  python ingestion/pipeline.py --source 84000
  python ingestion/pipeline.py --source 84000 --force
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
    parser.add_argument('--source', required=True,
                        choices=['suttacentral', '84000', 'cbeta', 'bdrc', 'gretil', 'all'],
                        help='Corpus to ingest')
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


if __name__ == '__main__':
    main()
