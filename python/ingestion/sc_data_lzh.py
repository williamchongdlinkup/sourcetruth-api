"""
SuttaCentral sc-data — Classical Chinese (lzh) legacy HTML ingestion.

Source: https://github.com/suttacentral/sc-data
Path:   html_text/lzh/sutta/*  and  html_text/lzh/vinaya/*
Format: SuttaCentral legacy HTML (BeautifulSoup parsed)
License: CC0 — same as bilara-data
Tradition: early-buddhist (Sarvāstivāda / Dharmaguptaka / Mahāsāṃghika)

Coverage (sutta):
  da        長阿含經  Dīrgha Āgama T1        Dharmaguptaka
  da-ot     Long Discourse parallel texts
  ma        中阿含經  Madhyama Āgama T26      Sarvāstivāda
  ma-ot     Medium Discourse parallel texts
  sa        雜阿含經  Saṃyukta Āgama T99      Sarvāstivāda
  sa-2      雜阿含經  Shorter Saṃyukta T100
  sa-3      Saṃyukta Āgama (3rd recension)
  sa-ot     Saṃyukta parallel texts
  ea        增一阿含經 Ekottarika Āgama T125  Mahāsāṃghika
  ea-2      Ekottarika (2nd recension)
  ea-ot     Ekottarika parallel texts
  lzh-dhp   法句經   Chinese Dhammapada
  lzh-nbs   Numerical Body Sutras

Note: sc-data HTML footer cites CBETA as a data source. SuttaCentral publishes
under CC0 (their own HTML encoding). Confirm with SuttaCentral before public launch.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from itertools import groupby
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one
from embed import embed_documents
from normalisation.entity_resolver import EntityResolver

# ── Constants ─────────────────────────────────────────────────────────────────

REPO_URL  = 'https://github.com/suttacentral/sc-data.git'
DATA_DIR  = Path(os.getenv('DATA_DIR', 'data'))
REPO_DIR  = DATA_DIR / 'raw' / 'sc-data-lzh'

# CJK chunking: 800 chars ≈ 400 Voyage tokens
CHUNK_TARGET_CHARS = 800
CHUNK_MAX_CHARS    = 1200

TEXT_BATCH  = 10
MAX_RETRIES = 5
RETRY_DELAY = 30

_GIT_WIN_PATHS = [
    r'C:\Program Files\Git\mingw64\bin\git.exe',
    r'C:\Program Files\Git\cmd\git.exe',
]

# Sutta collections to ingest by default (vinaya excluded at launch)
DEFAULT_SUTTA_COLLECTIONS = [
    'da', 'da-ot',
    'ma', 'ma-ot',
    'sa', 'sa-2', 'sa-3', 'sa-ot',
    'ea', 'ea-2', 'ea-ot',
    'lzh-dhp', 'lzh-nbs',
]

# Collection metadata: code → (collection_slug, full_name, tradition)
COLLECTION_META = {
    'da':       ('agama-da',       '長阿含經 Dīrgha Āgama',           'dharmaguptaka'),
    'da-ot':    ('agama-da-other', 'Dīrgha Āgama parallel texts',     'early-buddhist'),
    'ma':       ('agama-ma',       '中阿含經 Madhyama Āgama',          'sarvastivada'),
    'ma-ot':    ('agama-ma-other', 'Madhyama Āgama parallel texts',   'early-buddhist'),
    'sa':       ('agama-sa',       '雜阿含經 Saṃyukta Āgama',          'sarvastivada'),
    'sa-2':     ('agama-sa-2',     '雜阿含經 Shorter Saṃyukta',        'early-buddhist'),
    'sa-3':     ('agama-sa-3',     'Saṃyukta Āgama (3rd recension)',  'early-buddhist'),
    'sa-ot':    ('agama-sa-other', 'Saṃyukta parallel texts',         'early-buddhist'),
    'ea':       ('agama-ea',       '增一阿含經 Ekottarika Āgama',       'mahasanghika'),
    'ea-2':     ('agama-ea-2',     'Ekottarika Āgama (2nd recension)','early-buddhist'),
    'ea-ot':    ('agama-ea-other', 'Ekottarika parallel texts',       'early-buddhist'),
    'lzh-dhp':  ('dhp-lzh',        '法句經 Chinese Dhammapada',        'early-buddhist'),
    'lzh-nbs':  ('nbs-lzh',        'Numerical Body Sutras',           'early-buddhist'),
    'lzh-dharani': ('dharani-lzh', 'Dhāraṇī texts',                  'early-buddhist'),
    # Vinaya
    'lzh-dg-bu-vb':   ('vinaya-dg',   'Dharmaguptaka Bhikṣu Vibhaṅga', 'dharmaguptaka'),
    'lzh-mg-bu-vb':   ('vinaya-mg',   'Mahāsāṃghika Bhikṣu Vibhaṅga',  'mahasanghika'),
    'lzh-mi-bu-vb':   ('vinaya-mi',   'Mahīśāsaka Bhikṣu Vibhaṅga',    'mahisasaka'),
    'lzh-sarv-bu-vb': ('vinaya-sarv', 'Sarvāstivāda Bhikṣu Vibhaṅga',  'sarvastivada'),
}

_PAGE_REF_RE = re.compile(r'\b[a-z]\d{4}[abc]\d{2}\b')  # e.g. t0026a01
_WS_RE       = re.compile(r'\s+')


def _git() -> str:
    found = shutil.which('git')
    if found:
        return found
    for p in _GIT_WIN_PATHS:
        if os.path.exists(p):
            return p
    return 'git'


# ── Phase A: Clone ─────────────────────────────────────────────────────────────

def clone_or_update_repo() -> None:
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    git = _git()

    if (REPO_DIR / '.git').exists():
        print('Expanding sc-data sparse-checkout and pulling...')
        subprocess.run(
            [git, '-C', str(REPO_DIR), 'sparse-checkout', 'set', 'html_text/lzh'],
            check=True,
        )
        subprocess.run([git, '-C', str(REPO_DIR), 'pull', '--depth=1'], check=True)
        return

    print('Cloning suttacentral/sc-data (sparse, lzh only — may take a few minutes)...')
    subprocess.run([
        git, 'clone', '--depth=1', '--filter=blob:none', '--sparse',
        REPO_URL, str(REPO_DIR),
    ], check=True)
    subprocess.run(
        [git, '-C', str(REPO_DIR), 'sparse-checkout', 'set', 'html_text/lzh'],
        check=True,
    )
    subprocess.run([git, '-C', str(REPO_DIR), 'checkout'], check=True)
    print('Clone complete.')


# ── Phase B: File discovery ────────────────────────────────────────────────────

def find_collection_files(collection: str, section: str = 'sutta') -> list[Path]:
    """Return all .html files for a given collection code under sutta/ or vinaya/."""
    coll_dir = REPO_DIR / 'html_text' / 'lzh' / section / collection
    if not coll_dir.exists():
        # Try vinaya section if not found in sutta
        coll_dir = REPO_DIR / 'html_text' / 'lzh' / 'vinaya' / collection
    if not coll_dir.exists():
        return []
    return sorted(coll_dir.rglob('*.html'))


# ── Phase C: Parse HTML ────────────────────────────────────────────────────────

def _clean_elem(elem) -> str:
    """Extract text from a BS4 element, stripping ref anchors and notes."""
    for tag in elem.find_all(['a'], class_='ref'):
        tag.decompose()
    for tag in elem.find_all('span', class_='t-note'):
        tag.decompose()
    text = elem.get_text(' ', strip=True)
    text = _PAGE_REF_RE.sub('', text)
    text = _WS_RE.sub(' ', text).strip()
    return text


def parse_sc_html(path: Path, collection: str) -> dict | None:
    """
    Parse a SuttaCentral legacy HTML file.

    Returns:
      { uid, title_zh, title_en, translator, taisho_ref,
        collection_slug, tradition, paragraphs }
    where paragraphs = [{ text, chapter, is_verse }]
    """
    try:
        raw  = path.read_bytes()
        soup = BeautifulSoup(raw, 'html.parser')
    except Exception as e:
        print(f'  [WARN] {path.name}: parse error — {e}')
        return None

    uid = path.stem  # e.g. 'ma1', 'sa1', 'da1'

    # ── Title ────────────────────────────────────────────────────────────────
    title_zh = ''
    title_en = ''
    header = soup.find('header', class_='mirror')
    if header:
        divs = header.find_all(['div', 'span', 'p'])
        texts = [d.get_text(strip=True) for d in divs if d.get_text(strip=True)]
        for t in texts:
            if any('一' <= c <= '鿿' for c in t) and not title_zh:
                title_zh = t
            elif t and not title_en and not any('一' <= c <= '鿿' for c in t):
                title_en = t

    # Fallback: page <title>
    if not title_zh and soup.title:
        title_zh = soup.title.string or ''

    # ── Metadata ─────────────────────────────────────────────────────────────
    translator = ''
    taisho_ref = ''
    suttainfo  = soup.find(class_='suttainfo') or soup.find(id='suttainfo')
    if suttainfo:
        info_text = suttainfo.get_text(' ', strip=True)
        # Taisho reference: look for T\d+ or vol./no. pattern
        m = re.search(r'\bT(\d+)\b', info_text)
        if m:
            taisho_ref = f'T{m.group(1)}'
        # Translator: Chinese name often follows 譯 or 翻譯
        m2 = re.search(r'([一-鿿]{2,6})(?:譯|翻譯|漢譯)', info_text)
        if m2:
            translator = m2.group(1)

    meta = COLLECTION_META.get(collection, ('unknown', collection, 'early-buddhist'))
    collection_slug, _, tradition = meta

    # ── Main content ─────────────────────────────────────────────────────────
    # Find the article/main/body — skip header and suttainfo
    body  = soup.find('article') or soup.find('main') or soup.find('body')
    if not body:
        return None

    paragraphs: list[dict] = []
    current_chapter = ''

    for elem in body.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'p', 'blockquote', 'div'], recursive=False):
        tag = elem.name

        # Skip header mirror and suttainfo containers
        classes = elem.get('class') or []
        if 'mirror' in classes or 'suttainfo' in classes:
            continue

        # Chapter / section headings
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5'):
            text = _clean_elem(elem)
            if text:
                current_chapter = text
            continue

        # Verse blocks
        if tag == 'blockquote' and 'gatha' in classes:
            text = _clean_elem(elem)
            if len(text) >= 10:
                paragraphs.append({'text': text, 'chapter': current_chapter, 'is_verse': True})
            continue

        # Prose paragraphs
        if tag == 'p':
            text = _clean_elem(elem)
            if len(text) >= 15:
                paragraphs.append({'text': text, 'chapter': current_chapter, 'is_verse': False})
            continue

        # Recurse into generic divs that may contain p/blockquote
        if tag == 'div':
            for child in elem.find_all(['h4', 'h5', 'p', 'blockquote'], recursive=True):
                child_classes = child.get('class') or []
                if child.name in ('h4', 'h5'):
                    t = _clean_elem(child)
                    if t:
                        current_chapter = t
                elif child.name == 'blockquote' and 'gatha' in child_classes:
                    t = _clean_elem(child)
                    if len(t) >= 10:
                        paragraphs.append({'text': t, 'chapter': current_chapter, 'is_verse': True})
                elif child.name == 'p':
                    t = _clean_elem(child)
                    if len(t) >= 15:
                        paragraphs.append({'text': t, 'chapter': current_chapter, 'is_verse': False})

    if not paragraphs:
        return None

    return {
        'uid':            uid,
        'title_zh':       title_zh or uid,
        'title_en':       title_en or '',
        'translator':     translator,
        'taisho_ref':     taisho_ref,
        'collection_slug': collection_slug,
        'tradition':      tradition,
        'paragraphs':     paragraphs,
    }


# ── Phase D: Chunking ─────────────────────────────────────────────────────────

def chunk_paragraphs(paragraphs: list[dict], uid: str) -> list[dict]:
    """Group paragraphs into ~800-char chunks (≈ 400 CJK tokens)."""
    chunks: list[dict] = []
    current: list[dict] = []
    current_chars = 0
    seq = 0

    def flush() -> None:
        nonlocal current, current_chars, seq
        if not current:
            return
        text    = '\n\n'.join(p['text'] for p in current)
        chapter = next((p['chapter'] for p in current if p.get('chapter')), '')
        verse   = any(p.get('is_verse', False) for p in current)
        seq    += 1
        chunks.append({
            'text':      text,
            'reference': f'{uid} §{seq}',
            'chapter':   chapter,
            'section':   '',
            'is_verse':  verse,
        })
        current       = []
        current_chars = 0

    for para in paragraphs:
        plen = len(para['text'])
        if current_chars + plen > CHUNK_MAX_CHARS and current:
            flush()
        current.append(para)
        current_chars += plen
        if current_chars >= CHUNK_TARGET_CHARS:
            flush()

    flush()
    return chunks


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_or_create_text(conn, corpus_id: int, parsed: dict) -> int:
    eid = parsed['uid']
    existing = execute_one(
        conn, 'SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s',
        (corpus_id, eid),
    )
    if existing:
        return existing['id']

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO canon_texts
                (corpus_id, external_id, title_original, title_english,
                 tradition, language, collection, number, translator, url, word_count)
            VALUES (%s,%s,%s,%s,%s,'lzh',%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            corpus_id,
            eid,
            parsed['title_zh'] or None,
            parsed['title_en'] or None,
            parsed['tradition'],
            parsed['collection_slug'],
            parsed['taisho_ref'] or None,
            parsed['translator'] or None,
            f'https://suttacentral.net/{eid}',
            sum(len(p['text']) // 2 for p in parsed['paragraphs']),
        ))
        return cur.fetchone()['id']


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(
    collections: list[str] | None = None,
    force: bool = False,
    skip_clone: bool = False,
) -> None:
    """Ingest SuttaCentral sc-data Classical Chinese legacy texts."""

    if not skip_clone:
        clone_or_update_repo()

    target = collections or DEFAULT_SUTTA_COLLECTIONS

    conn      = get_conn()
    corpus    = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'sc-data-lzh'")
    if not corpus:
        # Register corpus on first run
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
                VALUES ('sc-data-lzh',
                        'SuttaCentral Legacy Chinese Canon',
                        'early-buddhist', 'lzh', 'CC0',
                        'https://github.com/suttacentral/sc-data')
                ON CONFLICT (code) DO NOTHING
            """)
        conn.commit()
        corpus = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'sc-data-lzh'")
    corpus_id = corpus['id']

    resolver  = EntityResolver(conn)
    _variants = resolver.get_all_variants()
    _name_map: dict[str, list[int]] = {}
    for v in _variants:
        _name_map.setdefault(v['name_text'].lower(), []).append(v['entity_id'])

    def _entity_ids(text: str) -> list[int]:
        t = text.lower()
        return list({eid for name, eids in _name_map.items() if name in t for eid in eids})

    # Prefetch already-ingested state
    existing_rows = execute(conn,
        'SELECT external_id, id FROM canon_texts WHERE corpus_id = %s', (corpus_id,))
    eid_to_tid: dict[str, int] = {r['external_id']: r['id'] for r in existing_rows}
    done_tids: set[int] = set()
    if eid_to_tid and not force:
        done_rows = execute(conn,
            'SELECT DISTINCT text_id FROM document_chunks WHERE text_id = ANY(%s)',
            (list(eid_to_tid.values()),))
        done_tids = {r['text_id'] for r in done_rows}

    # ── Parse all target collections ──────────────────────────────────────────
    all_items:   list[dict] = []
    skipped = parse_errors   = 0

    for collection in target:
        files = find_collection_files(collection)
        if not files:
            print(f'  [INFO] {collection}: no files found (skipping)')
            continue

        meta = COLLECTION_META.get(collection, ('unknown', collection, 'early-buddhist'))
        print(f'\n{collection} ({meta[1]}): {len(files)} files')

        for path in tqdm(files, desc=collection, unit='file'):
            parsed = parse_sc_html(path, collection)
            if not parsed:
                parse_errors += 1
                continue

            eid = parsed['uid']
            existing_tid = eid_to_tid.get(eid)
            if existing_tid and existing_tid in done_tids and not force:
                skipped += 1
                continue

            chunks = chunk_paragraphs(parsed['paragraphs'], eid)
            if not chunks:
                parse_errors += 1
                continue

            try:
                text_id = get_or_create_text(conn, corpus_id, parsed)
                eid_to_tid[eid] = text_id
            except Exception as e:
                print(f'  [WARN] {eid}: DB error — {e}')
                conn.rollback()
                parse_errors += 1
                continue

            if force and existing_tid:
                with conn.cursor() as cur:
                    cur.execute('DELETE FROM document_chunks WHERE text_id = %s', (text_id,))
                conn.commit()

            for i, chunk in enumerate(chunks):
                all_items.append({
                    'text_id':     text_id,
                    'chunk_index': i,
                    'text':        chunk['text'],
                    'reference':   chunk['reference'],
                    'chapter':     chunk['chapter'],
                    'section':     chunk['section'],
                    'word_count':  len(chunk['text']) // 2,
                    'token_count': len(chunk['text']) // 2,
                    'entity_ids':  _entity_ids(chunk['text']),
                    'language':    'lzh',
                    'tradition':   parsed['tradition'],
                    'corpus_code': 'sc-data-lzh',
                    'collection':  parsed['collection_slug'],
                    'is_verse':    chunk.get('is_verse', False),
                })

    conn.commit()
    if skipped:
        print(f'\nSkipped {skipped} already-ingested texts')
    if parse_errors:
        print(f'{parse_errors} files produced no usable text')
    if not all_items:
        print('Nothing new to embed.')
        conn.close()
        return

    # ── Embed and write ───────────────────────────────────────────────────────
    all_items.sort(key=lambda x: x['text_id'])
    text_groups = [
        (tid, list(items))
        for tid, items in groupby(all_items, key=lambda x: x['text_id'])
    ]
    total_texts  = len(text_groups)
    total_chunks = len(all_items)
    print(f'\nsc-data lzh — embedding {total_chunks:,} chunks across {total_texts} texts '
          f'(batches of {TEXT_BATCH})...')

    write_errors = written_chunks = 0

    for batch_idx in range(0, total_texts, TEXT_BATCH):
        batch_groups  = text_groups[batch_idx:batch_idx + TEXT_BATCH]
        batch_items   = [item for _, items in batch_groups for item in items]
        batch_num     = batch_idx // TEXT_BATCH + 1
        total_batches = (total_texts + TEXT_BATCH - 1) // TEXT_BATCH

        print(f'  [{batch_num}/{total_batches}] {len(batch_items)} chunks — embedding...')

        embeddings = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = embed_documents([item['text'] for item in batch_items])
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    print(f'  [WARN] Voyage attempt {attempt}/{MAX_RETRIES}: {e}. '
                          f'Retrying in {RETRY_DELAY}s...')
                    time.sleep(RETRY_DELAY)
                else:
                    print(f'  [ERROR] Voyage failed after {MAX_RETRIES} attempts: {e}')
                    write_errors += len(batch_items)

        if embeddings is None:
            continue

        offset = 0
        for _tid, items in batch_groups:
            n          = len(items)
            group_embs = embeddings[offset:offset + n]
            offset    += n
            try:
                for item, emb in zip(items, group_embs):
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO document_chunks
                                (text_id, chunk_index, chunk_text, reference,
                                 chapter, section, word_count, token_count, entity_ids,
                                 language, tradition, corpus_code, collection, is_verse)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            RETURNING id
                        """, (
                            item['text_id'], item['chunk_index'], item['text'],
                            item['reference'], item['chapter'], item['section'],
                            item['word_count'], item['token_count'], item['entity_ids'],
                            item['language'], item['tradition'], item['corpus_code'],
                            item['collection'], item['is_verse'],
                        ))
                        chunk_id = cur.fetchone()['id']
                    with conn.cursor() as cur:
                        cur.execute(
                            'INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)',
                            (chunk_id, emb),
                        )
                conn.commit()
                written_chunks += n
            except Exception as e:
                print(f'  [WARN] write error for text_id {_tid}: {e}')
                try:
                    conn.rollback()
                except Exception:
                    conn = get_conn()
                write_errors += n

        print(f'  [{batch_num}/{total_batches}] committed — '
              f'{written_chunks:,}/{total_chunks:,} chunks')

    conn.close()
    total = len(all_items) - write_errors
    print(f'\nsc-data lzh done. {total:,} chunks ingested'
          + (f', {write_errors} write errors' if write_errors else '') + '.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest SuttaCentral sc-data Chinese canon')
    parser.add_argument('--collections', nargs='+', default=None,
                        help='Specific collections to ingest (e.g. da ma sa ea)')
    parser.add_argument('--skip-clone', action='store_true')
    parser.add_argument('--force',      action='store_true')
    args = parser.parse_args()
    run(collections=args.collections, force=args.force, skip_clone=args.skip_clone)
