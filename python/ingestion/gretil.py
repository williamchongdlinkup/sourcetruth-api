"""
GRETIL Buddhist Sanskrit ingestion pipeline.

Source: https://gretil.sub.uni-goettingen.de/gretil.html
Section: 1. Sanskrit > 4. Religious Literature > Buddhist

Covers ~200+ texts including:
  Prajnaparamita sutras, Madhyamaka (Nagarjuna, Chandrakirti),
  Yogacara (Vasubandhu, Asanga), pramana (Dignaga, Dharmakirti),
  major Mahayana sutras (Lotus, Lankavatara, Vimalakirti, Sukhavativyuha),
  Abhidharmakosa, Avadana literature, Vinaya.
  DSBC-sourced texts are included within GRETIL's Buddhist section.

Two HTML formats handled:
  corpustei — new TEI-derived HTML (most texts): <hr> → <h2>Text</h2> → <div> of <p>
  legacy    — old format using a <pre> block

Usage:
  python ingestion/gretil.py              # download + ingest all new texts
  python ingestion/gretil.py --skip-dl    # skip download, use cached .htm files
  python ingestion/gretil.py --force      # re-embed already-ingested texts
"""
from __future__ import annotations

import os
import re
import sys
import time
from itertools import groupby
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one
from embed import embed_documents
from normalisation.entity_resolver import EntityResolver


# ── Constants ─────────────────────────────────────────────────────────────────

GRETIL_BASE   = 'https://gretil.sub.uni-goettingen.de'
GRETIL_INDEX  = f'{GRETIL_BASE}/gretil.html'

DATA_DIR  = Path(os.getenv('DATA_DIR', 'data'))
LOCAL_DIR = DATA_DIR / 'raw' / 'gretil'

CHUNK_TARGET_TOKENS = 400
CHUNK_MAX_TOKENS    = 600

DOWNLOAD_DELAY = 0.5   # seconds between HTTP requests (polite crawl)
TEXT_BATCH     = 15    # texts per Voyage call group
MAX_RETRIES    = 5
RETRY_DELAY    = 30    # seconds between Voyage retries

# Legacy-format header lines to strip (INPUT BY: style)
_LEGACY_HEADER_RE = re.compile(
    r'^(input\s+by|recension\s+by|date\s+of\s+input|prepared\s+by|'
    r'proof-?read\s+by|checked\s+by|encoded\s+by|based\s+on|'
    r'edited\s+by|transliterated\s+by|data\s+entry|'
    r'copyright|source|notes?)\s*[=:\-—]',
    re.IGNORECASE,
)
# Page-reference markers like (Vaidya 2) or (p. 23)
_PAGE_REF_RE  = re.compile(r'\([A-Za-z]\w*\.?\s*\d+[a-z]?\)')
# Inline footnote references like (1)
_FOOTNOTE_RE  = re.compile(r'\(\d+\)')
# Variant readings in braces
_VARIANTS_RE  = re.compile(r'\{[^}]{0,200}\}')
# Cleanup excess whitespace
_EXCESS_NL_RE = re.compile(r'\n{3,}')

# Keyword → collection sub-classification from filename/title
_COLLECTION_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'prajn[aā]p[aā]ramit[aā]|sa?[hk]asrik[aā]|vajracc|hrdaya|h[rṛ]day', re.I),
     'prajnaparamita'),
    (re.compile(r'madhyamak|m[uū]lamadhyam|prasannapad|vigrahav|yukti[sṣ]a[sṣ][tṭ]ik', re.I),
     'madhyamaka'),
    (re.compile(r'yogac[aā]r|vijñaptim[aā]tr|trim[sś]ik|vim[sś]atik|abhidharmako[sś]|'
                r'zr[aā]vakabh[uū]|sravakabh[uū]|mahAyAnasUtr[aā]lam', re.I),
     'yogacara-abhidharma'),
    (re.compile(r'pram[aā][nṇ]a|nyayabindu|nyAybindu|dharmak[iī]rti|dign[aā]g|hetubindu', re.I),
     'pramana'),
    (re.compile(r'saddharmapun[dḍ]ar[iī]|avata[mṃ]sak|ga[nṇ][dḍ]avy[uū]h|'
                r'lalitavistar|vimalakirti|sukh[aā]vat|da[sś]abh[uū]mi|'
                r'la[nṃ]k[aā]vat[aā]r|sam[aā]dhir[aā]j|ratnako[sś]', re.I),
     'mahayana-sutra'),
    (re.compile(r'avad[aā]na|divyavad|a[sś]ok[aā]vad|j[aā]takam[aā]l', re.I),
     'avadana'),
    (re.compile(r'vinaya|pr[aā]timok[sś]|bhik[sś]u|karmav[aā]can', re.I),
     'vinaya'),
    (re.compile(r'stotra|stava|dharma[nṇ][iī]|dhvaj[aā]gr', re.I),
     'stotra-dharani'),
    (re.compile(r'buddhacarita|lalitavistar|jNAnezvar', re.I),
     'kavya-biography'),
]


def _detect_collection(title: str, stem: str) -> str:
    combined = f'{title} {stem}'
    for pat, label in _COLLECTION_KEYWORDS:
        if pat.search(combined):
            return label
    return 'buddhist-sanskrit'


# ── Phase A: Fetch text listing ───────────────────────────────────────────────

def fetch_buddhist_urls() -> list[tuple[str, str]]:
    """
    Scrape GRETIL index and return [(url, stem), ...] for all Buddhist Sanskrit texts.

    Walks siblings of the <h4 id='RLBuddh'> heading until the next <h4>,
    collecting all .htm links that resolve to GRETIL's own server.
    stem (filename without .htm extension) is used as external_id.
    """
    import httpx
    print(f'Fetching index: {GRETIL_INDEX}')
    resp = httpx.get(GRETIL_INDEX, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Locate the Buddhist section heading
    buddh_h = soup.find(id='RLBuddh')
    if not buddh_h:
        raise RuntimeError("Could not find Buddhist section anchor (#RLBuddh) on GRETIL index.")

    urls: list[tuple[str, str]] = []
    seen: set[str] = set()

    for sibling in buddh_h.next_siblings:
        if not hasattr(sibling, 'name') or not sibling.name:
            continue
        # Stop at the next section heading (h4 or h3 at the same level)
        if sibling.name in ('h3', 'h4') and sibling.get('id'):
            break
        for a in sibling.find_all('a', href=True):
            href: str = a['href']
            if not href.endswith('.htm'):
                continue
            # Skip external links (Taisho, TITUS, etc.)
            if href.startswith('http') and 'gretil.sub.uni-goettingen.de' not in href:
                continue
            # Skip in-page anchors and navigation pages
            if 'gretil.html' in href or href.startswith('#'):
                continue
            full_url = urljoin(GRETIL_INDEX, href)
            stem = Path(href).stem
            if stem not in seen:
                seen.add(stem)
                urls.append((full_url, stem))

    return urls


# ── Phase B: Download texts ───────────────────────────────────────────────────

def download_texts(url_stems: list[tuple[str, str]]) -> list[Path]:
    """Download each .htm file to LOCAL_DIR, skipping already-cached files."""
    import httpx
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for url, stem in tqdm(url_stems, desc='download', unit='text'):
        local = LOCAL_DIR / f'{stem}.htm'
        if local.exists():
            paths.append(local)
            continue
        try:
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            local.write_bytes(resp.content)
            paths.append(local)
            time.sleep(DOWNLOAD_DELAY)
        except Exception as e:
            print(f'  [WARN] download failed for {stem}: {e}')

    return paths


# ── Phase C: Parse .htm files ─────────────────────────────────────────────────

def _read_htm(path: Path) -> bytes:
    return path.read_bytes()


def _decode_htm(raw: bytes) -> str:
    """Decode bytes trying UTF-8 first, then Latin-1 (older GRETIL files)."""
    for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')


def _is_corpustei(soup: BeautifulSoup) -> bool:
    """True if this is the new TEI-derived corpustei HTML format."""
    return soup.find('hr') is not None and soup.find('div') is not None


def _parse_corpustei(soup: BeautifulSoup, stem: str) -> dict | None:
    """
    Parse new-format corpustei HTML.

    Structure:
      <h1> title </h1>
      <h2>Header</h2>  ... metadata ...
      <hr>
      <h2>Text</h2>
      <div>
        <p class='bold'> chapter heading </p>
        <p> text passage </p>
        ...
      </div>
    """
    # Title from <h1>
    h1 = soup.find('h1')
    title = h1.get_text(strip=True) if h1 else stem

    # Find the text <div> — first <div> after the <hr>
    children = [c for c in soup.body.children if hasattr(c, 'name') and c.name]
    hr_idx   = next((i for i, c in enumerate(children) if c.name == 'hr'), None)
    if hr_idx is None:
        return None
    text_div = next((c for c in children[hr_idx + 1:] if c.name == 'div'), None)
    if text_div is None:
        return None

    paragraphs: list[dict] = []
    current_chapter = ''

    for elem in text_div.children:
        if not hasattr(elem, 'name') or not elem.name:
            continue
        # Remove inline notes before extracting text
        for note in elem.find_all('span', class_='note'):
            note.decompose()

        cls  = ' '.join(elem.get('class') or [])
        text = elem.get_text(' ', strip=True)
        # Strip page references and verse-end markers
        text = _PAGE_REF_RE.sub('', text)
        text = re.sub(r'\s*//+\s*', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        if not text or len(text) < 10:
            continue

        if 'bold' in cls:
            current_chapter = text
        else:
            paragraphs.append({'text': text, 'chapter': current_chapter})

    if not paragraphs:
        return None

    return {
        'external_id': stem,
        'title':       title,
        'collection':  _detect_collection(title, stem),
        'input_by':    '',
        'paragraphs':  paragraphs,
    }


def _parse_legacy(soup: BeautifulSoup, html: str, stem: str) -> dict | None:
    """
    Parse old GRETIL format using a <pre> block with INPUT BY: header.
    """
    h1 = soup.find('h1') or soup.find('h2')
    title_tag = soup.title.string if soup.title else ''
    for sep in (' - ', ': ', ' — '):
        if sep in title_tag:
            title_tag = title_tag.split(sep, 1)[-1].strip()
    title = (h1.get_text(strip=True) if h1 else '') or title_tag or stem

    # Get INPUT BY for attribution
    m = re.search(r'input\s+by\s*[=:\-—]\s*([^\n<]{1,100})', html, re.IGNORECASE)
    input_by = m.group(1).strip() if m else ''

    pre = soup.find('pre')
    body_text = pre.get_text() if pre else soup.get_text()
    cleaned   = _clean_legacy_body(body_text)
    if not cleaned or len(cleaned) < 200:
        return None

    paragraphs: list[dict] = []
    current_chapter = ''
    for block in re.split(r'\n\n+', cleaned):
        block = block.strip()
        if not block:
            continue
        if len(block) < 100 and (
            block.isupper()
            or re.match(r'^[IVXivx]+\.?\s', block)
            or re.match(r'^(chapter|canto|varga|adhyaya|adhyāya)\b', block, re.I)
        ):
            current_chapter = block
            continue
        paragraphs.append({'text': block, 'chapter': current_chapter})

    if not paragraphs:
        return None

    return {
        'external_id': stem,
        'title':       title,
        'collection':  _detect_collection(title, stem),
        'input_by':    input_by,
        'paragraphs':  paragraphs,
    }


def _clean_legacy_body(body_text: str) -> str:
    """Strip header lines and editorial markup from legacy <pre> body text."""
    lines     = body_text.split('\n')
    cleaned   = []
    in_header = True

    for line in lines:
        stripped = line.strip()
        if in_header:
            if not stripped:
                continue
            if _LEGACY_HEADER_RE.match(stripped):
                continue
            in_header = False

        stripped = _VARIANTS_RE.sub('', stripped)
        stripped = _FOOTNOTE_RE.sub('', stripped)
        stripped = re.sub(r'\s*//+\s*$', '', stripped)
        cleaned.append(stripped)

    text = '\n'.join(cleaned)
    text = _EXCESS_NL_RE.sub('\n\n', text)
    return text.strip()


def parse_gretil_htm(path: Path) -> dict | None:
    """
    Dispatch to corpustei or legacy parser based on file structure.
    Returns {external_id, title, collection, input_by, paragraphs} or None.
    """
    raw  = _read_htm(path)
    html = _decode_htm(raw)
    soup = BeautifulSoup(html, 'html.parser')
    stem = path.stem

    if _is_corpustei(soup):
        return _parse_corpustei(soup, stem)
    return _parse_legacy(soup, html, stem)


# ── Phase D: Chunking ─────────────────────────────────────────────────────────

def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_paragraphs(paragraphs: list[dict], external_id: str) -> list[dict]:
    """Group paragraphs into ~400-token chunks at paragraph boundaries."""
    chunks: list[dict] = []
    current: list[dict] = []
    current_tokens = 0
    seq = 0

    def flush():
        nonlocal current, current_tokens, seq
        if not current:
            return
        text    = '\n\n'.join(p['text'] for p in current)
        chapter = next((p['chapter'] for p in current if p['chapter']), '')
        seq    += 1
        chunks.append({
            'text':      text,
            'reference': f'{external_id} §{seq}',
            'chapter':   chapter,
            'section':   '',
        })
        current        = []
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
    eid      = parsed['external_id']
    existing = execute_one(
        conn, "SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s",
        (corpus_id, eid),
    )
    if existing:
        return existing['id']

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO canon_texts
                (corpus_id, external_id, title_sanskrit, tradition, language,
                 collection, translator, url, word_count)
            VALUES (%s,%s,%s,'pre-sectarian','san',%s,%s,%s,%s)
            RETURNING id
        """, (
            corpus_id, eid,
            parsed['title'],
            parsed['collection'],
            parsed.get('input_by') or None,
            f'{GRETIL_BASE}/gretil/corpustei/transformations/html/{eid}.htm',
            sum(len(p['text'].split()) for p in parsed['paragraphs']),
        ))
        return cur.fetchone()['id']


# ── Main ingestion ────────────────────────────────────────────────────────────

def run(force: bool = False, skip_dl: bool = False, db_url: str | None = None):
    """Full GRETIL Buddhist Sanskrit ingestion pipeline."""

    # ── Phase A+B: Discover and download ────────────────────────────────────
    if not skip_dl:
        url_stems   = fetch_buddhist_urls()
        print(f'Found {len(url_stems)} Buddhist Sanskrit texts on GRETIL')
        local_paths = download_texts(url_stems)
        print(f'Downloaded/cached {len(local_paths)} files in {LOCAL_DIR}')
    else:
        local_paths = sorted(LOCAL_DIR.glob('*.htm'))
        print(f'GRETIL: {len(local_paths)} cached .htm files in {LOCAL_DIR}')

    if not local_paths:
        print('[ERROR] No .htm files found. Run without --skip-dl first.')
        return

    # ── Phase C: Parse and chunk ─────────────────────────────────────────────
    conn   = get_conn(url=db_url)
    corpus = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'gretil'")
    if not corpus:
        raise RuntimeError("Corpus 'gretil' missing — re-run sql/02_schema.sql.")
    corpus_id = corpus['id']

    # Build in-memory entity lookup (avoids O(chunks) DB round-trips)
    resolver  = EntityResolver(conn)
    _variants = resolver.get_all_variants()
    _name_to_eids: dict[str, list[int]] = {}
    for v in _variants:
        _name_to_eids.setdefault(v['name_text'].lower(), []).append(v['entity_id'])

    def _entity_ids_fast(text: str) -> list[int]:
        t = text.lower()
        return list({eid for name, eids in _name_to_eids.items() if name in t for eid in eids})

    # Prefetch existing state in 2 queries instead of 228×2 round-trips to Singapore
    existing_rows = execute(conn,
        "SELECT external_id, id FROM canon_texts WHERE corpus_id = %s", (corpus_id,))
    eid_to_tid: dict[str, int] = {r['external_id']: r['id'] for r in existing_rows}

    done_tids: set[int] = set()
    if eid_to_tid and not force:
        done_rows = execute(conn,
            "SELECT DISTINCT text_id FROM document_chunks WHERE text_id = ANY(%s)",
            (list(eid_to_tid.values()),))
        done_tids = {r['text_id'] for r in done_rows}

    print(f'\nGRETIL Phase C — parsing {len(local_paths)} files '
          f'({len(done_tids)} already ingested)...')
    all_items:   list[dict] = []
    skipped = parse_errors  = 0

    for path in tqdm(local_paths, desc='parse', unit='text'):
        parsed = parse_gretil_htm(path)
        if not parsed:
            parse_errors += 1
            continue

        eid = parsed['external_id']
        existing_tid = eid_to_tid.get(eid)

        # Skip already-ingested texts unless --force
        if existing_tid and existing_tid in done_tids and not force:
            skipped += 1
            continue

        chunks = chunk_paragraphs(parsed['paragraphs'], eid)
        if not chunks:
            parse_errors += 1
            continue

        try:
            text_id = get_or_create_text(conn, corpus_id, parsed)
            eid_to_tid[eid] = text_id  # cache new inserts
        except Exception as e:
            print(f'  [WARN] {eid}: DB error — {e}')
            conn.rollback()
            parse_errors += 1
            continue

        if force and existing_tid:
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
                'language':    'san',
                'tradition':   'pre-sectarian',
                'corpus_code': 'gretil',
                'collection':  parsed['collection'],
                'is_verse':    False,
            })

    conn.commit()

    if skipped:
        print(f'  Skipped {skipped} already-ingested texts (use --force to re-embed)')
    if parse_errors:
        print(f'  {parse_errors} files produced no usable text (short stotras, stubs, encoding errors)')
    if not all_items:
        print('  Nothing new to embed.')
        conn.close()
        return

    # ── Phase D+E: Embed and write in text-level batches with retry ──────────
    all_items.sort(key=lambda x: x['text_id'])
    text_groups = [
        (tid, list(items))
        for tid, items in groupby(all_items, key=lambda x: x['text_id'])
    ]

    total_texts  = len(text_groups)
    total_chunks = len(all_items)
    print(f'\nGRETIL Phase D+E — embedding and writing {total_chunks:,} chunks '
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
                                (text_id, chunk_index, chunk_text, reference, chapter,
                                 section, word_count, token_count, entity_ids,
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
                try:
                    conn.rollback()
                except Exception:
                    # Connection dropped (pooler idle timeout during Voyage retry sleep)
                    conn = get_conn(url=db_url)
                write_errors += n

        print(f'  [{batch_num}/{total_batches}] committed — '
              f'{written_chunks:,}/{total_chunks:,} chunks total')

    conn.close()
    total = len(all_items) - write_errors
    print(f'\nGRETIL done. {total:,} chunks ingested'
          + (f', {write_errors} write errors' if write_errors else '') + '.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest GRETIL Buddhist Sanskrit corpus')
    parser.add_argument('--skip-dl', action='store_true',
                        help='Skip download phase; use already-cached .htm files')
    parser.add_argument('--force', action='store_true',
                        help='Re-embed and overwrite already-ingested texts')
    args = parser.parse_args()
    run(force=args.force, skip_dl=args.skip_dl)
