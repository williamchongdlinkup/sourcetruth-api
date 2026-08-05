"""
CBETA Chinese Buddhist Electronic Text Association ingestion pipeline.

Source: https://github.com/cbeta-org/xml-p5
Format: TEI P5 XML (Chinese Buddhist canonical texts)
Tradition: mahayana · Language: lzh (Literary Chinese)

Default targets (Phase 2 Āgamas):
  T0001 = 長阿含經  (Dīrgha Āgama)
  T0026 = 中阿含經  (Madhyama Āgama)
  T0099 = 雜阿含經  (Saṃyukta Āgama)
  T0125 = 增一阿含經 (Ekottarika Āgama)

Usage:
  python ingestion/cbeta.py                  # clone T01–T09 + ingest all texts
  python ingestion/cbeta.py --skip-clone     # use cached XML files
  python ingestion/cbeta.py --force          # re-embed already-ingested texts
  python ingestion/cbeta.py --texts T0001    # specific T-numbers only
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

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one
from embed import embed_documents
from normalisation.entity_resolver import EntityResolver

try:
    from lxml import etree
except ImportError:
    etree = None  # type: ignore  # fallback to xml.etree.ElementTree below


# ── Constants ─────────────────────────────────────────────────────────────────

CBETA_REPO   = 'https://github.com/cbeta-org/xml-p5'
DATA_DIR     = Path(os.getenv('DATA_DIR', 'data'))
LOCAL_DIR    = DATA_DIR / 'raw' / 'cbeta'
TEI_NS       = 'http://www.tei-c.org/ns/1.0'
CBETA_NS     = 'http://www.cbeta.org/ns/1.0'

# Taisho volumes: T01–T09 covers Āgamas → Prajnaparamita → Lotus → Avatamsaka → Ratnakuta
SPARSE_DIRS = [f'T/T{i:02d}' for i in range(1, 10)]  # T01 … T09

# Known Āgama metadata (used for precise titles when XML title extraction is needed)
AGAMA_META: dict[str, tuple[str, str, str]] = {
    'T0001': ('長阿含經',  'Dīrgha Āgama',      'agama'),
    'T0026': ('中阿含經',  'Madhyama Āgama',    'agama'),
    'T0099': ('雜阿含經',  'Saṃyukta Āgama',    'agama'),
    'T0125': ('增一阿含經', 'Ekottarika Āgama', 'agama'),
}


def _taisho_collection(tnumber: str) -> str:
    """Map Taisho T-number to a collection label."""
    try:
        n = int(tnumber.lstrip('T'))
    except ValueError:
        return 'sutra'
    if n <= 151:  return 'agama'
    if n <= 219:  return 'jataka-avadana'
    if n <= 261:  return 'prajnaparamita'
    if n <= 277:  return 'lotus'
    if n <= 309:  return 'avatamsaka'
    if n <= 373:  return 'ratnakuta'
    if n <= 423:  return 'nirvana-sutra'
    if n <= 847:  return 'tantra'
    if n <= 1163: return 'vinaya'
    if n <= 1692: return 'abhidharma'
    return 'commentary'

# Chinese characters: ~2 per token; target 400 tokens = 800 chars
CHUNK_TARGET_CHARS = 800
CHUNK_MAX_CHARS    = 1200

TEXT_BATCH   = 10    # text groups per Voyage call
MAX_RETRIES  = 5
RETRY_DELAY  = 30    # seconds between Voyage retries

# Chinese sentence-ending punctuation
_SENT_END_RE = re.compile(r'[。！？；]')
# Strip whitespace runs
_WS_RE = re.compile(r'\s+')
# CBETA page-number annotation like 「0001a01」or @n attribute text
_PAGE_REF_RE = re.compile(r'[「」]?\d{4}[a-c]\d{2}[「」]?')


# ── Git helper ────────────────────────────────────────────────────────────────

_GIT_WIN_PATHS = [
    r'C:\Program Files\Git\mingw64\bin\git.exe',
    r'C:\Program Files\Git\cmd\git.exe',
    r'C:\Program Files (x86)\Git\mingw64\bin\git.exe',
]


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
    """Sparse-checkout cbeta-org/xml-p5 — Taisho volumes T01–T09."""
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    git = _git()

    if (LOCAL_DIR / '.git').exists():
        print(f'Expanding sparse-checkout to {len(SPARSE_DIRS)} volumes and pulling...')
        subprocess.run(
            [git, '-C', str(LOCAL_DIR), 'sparse-checkout', 'set'] + SPARSE_DIRS,
            check=True,
        )
        subprocess.run([git, '-C', str(LOCAL_DIR), 'pull', '--depth=1'], check=True)
        return

    print('Cloning cbeta-org/xml-p5 (sparse depth=1 — may take a few minutes)...')
    subprocess.run([
        git, 'clone', '--depth=1',
        '--filter=blob:none',
        '--sparse',
        CBETA_REPO, str(LOCAL_DIR),
    ], check=True)

    subprocess.run(
        [git, '-C', str(LOCAL_DIR), 'sparse-checkout', 'set'] + SPARSE_DIRS,
        check=True,
    )
    subprocess.run([git, '-C', str(LOCAL_DIR), 'checkout'], check=True)
    print('Clone complete.')


# ── Phase B: File discovery ───────────────────────────────────────────────────

def _tnumber_from_stem(stem: str) -> str | None:
    """
    Extract Taisho text number from CBETA filename stem.

    Examples:
      T01n0001   → T0001
      T02n0026   → T0026
      T02n0099a  → T0099
    """
    m = re.search(r'n(\d{4})', stem)
    return f'T{m.group(1)}' if m else None


def find_target_files(
    target_tnumbers: set[str] | None = None,
    all_texts: bool = False,
) -> list[tuple[Path, str]]:
    """
    Return [(xml_path, tnumber), ...].
    all_texts=True  → every XML file in the downloaded volumes (no T-number filter).
    target_tnumbers → filter to specific T-numbers.
    Neither set    → defaults to AGAMA_META keys only.
    """
    results: list[tuple[Path, str]] = []
    for sparse_dir in SPARSE_DIRS:
        d = LOCAL_DIR / sparse_dir
        if not d.exists():
            continue
        for f in sorted(d.glob('*.xml')):
            tn = _tnumber_from_stem(f.stem) or f.stem
            if all_texts or (target_tnumbers and tn in target_tnumbers) \
                    or (not all_texts and not target_tnumbers and tn in AGAMA_META):
                results.append((f, tn))
    return results


# ── Phase C: TEI P5 parsing ───────────────────────────────────────────────────

def _localname(tag) -> str:
    """Return local part of a potentially namespaced lxml tag."""
    if not isinstance(tag, str):
        return ''
    return etree.QName(tag).localname if etree else tag.split('}', 1)[-1] if '}' in tag else tag


def _clean_text(elem) -> str:
    """
    Extract text content from a TEI element, skipping editorial markup.

    Strips: <note>, <rdg> (variant readings), <del>, <sic>, <unclear>
    Keeps:  <lem> (preferred reading), <g> gaiji text, regular text nodes
    Replaces: <lb> with space, <pb> with nothing
    """
    skip_locals = {'note', 'rdg', 'del', 'sic', 'unclear', 'back'}
    parts: list[str] = []

    def collect(e):
        local = _localname(e.tag)
        if local in skip_locals:
            return
        if local == 'pb':
            return
        if local == 'lb':
            parts.append(' ')
            if e.tail:
                parts.append(e.tail)
            return
        if local == 'app':
            # <app> contains <lem> (best reading) + <rdg> (variants); keep <lem>
            for child in e:
                if _localname(child.tag) == 'lem':
                    collect(child)
            if e.tail:
                parts.append(e.tail)
            return
        if local == 'g':
            # Gaiji: text content is the best rendering we have
            t = ''.join(e.itertext()).strip()
            parts.append(t if t else '□')
            if e.tail:
                parts.append(e.tail)
            return

        if e.text:
            parts.append(e.text)
        for child in e:
            collect(child)
        if e.tail:
            parts.append(e.tail)

    if etree is not None:
        collect(elem)
    else:
        parts.append(''.join(elem.itertext()))

    text = ''.join(parts)
    text = _PAGE_REF_RE.sub('', text)       # strip inline page refs
    text = _WS_RE.sub(' ', text).strip()
    return text


def parse_cbeta_xml(path: Path) -> dict | None:
    """
    Parse a CBETA TEI P5 XML file.

    Returns:
      {
        external_id: str,          # filename stem  e.g. "T01n0001"
        tnumber: str,              # e.g. "T0001"
        title_original: str,       # Chinese title
        title_english: str,        # English title (from AGAMA_META or header)
        collection: str,           # "agama" or from AGAMA_META
        paragraphs: [{text, chapter}]
      }
    """
    try:
        if etree is not None:
            root = etree.fromstring(path.read_bytes())
        else:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(path.read_bytes().decode('utf-8', errors='replace'))
    except Exception as e:
        print(f'  [WARN] {path.name}: XML parse error — {e}')
        return None

    stem    = path.stem
    tnumber = _tnumber_from_stem(stem) or stem

    # Title — prefer <title level="m"> (monograph) over series title
    title_zh = ''
    for ns_prefix in [f'{{{TEI_NS}}}', '']:
        ts = root.findall(f'.//{ns_prefix}titleStmt/{ns_prefix}title')
        # 1st choice: level="m" (individual text title)
        for t in ts:
            if t.get('level') == 'm':
                title_zh = ''.join(t.itertext()).strip()
                break
        if title_zh:
            break
        # 2nd choice: any title with a Chinese lang attribute
        for t in ts:
            lang = t.get(f'{{{TEI_NS}}}lang') or t.get('xml:lang', '')
            if lang.startswith('zh') and t.get('level') != 's':
                title_zh = ''.join(t.itertext()).strip()
                break
        if title_zh:
            break
    if not title_zh:
        title_zh = AGAMA_META.get(tnumber, ('',))[0] or stem

    meta        = AGAMA_META.get(tnumber)
    title_en    = meta[1] if meta else ''
    collection  = meta[2] if meta else _taisho_collection(tnumber)

    # Body
    body = None
    for ns_prefix in [f'{{{TEI_NS}}}', '']:
        body = root.find(f'.//{ns_prefix}body')
        if body is not None:
            break
    if body is None:
        return None

    paragraphs: list[dict] = []
    current_chapter = ''
    current_lb_ref  = ''

    def walk(elem):
        nonlocal current_chapter, current_lb_ref
        local = _localname(elem.tag)

        if local in ('note', 'back'):
            return

        # Taisho line-break milestones — track for deep links
        if local == 'lb':
            if elem.get('ed') == 'T' and elem.get('n'):
                current_lb_ref = elem.get('n')
            return

        # Chapter / fascicle headings
        if local in ('head', 'juan', 'mulu'):
            text = ''.join(elem.itertext()).strip()
            if text:
                current_chapter = text
            return

        # Paragraph elements → extract text
        if local == 'p':
            text = _clean_text(elem)
            if len(text) >= 15:
                paragraphs.append({'text': text, 'chapter': current_chapter, 'lb_ref': current_lb_ref, 'is_verse': False})
            # Advance past lb elements inside this p so the next paragraph gets the right ref
            for child in elem.iter():
                if _localname(child.tag) == 'lb' and child.get('ed') == 'T' and child.get('n'):
                    current_lb_ref = child.get('n')
            return

        # Verse lines → join with newline then treat as one paragraph
        if local == 'lg':
            lines     = []
            lg_lb_ref = current_lb_ref
            for child in elem:
                clocal = _localname(child.tag)
                if clocal == 'lb' and child.get('ed') == 'T' and child.get('n'):
                    current_lb_ref = child.get('n')
                elif clocal == 'l':
                    t = _clean_text(child)
                    if t:
                        lines.append(t)
                    for sub in child.iter():
                        if _localname(sub.tag) == 'lb' and sub.get('ed') == 'T' and sub.get('n'):
                            current_lb_ref = sub.get('n')
            if lines:
                paragraphs.append({'text': '\n'.join(lines), 'chapter': current_chapter, 'lb_ref': lg_lb_ref, 'is_verse': True})
            return

        # Recurse into divs, divN, and other containers
        if local not in ('note', 'rdg', 'del', 'sic', 'unclear', 'back', 'p', 'lg', 'head', 'juan', 'mulu'):
            for child in elem:
                walk(child)

    walk(body)

    # If no <p> found (older format — text as text nodes between milestones):
    # fall back to extracting all body text then splitting at sentence boundaries.
    if not paragraphs:
        paragraphs = _extract_text_node_paragraphs(body)

    if not paragraphs:
        return None

    return {
        'external_id':   stem,
        'tnumber':       tnumber,
        'title_original': title_zh,
        'title_english': title_en,
        'collection':    collection,
        'paragraphs':    paragraphs,
    }


def _extract_text_node_paragraphs(body) -> list[dict]:
    """
    Fallback: extract paragraphs from text-node–based CBETA format
    (older files without explicit <p> elements).

    Collects all text, splits at sentence boundaries (~200 chars / sentence),
    and groups into paragraph-sized units.
    """
    text = _clean_text(body)
    if not text or len(text) < 50:
        return []

    # Split at Chinese sentence endings
    sentences = _SENT_END_RE.split(text)
    paras: list[dict] = []
    current = ''
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        current += sent + '。'
        if len(current) >= 200:
            paras.append({'text': current.strip(), 'chapter': ''})
            current = ''
    if current.strip():
        paras.append({'text': current.strip(), 'chapter': ''})
    return paras


# ── Phase D: Chunking ─────────────────────────────────────────────────────────

def chunk_paragraphs(paragraphs: list[dict], external_id: str) -> list[dict]:
    """
    Group paragraphs into ~800-character chunks at paragraph boundaries.

    Chinese text: character count ≈ 2 × token count, so 800 chars ≈ 400 tokens.
    """
    chunks: list[dict] = []
    current: list[dict] = []
    current_chars = 0
    seq = 0

    def flush():
        nonlocal current, current_chars, seq
        if not current:
            return
        text    = '\n\n'.join(p['text'] for p in current)
        chapter = next((p['chapter'] for p in current if p.get('chapter')), '')
        seq    += 1
        lb_ref = next((p.get('lb_ref', '') for p in current if p.get('lb_ref')), '')
        verse = any(p.get('is_verse', False) for p in current)
        chunks.append({
            'text':      text,
            'reference': f'{external_id} §{seq}',
            'chapter':   chapter,
            'section':   lb_ref,
            'is_verse':  verse,
        })
        current        = []
        current_chars  = 0

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
    eid = parsed['external_id']
    existing = execute_one(
        conn,
        'SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s',
        (corpus_id, eid),
    )
    if existing:
        return existing['id']

    tn         = parsed['tnumber']
    num        = tn.lstrip('T').lstrip('0') or tn
    collection = parsed['collection'] or _taisho_collection(tn)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO canon_texts
                (corpus_id, external_id, title_original, title_english,
                 tradition, language, collection, number, url, word_count)
            VALUES (%s, %s, %s, %s, 'mahayana', 'lzh', %s, %s, %s, %s)
            RETURNING id
        """, (
            corpus_id,
            eid,
            parsed['title_original'],
            parsed['title_english'],
            collection,
            num,
            f'https://cbetaonline.dila.edu.tw/zh/{tn}',
            sum(len(p['text']) // 2 for p in parsed['paragraphs']),
        ))
        return cur.fetchone()['id']


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(
    force: bool = False,
    skip_clone: bool = False,
    db_url: str | None = None,
    target_texts: list[str] | None = None,
    all_texts: bool = False,
) -> None:
    """Full CBETA Taisho ingestion pipeline."""

    # ── Phase A: Clone / expand sparse-checkout ──────────────────────────────
    if not skip_clone:
        clone_or_update_repo()
    else:
        print(f'CBETA: skipping clone, using {LOCAL_DIR}')

    # ── Find target XML files ────────────────────────────────────────────────
    targets = set(target_texts) if target_texts else None
    file_pairs = find_target_files(targets, all_texts=all_texts)
    if not file_pairs:
        print(f'[ERROR] No matching XML files found under {LOCAL_DIR}.')
        print('  Expected directories: ' + ', '.join(str(LOCAL_DIR / d) for d in SPARSE_DIRS))
        return

    print(f'Found {len(file_pairs)} CBETA XML files matching targets '
          f'({", ".join(sorted(set(tn for _, tn in file_pairs)))})')

    # ── Connect and prefetch state ───────────────────────────────────────────
    conn    = get_conn(url=db_url)
    corpus  = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'cbeta'")
    if not corpus:
        raise RuntimeError("Corpus 'cbeta' missing — re-run sql/02_schema.sql.")
    corpus_id = corpus['id']

    # Entity resolver for tagging
    resolver  = EntityResolver(conn)
    _variants = resolver.get_all_variants()
    _name_to_eids: dict[str, list[int]] = {}
    for v in _variants:
        _name_to_eids.setdefault(v['name_text'].lower(), []).append(v['entity_id'])

    def _entity_ids_fast(text: str) -> list[int]:
        t = text.lower()
        return list({eid for name, eids in _name_to_eids.items() if name in t for eid in eids})

    existing_rows = execute(conn,
        'SELECT external_id, id FROM canon_texts WHERE corpus_id = %s', (corpus_id,))
    eid_to_tid: dict[str, int] = {r['external_id']: r['id'] for r in existing_rows}

    done_tids: set[int] = set()
    if eid_to_tid and not force:
        done_rows = execute(conn,
            'SELECT DISTINCT text_id FROM document_chunks WHERE text_id = ANY(%s)',
            (list(eid_to_tid.values()),))
        done_tids = {r['text_id'] for r in done_rows}

    # ── Phase C: Parse ───────────────────────────────────────────────────────
    print(f'\nCBETA Phase C — parsing {len(file_pairs)} XML files '
          f'({len(done_tids)} already ingested)...')

    all_items:    list[dict] = []
    skipped = parse_errors   = 0

    for path, tn in tqdm(file_pairs, desc='parse', unit='file'):
        parsed = parse_cbeta_xml(path)
        if not parsed:
            parse_errors += 1
            continue

        eid = parsed['external_id']
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
                'word_count':  len(chunk['text']) // 2,  # Chinese: chars/2 ≈ words
                'token_count': len(chunk['text']) // 2,  # CJK: ~1 char/token
                'entity_ids':  _entity_ids_fast(chunk['text']),
                'language':    'lzh',
                'tradition':   'mahayana',
                'corpus_code': 'cbeta',
                'collection':  parsed['collection'],
                'is_verse':    chunk.get('is_verse', False),
            })

    conn.commit()

    if skipped:
        print(f'  Skipped {skipped} already-ingested files (use --force to re-embed)')
    if parse_errors:
        print(f'  {parse_errors} files produced no usable text')
    if not all_items:
        print('  Nothing new to embed.')
        conn.close()
        return

    # ── Phase D+E: Embed and write ───────────────────────────────────────────
    all_items.sort(key=lambda x: x['text_id'])
    text_groups = [
        (tid, list(items))
        for tid, items in groupby(all_items, key=lambda x: x['text_id'])
    ]

    total_texts  = len(text_groups)
    total_chunks = len(all_items)
    print(f'\nCBETA Phase D+E — embedding and writing {total_chunks:,} chunks '
          f'across {total_texts} texts (batches of {TEXT_BATCH})...')

    write_errors = written_chunks = 0

    for batch_idx in range(0, total_texts, TEXT_BATCH):
        batch_groups  = text_groups[batch_idx:batch_idx + TEXT_BATCH]
        batch_items   = [item for _, items in batch_groups for item in items]
        batch_num     = batch_idx // TEXT_BATCH + 1
        total_batches = (total_texts + TEXT_BATCH - 1) // TEXT_BATCH

        print(f'  [{batch_num}/{total_batches}] {len(batch_items)} chunks '
              f'({len(batch_groups)} texts) — embedding...')

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
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                            'INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (%s, %s)',
                            (chunk_id, emb),
                        )
                conn.commit()
                written_chunks += n
            except Exception as e:
                print(f'  [WARN] write error for text_id {_tid}: {e}')
                try:
                    conn.rollback()
                except Exception:
                    conn = get_conn(url=db_url)
                write_errors += n

        print(f'  [{batch_num}/{total_batches}] committed — '
              f'{written_chunks:,}/{total_chunks:,} chunks total')

    conn.close()
    total = len(all_items) - write_errors
    print(f'\nCBETA done. {total:,} chunks ingested'
          + (f', {write_errors} write errors' if write_errors else '') + '.')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Ingest CBETA Chinese Āgama corpus')
    parser.add_argument('--skip-clone', action='store_true',
                        help='Skip clone phase; use already-cached XML files')
    parser.add_argument('--force', action='store_true',
                        help='Re-embed and overwrite already-ingested texts')
    parser.add_argument('--texts', nargs='+', default=None,
                        help='Specific T-numbers to ingest (e.g. T0001 T0026); omit for all')
    parser.add_argument('--db-url', default=None,
                        help='Database URL (overrides DATABASE_URL_3 env var)')
    args = parser.parse_args()

    db = args.db_url or os.getenv('DATABASE_URL_3')
    if not db:
        print('[ERROR] Set DATABASE_URL_3 in .env or pass --db-url')
        sys.exit(1)

    run(force=args.force, skip_clone=args.skip_clone,
        db_url=db, target_texts=args.texts,
        all_texts=(not args.texts))
