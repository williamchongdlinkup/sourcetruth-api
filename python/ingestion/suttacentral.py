"""
SuttaCentral bilara-data ingestion pipeline.

Downloads and ingests Pali root texts + English translations from:
https://github.com/suttacentral/bilara-data

Bilara-data structure:
  root/pli/ms/<nikaya>/<filename>_root-pli-ms.json
  translation/en/sujato/<nikaya>/<filename>_translation-en-sujato.json
  translation/en/bodhi/<nikaya>/<filename>_translation-en-bodhi.json

Each JSON file:
  { "mn1:0.1": "Majjhimanikāya 1", "mn1:0.2": "Mūlapariyāyasutta", ... }

Chunking strategy:
  - Group segments into ~400-token chunks at natural section/paragraph boundaries
  - Always keep sutta preamble (0.x segments) with the first chunk
  - Store bilara segment range as reference: "mn1:1.1–mn1:4.5"
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one, execute_many
from embed import embed_documents
from normalisation.entity_resolver import EntityResolver

DATA_DIR    = Path(os.getenv('DATA_DIR', 'data'))
SC_DIR      = Path(os.getenv('SC_DATA_DIR', 'data/raw/suttacentral'))
BILARA_REPO = 'https://github.com/suttacentral/bilara-data.git'

CHUNK_TARGET_TOKENS = 400   # approximate; 1 token ≈ 4 chars
CHUNK_MAX_TOKENS    = 600

# Which nikayas to ingest in Phase 1 (extend for full Tipitaka)
PHASE1_NIKAYAS = ['mn', 'dn', 'sn', 'an']


_GIT_WIN_PATHS = [
    r'C:\Program Files\Git\mingw64\bin\git.exe',
    r'C:\Program Files\Git\cmd\git.exe',
]


def _git() -> str:
    found = shutil.which('git')
    if found:
        return found
    for p in _GIT_WIN_PATHS:
        if os.path.exists(p):
            return p
    return 'git'


def clone_or_update_bilara():
    """Clone bilara-data sparsely (root + translation dirs only, no full history)."""
    SC_DIR.mkdir(parents=True, exist_ok=True)
    git = _git()

    if (SC_DIR / '.git').exists():
        print('Updating bilara-data...')
        subprocess.run([git, '-C', str(SC_DIR), 'pull', '--depth=1'], check=True)
    else:
        print('Cloning bilara-data (sparse, depth=1 — may take a few minutes)...')
        subprocess.run([
            git, 'clone',
            '--depth=1',
            '--filter=blob:none',
            '--sparse',
            BILARA_REPO,
            str(SC_DIR)
        ], check=True)
        # Sparse checkout: Pali root + lzh Chinese Āgamas + English translations
        subprocess.run([
            git, '-C', str(SC_DIR), 'sparse-checkout', 'set',
            'root/pli/ms',
            'root/lzh/sct',
            'translation/en/sujato',
            'translation/en/bodhi',
            'translation/en/patton',
        ], check=True)
        subprocess.run([git, '-C', str(SC_DIR), 'checkout'], check=True)


def iter_sutta_files(nikaya: str) -> Iterator[tuple[Path, Path | None]]:
    """Yield (pali_file, en_file | None) for each sutta in a nikaya.

    bilara-data structure (as of 2025):
      root/pli/ms/sutta/<nikaya>/<uid>_root-pli-ms.json
      translation/en/sujato/sutta/<nikaya>/<uid>_translation-en-sujato.json
    """
    # Suttas live under a 'sutta/' subdirectory in the Pali root
    pali_base_dirs = [
        SC_DIR / 'root' / 'pli' / 'ms' / 'sutta' / nikaya,
        SC_DIR / 'root' / 'pli' / 'ms' / nikaya,              # fallback (old layout)
    ]
    pali_dir = next((d for d in pali_base_dirs if d.exists()), None)
    if pali_dir is None:
        return

    for pali_file in sorted(pali_dir.rglob('*_root-pli-ms.json')):
        uid = pali_file.name.replace('_root-pli-ms.json', '')
        en_file = None
        for translator in ['sujato', 'bodhi']:
            # Try sutta/ subdir first, then direct nikaya dir
            candidates = [
                SC_DIR / 'translation' / 'en' / translator / 'sutta' / nikaya / f'{uid}_translation-en-{translator}.json',
                SC_DIR / 'translation' / 'en' / translator / nikaya / f'{uid}_translation-en-{translator}.json',
                SC_DIR / 'translation' / 'en' / translator / 'sutta' / nikaya / pali_file.parent.name / f'{uid}_translation-en-{translator}.json',
            ]
            for candidate in candidates:
                if candidate.exists():
                    en_file = candidate
                    break
            if en_file:
                break
        yield pali_file, en_file


def load_bilara_json(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding='utf-8'))


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_segments(
    segments: dict[str, str],
    uid: str,
) -> list[dict]:
    """
    Group bilara segments into passages of ~CHUNK_TARGET_TOKENS.

    Returns list of:
      { 'text': str, 'reference': str, 'chapter': str, 'section': str }
    """
    chunks: list[dict] = []
    current_texts: list[str] = []
    current_keys: list[str] = []
    current_tokens = 0

    preamble_texts: list[str] = []
    preamble_keys:  list[str] = []

    def flush(is_preamble=False):
        nonlocal current_texts, current_keys, current_tokens
        if not current_texts:
            return
        text = ' '.join(current_texts).strip()
        if not text:
            current_texts = []; current_keys = []; current_tokens = 0
            return
        ref_start = current_keys[0]
        ref_end   = current_keys[-1]
        reference = ref_start if ref_start == ref_end else f'{ref_start}–{ref_end}'

        # Infer chapter/section from key pattern (e.g. mn1:1.2 → chapter "1")
        m = re.match(r'[a-z]+\d+[a-z]*:(\d+)\.', ref_start)
        chapter = m.group(1) if m else ''

        if is_preamble:
            preamble_texts.extend(current_texts)
            preamble_keys.extend(current_keys)
        else:
            chunks.append({
                'text': text,
                'reference': reference,
                'chapter': chapter,
                'section': '',
            })
        current_texts = []; current_keys = []; current_tokens = 0

    for key, text in segments.items():
        text = text.strip()
        if not text:
            continue

        # Preamble: segment keys like "mn1:0.1"
        if re.match(r'[a-z]+\d+[a-z]*:0\.', key):
            preamble_texts.append(text)
            preamble_keys.append(key)
            continue

        tok = _approx_tokens(text)
        if current_tokens + tok > CHUNK_MAX_TOKENS and current_texts:
            flush()

        current_texts.append(text)
        current_keys.append(key)
        current_tokens += tok

        if current_tokens >= CHUNK_TARGET_TOKENS:
            flush()

    flush()

    # Prepend preamble to the first substantive chunk
    if preamble_texts and chunks:
        preamble_text = ' '.join(preamble_texts).strip()
        chunks[0]['text'] = preamble_text + '\n\n' + chunks[0]['text']
        chunks[0]['reference'] = f"{preamble_keys[0]}–{chunks[0]['reference'].split('–')[-1]}"

    return chunks


def parse_sutta_uid(uid: str) -> dict:
    """Extract nikaya and number from a sutta UID like 'mn1', 'sn12.23', or 'sn12.72-81'."""
    # Strip trailing range suffix (e.g. '72-81' → '72') before matching
    normalized = re.sub(r'-\d+$', '', uid)
    m = re.match(r'^([a-z]+)(\d+(?:\.\d+)?)([a-z]*)$', normalized)
    if m:
        return {
            'collection': m.group(1),
            'number': uid[len(m.group(1)):],  # preserve original number including range
        }
    return {'collection': uid, 'number': ''}


def get_or_create_text(conn, corpus_id: int, uid: str, pali_segs: dict, en_segs: dict | None, translator: str) -> int:
    """Insert canon_texts record if not present, return text id."""
    # Title: first two preamble segments usually hold collection + title
    preamble = {k: v for k, v in pali_segs.items() if re.match(r'.*:0\.', k)}
    title_pali = ' · '.join(list(preamble.values())[:2]) if preamble else uid

    en_preamble = {}
    if en_segs:
        en_preamble = {k: v for k, v in en_segs.items() if re.match(r'.*:0\.', k)}
    title_en = ' · '.join(list(en_preamble.values())[:2]) if en_preamble else ''

    info = parse_sutta_uid(uid)

    existing = execute_one(conn, """
        SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s
    """, (corpus_id, uid))
    if existing:
        return existing['id']

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO canon_texts
                (corpus_id, external_id, title_pali, title_english, tradition,
                 language, collection, number, translator,
                 url, word_count)
            VALUES (%s,%s,%s,%s,'theravada',%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            corpus_id, uid, title_pali, title_en or None,
            'pli' if en_segs is None else 'en',
            info['collection'], info['number'],
            translator or None,
            f'https://suttacentral.net/{uid}/en/{translator}' if translator else f'https://suttacentral.net/{uid}',
            sum(len(v.split()) for v in pali_segs.values()),
        ))
        return cur.fetchone()['id']


def ingest_sutta(conn, corpus_id: int, pali_file: Path, en_file: Path | None, resolver: EntityResolver, force: bool = False):
    uid = pali_file.name.replace('_root-pli-ms.json', '')
    pali_segs = load_bilara_json(pali_file)
    en_segs = load_bilara_json(en_file) if en_file else None

    translator = ''
    if en_file:
        m = re.search(r'_translation-en-(\w+)\.json$', en_file.name)
        translator = m.group(1) if m else ''

    # Prefer English chunks for retrieval (more useful for developers)
    target_segs = en_segs if en_segs else pali_segs
    chunks = chunk_segments(target_segs, uid)

    if not chunks:
        return 0

    text_id = get_or_create_text(conn, corpus_id, uid, pali_segs, en_segs, translator)

    existing = execute_one(conn, "SELECT COUNT(*) AS n FROM document_chunks WHERE text_id = %s", (text_id,))
    if existing['n'] > 0:
        if not force:
            return existing['n']
        # force=True: wipe and re-embed. CASCADE handles chunk_embeddings.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (text_id,))

    texts_for_embedding = [c['text'] for c in chunks]
    embeddings = embed_documents(texts_for_embedding)

    chunk_rows = []
    embedding_rows = []

    lang = 'en' if en_segs else 'pli'
    info = parse_sutta_uid(uid)

    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        entity_ids = resolver.get_entity_ids_for_text(uid, chunk['text'])
        word_count = len(chunk['text'].split())

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO document_chunks
                    (text_id, chunk_index, chunk_text, reference, chapter, section,
                     word_count, token_count, entity_ids,
                     language, tradition, corpus_code, collection, is_verse)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                text_id, i, chunk['text'], chunk['reference'],
                chunk.get('chapter', ''), chunk.get('section', ''),
                word_count, len(chunk['text']) // 4, entity_ids or [],
                lang, 'theravada', 'suttacentral', info['collection'], False,
            ))
            chunk_id = cur.fetchone()['id']

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                (chunk_id, emb)
            )

    conn.commit()
    return len(chunks)


def _parse_sutta_chunks(
    conn, corpus_id: int, pali_file: Path, en_file: Path | None, resolver: EntityResolver
) -> list[dict]:
    """Parse one sutta and return chunk dicts ready for bulk embedding (no Voyage call)."""
    uid = pali_file.name.replace('_root-pli-ms.json', '')
    pali_segs = load_bilara_json(pali_file)
    en_segs = load_bilara_json(en_file) if en_file else None

    translator = ''
    if en_file:
        m = re.search(r'_translation-en-(\w+)\.json$', en_file.name)
        translator = m.group(1) if m else ''

    target_segs = en_segs if en_segs else pali_segs
    chunks = chunk_segments(target_segs, uid)
    if not chunks:
        return []

    text_id = get_or_create_text(conn, corpus_id, uid, pali_segs, en_segs, translator)

    lang = 'en' if en_segs else 'pli'
    info = parse_sutta_uid(uid)

    items = []
    for i, chunk in enumerate(chunks):
        entity_ids = resolver.get_entity_ids_for_text(uid, chunk['text'])
        items.append({
            'text_id':     text_id,
            'chunk_index': i,
            'text':        chunk['text'],
            'reference':   chunk['reference'],
            'chapter':     chunk.get('chapter', ''),
            'section':     chunk.get('section', ''),
            'word_count':  len(chunk['text'].split()),
            'token_count': len(chunk['text']) // 4,
            'entity_ids':  entity_ids or [],
            'language':    lang,
            'tradition':   'theravada',
            'corpus_code': 'suttacentral',
            'collection':  info['collection'],
            'is_verse':    False,
        })
    return items


def ingest_nikaya_batched(conn, corpus_id: int, nikaya: str, resolver: EntityResolver, force: bool = False) -> int:
    """Parse all suttas in a nikaya, embed all chunks in one Voyage pass, then write to DB.

    Reduces Voyage API calls from O(suttas) to O(total_chunks / 128).
    Skips suttas that already have chunks unless force=True.
    """
    files = list(iter_sutta_files(nikaya))
    print(f'\n{nikaya.upper()}: {len(files)} suttas — parsing...')

    all_items: list[dict] = []
    for pali_file, en_file in tqdm(files, desc=f'{nikaya.upper()} parse', unit='sutta'):
        try:
            items = _parse_sutta_chunks(conn, corpus_id, pali_file, en_file, resolver)
            all_items.extend(items)
        except Exception as exc:
            print(f'  [WARN] {pali_file.name}: {exc}')
            conn.rollback()

    conn.commit()  # commit all canon_texts inserts before the embedding call

    if not all_items:
        return 0

    # Skip text_ids that already have chunks, unless force re-embed was requested.
    text_ids = list({item['text_id'] for item in all_items})
    if not force:
        existing = execute(conn, "SELECT DISTINCT text_id FROM document_chunks WHERE text_id = ANY(%s)", (text_ids,))
        done_ids = {r['text_id'] for r in existing}
        skipped = sum(1 for item in all_items if item['text_id'] in done_ids)
        all_items = [item for item in all_items if item['text_id'] not in done_ids]
        if skipped:
            print(f'{nikaya.upper()}: skipping {skipped} already-ingested chunks, embedding {len(all_items)} new...')
    else:
        # force=True: wipe existing chunks first. CASCADE handles chunk_embeddings.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_chunks WHERE text_id = ANY(%s)", (text_ids,))
        conn.commit()

    if not all_items:
        return skipped if not force else 0

    print(f'{nikaya.upper()}: embedding {len(all_items)} chunks...')
    embeddings = embed_documents([item['text'] for item in all_items])

    print(f'{nikaya.upper()}: writing {len(all_items)} chunks to DB...')
    for item, emb in tqdm(zip(all_items, embeddings), total=len(all_items),
                          desc=f'{nikaya.upper()} write', unit='chunk'):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO document_chunks
                    (text_id, chunk_index, chunk_text, reference, chapter, section,
                     word_count, token_count, entity_ids,
                     language, tradition, corpus_code, collection, is_verse)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                item['text_id'], item['chunk_index'], item['text'], item['reference'],
                item['chapter'], item['section'], item['word_count'], item['token_count'],
                item['entity_ids'], item['language'], item['tradition'],
                item['corpus_code'], item['collection'], item['is_verse'],
            ))
            chunk_id = cur.fetchone()['id']
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                (chunk_id, emb),
            )

    conn.commit()
    return len(all_items)


# ── Classical Chinese (lzh) — SuttaCentral bilara-data ───────────────────────
# Root: root/lzh/sct/sutta/<agama>/**/*_root-lzh-sct.json
# Translations: translation/en/patton/sutta/<agama>/**/*_translation-en-patton.json
# Agamas: sa (雜阿含, T99), ma (中阿含, T26), ea (增一阿含, T125)
# Tradition: early-buddhist (Sarvāstivāda school; predates Theravāda/Mahāyāna split)
# License: CC0 — same as Pali bilara-data

LZH_AGAMA_COLLECTION = {
    'sa': 'agama-sa',   # Saṃyukta Āgama
    'ma': 'agama-ma',   # Madhyama Āgama
    'ea': 'agama-ea',   # Ekottarika Āgama
}

# CJK token approximation: 1 char ≈ 1.5–2 tokens; use chars//2 for conservative estimate.
# Target 400 tokens → ~800 characters per chunk (matches CBETA chunking strategy).
LZH_CHUNK_TARGET_CHARS = 800
LZH_CHUNK_MAX_CHARS    = 1200


def _approx_tokens_lzh(text: str) -> int:
    return max(1, len(text) // 2)


def iter_lzh_files(agama: str) -> Iterator[tuple[Path, Path | None]]:
    """Yield (lzh_root_file, en_patton_file | None) for each text in an Āgama."""
    lzh_dir = SC_DIR / 'root' / 'lzh' / 'sct' / 'sutta' / agama
    if not lzh_dir.exists():
        return
    for lzh_file in sorted(lzh_dir.rglob('*_root-lzh-sct.json')):
        uid = lzh_file.name.replace('_root-lzh-sct.json', '')
        en_file = None
        # Patton English translation (MA and SA have partial coverage)
        patton_base = SC_DIR / 'translation' / 'en' / 'patton' / 'sutta' / agama
        for candidate in patton_base.rglob(f'{uid}_translation-en-patton.json'):
            en_file = candidate
            break
        yield lzh_file, en_file


def chunk_segments_lzh(segments: dict[str, str], uid: str) -> list[dict]:
    """Group lzh bilara segments into ~800-character chunks (≈ 400 tokens for CJK)."""
    chunks: list[dict] = []
    current_texts: list[str] = []
    current_keys:  list[str] = []
    current_chars = 0

    preamble_texts: list[str] = []
    preamble_keys:  list[str] = []

    def flush(is_preamble: bool = False) -> None:
        nonlocal current_texts, current_keys, current_chars
        if not current_texts:
            return
        text = ' '.join(current_texts).strip()
        if not text:
            current_texts = []; current_keys = []; current_chars = 0
            return
        ref = current_keys[0] if current_keys[0] == current_keys[-1] else f'{current_keys[0]}–{current_keys[-1]}'
        m = re.match(r'[a-z]+\d+[a-z]*:(\d+)\.', current_keys[0])
        chapter = m.group(1) if m else ''
        if is_preamble:
            preamble_texts.extend(current_texts)
            preamble_keys.extend(current_keys)
        else:
            chunks.append({'text': text, 'reference': ref, 'chapter': chapter, 'section': ''})
        current_texts = []; current_keys = []; current_chars = 0

    for key, text in segments.items():
        text = text.strip()
        if not text:
            continue
        if re.match(r'[a-z]+\d+[a-z]*:0\.', key):
            preamble_texts.append(text)
            preamble_keys.append(key)
            continue
        char_count = len(text)
        if current_chars + char_count > LZH_CHUNK_MAX_CHARS and current_texts:
            flush()
        current_texts.append(text)
        current_keys.append(key)
        current_chars += char_count
        if current_chars >= LZH_CHUNK_TARGET_CHARS:
            flush()

    flush()

    if preamble_texts and chunks:
        preamble_text = ' '.join(preamble_texts).strip()
        chunks[0]['text'] = preamble_text + '\n\n' + chunks[0]['text']
        chunks[0]['reference'] = f"{preamble_keys[0]}–{chunks[0]['reference'].split('–')[-1]}"

    return chunks


def get_or_create_lzh_text(conn, corpus_id: int, uid: str, lzh_segs: dict,
                            en_segs: dict | None, agama: str) -> int:
    """Insert canon_texts record for an lzh Āgama text."""
    existing = execute_one(conn, "SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s",
                           (corpus_id, uid))
    if existing:
        return existing['id']

    preamble = {k: v for k, v in lzh_segs.items() if re.match(r'.*:0\.', k)}
    title_zh = ' · '.join(list(preamble.values())[:2]) if preamble else uid

    en_preamble = {k: v for k, v in en_segs.items() if re.match(r'.*:0\.', k)} if en_segs else {}
    title_en = ' · '.join(list(en_preamble.values())[:2]) if en_preamble else ''

    info = parse_sutta_uid(uid)
    collection = LZH_AGAMA_COLLECTION.get(agama, f'agama-{agama}')

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO canon_texts
                (corpus_id, external_id, title_original, title_english, tradition,
                 language, collection, number, translator, url, word_count)
            VALUES (%s,%s,%s,%s,'early-buddhist','lzh',%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            corpus_id, uid,
            title_zh or None,
            title_en or None,
            collection,
            info['number'],
            'patton' if en_segs else None,
            f'https://suttacentral.net/{uid}/lzh/sct',
            len(lzh_segs),
        ))
        return cur.fetchone()['id']


def _parse_lzh_chunks(conn, corpus_id: int, agama: str,
                      lzh_file: Path, en_file: Path | None,
                      resolver: EntityResolver) -> list[dict]:
    """Parse one lzh Āgama text and return chunk dicts ready for batch embedding."""
    uid = lzh_file.name.replace('_root-lzh-sct.json', '')
    lzh_segs = load_bilara_json(lzh_file)
    en_segs = load_bilara_json(en_file) if en_file else None

    chunks = chunk_segments_lzh(lzh_segs, uid)
    if not chunks:
        return []

    text_id = get_or_create_lzh_text(conn, corpus_id, uid, lzh_segs, en_segs, agama)
    collection = LZH_AGAMA_COLLECTION.get(agama, f'agama-{agama}')

    items = []
    for i, chunk in enumerate(chunks):
        entity_ids = resolver.get_entity_ids_for_text(uid, chunk['text'])
        items.append({
            'text_id':     text_id,
            'chunk_index': i,
            'text':        chunk['text'],
            'reference':   chunk['reference'],
            'chapter':     chunk.get('chapter', ''),
            'section':     chunk.get('section', ''),
            'word_count':  len(chunk['text']) // 2,   # CJK: chars/2 ≈ words
            'token_count': len(chunk['text']) // 2,
            'entity_ids':  entity_ids or [],
            'language':    'lzh',
            'tradition':   'early-buddhist',
            'corpus_code': 'suttacentral',
            'collection':  collection,
            'is_verse':    False,
        })
    return items


def ingest_lzh_batched(conn, corpus_id: int, agama: str,
                        resolver: EntityResolver, force: bool = False) -> int:
    """Parse all texts in an Āgama, embed in batch, write to DB."""
    files = list(iter_lzh_files(agama))
    if not files:
        print(f'  [WARN] No lzh files found for agama={agama}')
        return 0

    print(f'\n{agama.upper()} (lzh): {len(files)} texts — parsing...')
    all_items: list[dict] = []

    for lzh_file, en_file in tqdm(files, desc=f'{agama.upper()} lzh parse', unit='text'):
        try:
            items = _parse_lzh_chunks(conn, corpus_id, agama, lzh_file, en_file, resolver)
            all_items.extend(items)
        except Exception as exc:
            print(f'  [WARN] {lzh_file.name}: {exc}')
            conn.rollback()

    conn.commit()

    if not all_items:
        return 0

    if not force:
        text_ids = list({item['text_id'] for item in all_items})
        existing = execute(conn, "SELECT DISTINCT text_id FROM document_chunks WHERE text_id = ANY(%s)", (text_ids,))
        done_ids = {r['text_id'] for r in existing}
        skipped = sum(1 for item in all_items if item['text_id'] in done_ids)
        all_items = [item for item in all_items if item['text_id'] not in done_ids]
        if skipped:
            print(f'{agama.upper()} lzh: skipping {skipped} already-ingested chunks')
    else:
        text_ids = list({item['text_id'] for item in all_items})
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_chunks WHERE text_id = ANY(%s)", (text_ids,))
        conn.commit()

    if not all_items:
        return 0

    print(f'{agama.upper()} (lzh): embedding {len(all_items)} chunks...')
    embeddings = embed_documents([item['text'] for item in all_items])

    print(f'{agama.upper()} (lzh): writing {len(all_items)} chunks to DB...')
    for item, emb in tqdm(zip(all_items, embeddings), total=len(all_items),
                          desc=f'{agama.upper()} lzh write', unit='chunk'):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO document_chunks
                    (text_id, chunk_index, chunk_text, reference, chapter, section,
                     word_count, token_count, entity_ids,
                     language, tradition, corpus_code, collection, is_verse)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                item['text_id'], item['chunk_index'], item['text'], item['reference'],
                item['chapter'], item['section'], item['word_count'], item['token_count'],
                item['entity_ids'], item['language'], item['tradition'],
                item['corpus_code'], item['collection'], item['is_verse'],
            ))
            chunk_id = cur.fetchone()['id']
        with conn.cursor() as cur:
            cur.execute("INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s,%s)",
                        (chunk_id, emb))

    conn.commit()
    return len(all_items)


def run_lzh(agamas: list[str] | None = None, skip_clone: bool = False, force: bool = False):
    """Ingest SuttaCentral Classical Chinese Āgamas (CC0) into SourceTruth."""
    if not skip_clone:
        clone_or_update_bilara()

    conn = get_conn()
    corpus = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'suttacentral'")
    corpus_id = corpus['id']
    resolver = EntityResolver(conn)

    target_agamas = agamas or list(LZH_AGAMA_COLLECTION.keys())
    total = 0
    for agama in target_agamas:
        total += ingest_lzh_batched(conn, corpus_id, agama, resolver, force=force)

    conn.close()
    print(f'\nDone. {total:,} lzh chunks ingested.')


def run(nikayas: list[str] | None = None, skip_clone: bool = False, force: bool = False):
    if not skip_clone:
        clone_or_update_bilara()

    conn = get_conn()

    corpus = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'suttacentral'")
    corpus_id = corpus['id']

    resolver = EntityResolver(conn)
    target_nikayas = nikayas or PHASE1_NIKAYAS

    # SN uses per-sutta path (large nikaya, resumable after pauses).
    # All other nikayas use batch embedding to reduce Voyage API calls from O(suttas) to O(chunks/128).
    UNBATCHED = {'sn'}

    total_chunks = 0
    for nikaya in target_nikayas:
        if nikaya in UNBATCHED:
            files = list(iter_sutta_files(nikaya))
            print(f'\n{nikaya.upper()}: {len(files)} suttas')
            for pali_file, en_file in tqdm(files, desc=nikaya.upper(), unit='sutta'):
                try:
                    n = ingest_sutta(conn, corpus_id, pali_file, en_file, resolver, force=force)
                    total_chunks += n
                except Exception as exc:
                    print(f'  [WARN] {pali_file.name}: {exc}')
                    conn.rollback()
        else:
            total_chunks += ingest_nikaya_batched(conn, corpus_id, nikaya, resolver, force=force)

    conn.close()
    print(f'\nDone. {total_chunks:,} chunks ingested.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest SuttaCentral bilara-data')
    parser.add_argument('--nikayas', nargs='+', default=None,
                        help='Which nikayas to ingest, e.g. --nikayas mn dn')
    parser.add_argument('--skip-clone', action='store_true',
                        help='Skip git clone/pull (data already present)')
    parser.add_argument('--force', action='store_true',
                        help='Re-embed and overwrite already-ingested suttas')
    args = parser.parse_args()
    run(nikayas=args.nikayas, skip_clone=args.skip_clone, force=args.force)
