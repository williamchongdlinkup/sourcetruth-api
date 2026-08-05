"""
84000: Translating the Words of the Buddha — ingestion pipeline.

Source: https://github.com/84000/data-tei
Format: TEI P5 XML
Texts: 396 Kangyur + 3 Tengyur English translations (~400 texts, ~8K chunks)
Tradition: vajrayana · Language: en · Into: Account 1 (existing Supabase)

Phase A — clone:  sparse-checkout data-tei repo (translations/ dirs only)
Phase B — parse:  extract paragraphs/verses from each TEI file
Phase C — embed:  batch embed all new chunks (O(chunks/128) Voyage calls)
Phase D — write:  insert chunks + embeddings into DB

Usage:
  python ingestion/eighty4000.py
  python ingestion/eighty4000.py --force
  python ingestion/eighty4000.py --skip-clone   # repo already cloned
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one
from embed import embed_documents
from normalisation.entity_resolver import EntityResolver

# ── Constants ────────────────────────────────────────────────────────────────

TEI_NS = 'http://www.tei-c.org/ns/1.0'
XML_NS = 'http://www.w3.org/XML/1998/namespace'

REPO_URL  = 'https://github.com/84000/data-tei.git'
DATA_DIR  = Path(os.getenv('DATA_DIR', 'data'))
REPO_DIR  = DATA_DIR / 'raw' / '84000-tei'

# These subdirs within the repo contain actual published translations
TRANSLATION_DIRS = [
    'translations/kangyur/translations',
    'translations/tengyur/publications',
]

CHUNK_TARGET_TOKENS = 400
CHUNK_MAX_TOKENS    = 600

# Common Windows Git installation paths (fallback when git not in PATH)
_GIT_WIN_PATHS = [
    r'C:\Program Files\Git\mingw64\bin\git.exe',
    r'C:\Program Files\Git\cmd\git.exe',
    r'C:\Program Files (x86)\Git\mingw64\bin\git.exe',
]


def _git() -> str:
    """Return path to git executable."""
    found = shutil.which('git')
    if found:
        return found
    for p in _GIT_WIN_PATHS:
        if os.path.exists(p):
            return p
    return 'git'  # last resort — will raise FileNotFoundError with clear message


# ── Phase A: Clone repo ───────────────────────────────────────────────────────

def clone_or_update_repo():
    """Sparse-checkout 84000/data-tei — only translation subdirs we need."""
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    git = _git()

    if (REPO_DIR / '.git').exists():
        print('Updating data-tei repo...')
        subprocess.run([git, '-C', str(REPO_DIR), 'pull', '--depth=1'], check=True)
        return

    print('Cloning 84000/data-tei (sparse, depth=1 — may take a few minutes)...')
    subprocess.run([
        git, 'clone', '--depth=1',
        '--filter=blob:none',
        '--sparse',
        REPO_URL, str(REPO_DIR),
    ], check=True)

    dirs_arg = list(TRANSLATION_DIRS)
    subprocess.run(
        [git, '-C', str(REPO_DIR), 'sparse-checkout', 'set'] + dirs_arg,
        check=True,
    )
    subprocess.run([git, '-C', str(REPO_DIR), 'checkout'], check=True)
    print('Clone complete.')


def find_translation_files() -> list[Path]:
    """Return all .xml translation files from the checked-out sparse repo."""
    files = []
    for subdir in TRANSLATION_DIRS:
        d = REPO_DIR / subdir
        if d.exists():
            files.extend(sorted(d.glob('*.xml')))
    return files


# ── Phase B: Parse TEI ────────────────────────────────────────────────────────

def _toh_from_filename(path: Path) -> str:
    """Extract Toh-based external ID from filename.

    Examples:
      001-001_toh1-1_chapter_on_going_forth.xml  → toh1-1
      093-021_toh706-the_hundred_and...xml       → toh706
      toh8.xml                                   → toh8
    """
    stem = path.stem
    m = re.search(r'toh(\d+(?:-\d+)?)', stem)
    return f'toh{m.group(1)}' if m else stem


def _detect_collection(root, toh_id: str) -> str:
    """Infer Kangyur/Tengyur section from TEI series metadata or Toh range."""
    series_texts = [
        ''.join(s.itertext()).lower()
        for s in root.findall(f'.//{{{TEI_NS}}}series')
    ]
    combined = ' '.join(series_texts)

    if 'tengyur' in combined:
        return 'tengyur'
    for keyword, label in [
        ('tantra',         'kangyur-tantra'),
        ('prajnaparamita', 'kangyur-prajnaparamita'),
        ('prajnapāramitā', 'kangyur-prajnaparamita'),
        ('avatamsaka',     'kangyur-avatamsaka'),
        ('ratnakuta',      'kangyur-ratnakuta'),
        ('general sutra',  'kangyur-sutra'),
        ('vinaya',         'kangyur-vinaya'),
    ]:
        if keyword in combined:
            return label

    # Fall back to Derge Kangyur Toh number ranges
    try:
        base = int(re.search(r'\d+', toh_id).group())
        if base <= 7:   return 'kangyur-vinaya'
        if base <= 12:  return 'kangyur-prajnaparamita'
        if base <= 93:  return 'kangyur-avatamsaka'
        if base <= 287: return 'kangyur-ratnakuta'
        if base <= 359: return 'kangyur-sutra'
        if base <= 828: return 'kangyur-tantra'
        return 'tengyur'
    except (AttributeError, ValueError):
        return 'kangyur'


def parse_tei(xml_path: Path) -> dict | None:
    """
    Parse a 84000 TEI P5 file.

    Returns {toh_id, title_en, title_tibetan, translator, collection, paragraphs}
    where paragraphs is a list of {text, chapter, chunk_ref}.
    """
    try:
        from lxml import etree
        root = etree.fromstring(xml_path.read_bytes())
    except Exception as e:
        print(f'  [WARN] {xml_path.name}: XML parse error — {e}')
        return None

    toh_id = _toh_from_filename(xml_path)

    # ── Titles ──────────────────────────────────────────────────────────────
    title_en = ''
    title_tib = ''
    for t in root.findall(f'.//{{{TEI_NS}}}titleStmt/{{{TEI_NS}}}title'):
        lang = t.get(f'{{{XML_NS}}}lang', '')
        val = ''.join(t.itertext()).strip()
        if lang == 'en' and not title_en:
            title_en = val
        elif lang.startswith('bo') and not title_tib:
            title_tib = val

    # ── Translator ──────────────────────────────────────────────────────────
    translator = ''
    for author in root.findall(f'.//{{{TEI_NS}}}titleStmt/{{{TEI_NS}}}author'):
        role = author.get('role', '').lower()
        val = ''.join(author.itertext()).strip()
        if 'translatoreng' in role.replace('-', ''):
            if not translator:
                translator = val
        elif 'translatormain' in role.replace('-', '') and not translator:
            translator = val

    collection = _detect_collection(root, toh_id)

    # ── Translation body ─────────────────────────────────────────────────────
    translation_div = root.find(
        f'.//{{{TEI_NS}}}text/{{{TEI_NS}}}body/{{{TEI_NS}}}div[@type="translation"]'
    )
    if translation_div is None:
        translation_div = root.find(f'.//{{{TEI_NS}}}text/{{{TEI_NS}}}body')
    if translation_div is None:
        return None

    paragraphs = _extract_paragraphs(translation_div)
    if not paragraphs:
        return None

    return {
        'toh_id':        toh_id,
        'title_en':      title_en or toh_id,
        'title_tibetan': title_tib,
        'translator':    translator,
        'collection':    collection,
        'paragraphs':    paragraphs,
    }


def _extract_paragraphs(div) -> list[dict]:
    """
    Walk a TEI translation div and extract text passages.

    Tracks:
      - <milestone unit="chunk" xml:id="..."> for 84000's own chunk IDs
      - <head> for chapter/section headings
    Skips: <note>, <back>.
    """
    from lxml import etree

    paragraphs: list[dict] = []
    current_chapter = ''
    current_chunk_ref = ''

    def _clean(elem) -> str:
        return re.sub(r'\s+', ' ', ''.join(elem.itertext())).strip()

    def walk(elem, in_note: bool = False):
        nonlocal current_chapter, current_chunk_ref

        if in_note:
            return

        try:
            local = etree.QName(elem.tag).localname
        except Exception:
            return

        if local in ('note', 'back'):
            return

        # Track 84000's internal chunk IDs as passage references
        if local == 'milestone' and elem.get('unit', '') == 'chunk':
            xml_id = elem.get(f'{{{XML_NS}}}id', '')
            if xml_id:
                current_chunk_ref = xml_id

        # Chapter headings
        if local == 'head':
            val = _clean(elem)
            if val:
                current_chapter = val
            return

        # Paragraphs
        if local == 'p':
            text = _clean(elem)
            if len(text) > 30:
                paragraphs.append({
                    'text':      text,
                    'chapter':   current_chapter,
                    'chunk_ref': current_chunk_ref,
                    'is_verse':  False,
                })
            return

        # Verse groups
        if local == 'lg':
            lines = [
                _clean(l_elem)
                for l_elem in elem
                if isinstance(l_elem.tag, str)
                and etree.QName(l_elem.tag).localname == 'l'
            ]
            text = '\n'.join(l for l in lines if l)
            if len(text) > 20:
                paragraphs.append({
                    'text':      text,
                    'chapter':   current_chapter,
                    'chunk_ref': current_chunk_ref,
                    'is_verse':  True,
                })
            return

        for child in elem:
            if isinstance(child.tag, str):  # skip comments and processing instructions
                walk(child, in_note=(local == 'note'))

    walk(div)
    return paragraphs


# ── Chunking ──────────────────────────────────────────────────────────────────

def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_paragraphs(paragraphs: list[dict], toh_id: str) -> list[dict]:
    """Group paragraphs into ~400-token chunks.

    Returns list of {text, reference, chapter, section}.
    """
    chunks: list[dict] = []
    current: list[dict] = []
    current_tokens = 0
    chunk_seq = 0

    def flush():
        nonlocal current, current_tokens, chunk_seq
        if not current:
            return
        text = '\n\n'.join(p['text'] for p in current)
        chapter = next((p['chapter'] for p in current if p['chapter']), '')

        # Use 84000's chunk ID if available
        ref_start = next((p['chunk_ref'] for p in current if p['chunk_ref']), '')
        ref_end = next((p['chunk_ref'] for p in reversed(current) if p['chunk_ref']), '')
        if ref_start:
            ref = ref_start if ref_start == ref_end else f'{ref_start}–{ref_end}'
        else:
            chunk_seq += 1
            ref = f'{toh_id} §{chunk_seq}'

        verse = any(p.get('is_verse', False) for p in current)
        chunks.append({'text': text, 'reference': ref, 'chapter': chapter, 'section': '', 'is_verse': verse})
        current = []
        current_tokens = 0

    for para in paragraphs:
        tok = _approx_tokens(para['text'])
        if current_tokens + tok > CHUNK_MAX_TOKENS and current:
            flush()
        current.append(para)
        current_tokens += tok
        if current_tokens >= CHUNK_TARGET_TOKENS:
            flush()

    flush()
    return chunks


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_or_create_text(conn, corpus_id: int, parsed: dict) -> int:
    toh_id = parsed['toh_id']
    existing = execute_one(
        conn, "SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s",
        (corpus_id, toh_id)
    )
    if existing:
        return existing['id']

    number = toh_id.replace('toh', '')
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO canon_texts
                (corpus_id, external_id, title_original, title_english,
                 tradition, language, collection, number, translator, url)
            VALUES (%s,%s,%s,%s,'vajrayana','en',%s,%s,%s,%s)
            RETURNING id
        """, (
            corpus_id, toh_id,
            parsed.get('title_tibetan') or None,
            parsed['title_en'] or None,
            parsed.get('collection', 'kangyur'),
            number,
            parsed.get('translator') or None,
            f'https://read.84000.co/translation/{toh_id}.html',
        ))
        return cur.fetchone()['id']


# ── Main ingestion ────────────────────────────────────────────────────────────

def run(force: bool = False, skip_clone: bool = False):
    """Ingest all published 84000 translations into CanonRAG."""

    # ── Phase A: Clone ───────────────────────────────────────────────────────
    if not skip_clone:
        clone_or_update_repo()

    xml_files = find_translation_files()
    print(f'\n84000: {len(xml_files)} translation files found')

    conn = get_conn()
    corpus = execute_one(conn, "SELECT id FROM source_corpora WHERE code = '84000'")
    if not corpus:
        raise RuntimeError("Corpus '84000' not in source_corpora. Re-run sql/02_schema.sql.")
    corpus_id = corpus['id']

    # Pre-load entity variants into memory for fast in-process matching
    # (avoids O(chunks) ILIKE DB round-trips — reduces parse phase from hours to minutes)
    resolver = EntityResolver(conn)
    _variants = resolver.get_all_variants()
    _name_to_eids: dict[str, list[int]] = {}
    for v in _variants:
        name = v['name_text'].lower()
        _name_to_eids.setdefault(name, []).append(v['entity_id'])

    def _entity_ids_fast(text: str) -> list[int]:
        t = text.lower()
        found: set[int] = set()
        for name, eids in _name_to_eids.items():
            if name in t:
                found.update(eids)
        return list(found)

    # ── Phase B: Parse ───────────────────────────────────────────────────────
    print('\n84000 Phase B — parsing TEI and chunking...')
    all_items: list[dict] = []
    skipped = 0
    parse_errors = 0

    for xml_path in tqdm(xml_files, desc='parse', unit='text'):
        parsed = parse_tei(xml_path)
        if not parsed:
            parse_errors += 1
            continue

        toh_id = parsed['toh_id']

        # Skip already-ingested texts unless --force
        existing_text = execute_one(
            conn, "SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s",
            (corpus_id, toh_id)
        )
        if existing_text and not force:
            count = execute_one(
                conn, "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s",
                (existing_text['id'],)
            )
            if count and count['n'] > 0:
                skipped += 1
                continue

        chunks = chunk_paragraphs(parsed['paragraphs'], toh_id)
        if not chunks:
            parse_errors += 1
            continue

        try:
            text_id = get_or_create_text(conn, corpus_id, parsed)
        except Exception as e:
            print(f'  [WARN] {toh_id}: text record error — {e}')
            conn.rollback()
            parse_errors += 1
            continue

        if force and existing_text:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))
            conn.commit()

        for i, chunk in enumerate(chunks):
            all_items.append({
                'text_id':     text_id,
                'chunk_index': i,
                'text':        chunk['text'],
                'reference':   chunk['reference'],
                'chapter':     chunk['chapter'],
                'section':     chunk['section'],
                'word_count':  len(chunk['text'].split()),
                'token_count': len(chunk['text']) // 4,
                'entity_ids':  _entity_ids_fast(chunk['text']),
                'language':    'en',
                'tradition':   'vajrayana',
                'corpus_code': '84000',
                'collection':  parsed.get('collection', 'kangyur'),
                'is_verse':    chunk.get('is_verse', False),
            })

    conn.commit()

    if skipped:
        print(f'  Skipped {skipped} already-ingested texts (use --force to re-embed)')
    if parse_errors:
        print(f'  {parse_errors} files could not be parsed')
    if not all_items:
        print('  Nothing new to embed.')
        conn.close()
        return

    # ── Phase C+D: Embed and write in per-text batches (with retry) ──────────
    # Groups chunks by text_id so each text is written atomically.
    # Embeds TEXT_BATCH texts at a time to limit per-call size and enable
    # checkpointing: if Voyage times out, previously committed texts are safe
    # and will be skipped on re-run via the skip-if-has-chunks logic above.

    import time as _time

    TEXT_BATCH   = 20    # texts per Voyage call group (~300–600 chunks)
    MAX_RETRIES  = 5
    RETRY_DELAY  = 30   # seconds between Voyage retry attempts

    # Group items by text_id (all_items is already ordered by text since we
    # iterate xml_files sequentially and append per text)
    from itertools import groupby as _groupby
    all_items.sort(key=lambda x: x['text_id'])
    text_groups = [
        (tid, list(items))
        for tid, items in _groupby(all_items, key=lambda x: x['text_id'])
    ]

    total_texts  = len(text_groups)
    total_chunks = len(all_items)
    print(f'\n84000 Phase C+D — embedding and writing {total_chunks:,} chunks '
          f'across {total_texts} texts (batches of {TEXT_BATCH})...')

    write_errors = 0
    written_chunks = 0

    for batch_idx in range(0, total_texts, TEXT_BATCH):
        batch_groups = text_groups[batch_idx:batch_idx + TEXT_BATCH]
        batch_items  = [item for _, items in batch_groups for item in items]
        batch_num    = batch_idx // TEXT_BATCH + 1
        total_batches = (total_texts + TEXT_BATCH - 1) // TEXT_BATCH

        print(f'  [{batch_num}/{total_batches}] {len(batch_items)} chunks '
              f'({len(batch_groups)} texts) — embedding...')

        # Embed with retry
        embeddings = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = embed_documents([item['text'] for item in batch_items])
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    print(f'  [WARN] Voyage attempt {attempt}/{MAX_RETRIES} failed: {e}. '
                          f'Retrying in {RETRY_DELAY}s...')
                    _time.sleep(RETRY_DELAY)
                else:
                    print(f'  [ERROR] Voyage failed after {MAX_RETRIES} attempts: {e}')
                    write_errors += len(batch_items)
                    embeddings = None

        if embeddings is None:
            continue

        # Write batch — each text atomically
        offset = 0
        for _tid, items in batch_groups:
            n = len(items)
            group_embs = embeddings[offset:offset + n]
            offset += n
            try:
                for item, emb in zip(items, group_embs):
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO document_chunks
                                (text_id, chunk_index, chunk_text, reference, chapter, section,
                                 word_count, token_count, entity_ids,
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
                            "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                            (chunk_id, emb),
                        )
                conn.commit()
                written_chunks += n
            except Exception as e:
                print(f'  [WARN] write error for text_id {_tid}: {e}')
                conn.rollback()
                write_errors += n

        print(f'  [{batch_num}/{total_batches}] committed — '
              f'{written_chunks:,}/{total_chunks:,} chunks total')

    conn.close()

    total = len(all_items) - write_errors
    print(f'\n84000 done. {total:,} chunks ingested'
          + (f', {write_errors} write errors' if write_errors else '') + '.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest 84000 Tibetan translations')
    parser.add_argument('--force', action='store_true',
                        help='Re-clone and re-embed already-ingested texts')
    parser.add_argument('--skip-clone', action='store_true',
                        help='Skip git clone/pull; use already-cloned repo')
    args = parser.parse_args()
    run(force=args.force, skip_clone=args.skip_clone)
