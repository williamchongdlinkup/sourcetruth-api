"""
Quran ingestion pipeline.

Source:      Tanzil Project (tanzil.net) — CC BY 3.0
Mirror:      github.com/acfatah/tanzil (CC BY 3.0)
Attribution: "Quran text sourced from the Tanzil Project (tanzil.net)"

Text:        Uthmani Arabic script (standard muṣḥaf orthography)
Language:    ara (Classical Arabic, ISO 639-3)
Corpus:      114 sūrahs, 6,236 āyāt
Tradition:   islam

Chunking:    ~10–15 āyāt per chunk; sūrah never split across chunks at head/tail
             Short sūrahs (≤ CHUNK_MAX āyāt) kept as a single chunk.

Usage:
  python ingestion/quran.py              # download + ingest
  python ingestion/quran.py --skip-dl    # use cached quran-uthmani.txt
  python ingestion/quran.py --force      # re-embed all sūrahs
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn, execute, execute_one
from embed import embed_documents

# ── Constants ─────────────────────────────────────────────────────────────────

DATA_DIR   = Path(os.getenv('DATA_DIR', 'data'))
LOCAL_FILE = DATA_DIR / 'raw' / 'quran' / 'quran-uthmani.txt'

# github.com/acfatah/tanzil CC BY 3.0 mirror of Tanzil Uthmani text
TANZIL_URL = (
    'https://raw.githubusercontent.com/acfatah/tanzil/master/data/quran-uthmani.txt'
)

CHUNK_TARGET_TOKENS = 400
CHUNK_MAX_TOKENS    = 600
TEXT_BATCH          = 20   # sūrahs per Voyage call group
MAX_RETRIES         = 5
RETRY_DELAY         = 30

def _approx_tokens(text: str) -> int:
    # UTF-8 byte length ÷ 3: Arabic encodes at 2 bytes/char, so this gives
    # a conservative token estimate consistent with embed.py's approach.
    return max(1, len(text.encode('utf-8')) // 3)


# ── Sūrah metadata ────────────────────────────────────────────────────────────
# (number, name_arabic, name_transliterated, name_english, revelation_type, ayah_count)
# revelation_type: 'meccan' | 'medinan'

SURAH_META: list[tuple[int, str, str, str, str, int]] = [
    (1,   'الفاتحة',       'Al-Fatihah',      'The Opening',                    'meccan',  7),
    (2,   'البقرة',        'Al-Baqarah',       'The Cow',                        'medinan', 286),
    (3,   'آل عمران',      'Al-Imran',         "Family of Imran",                'medinan', 200),
    (4,   'النساء',        'An-Nisa',          'The Women',                      'medinan', 176),
    (5,   'المائدة',       'Al-Maidah',        'The Table Spread',               'medinan', 120),
    (6,   'الأنعام',       'Al-Anam',          'The Cattle',                     'meccan',  165),
    (7,   'الأعراف',       'Al-Araf',          'The Heights',                    'meccan',  206),
    (8,   'الأنفال',       'Al-Anfal',         'The Spoils of War',              'medinan', 75),
    (9,   'التوبة',        'At-Tawbah',        'The Repentance',                 'medinan', 129),
    (10,  'يونس',          'Yunus',            'Jonah',                          'meccan',  109),
    (11,  'هود',           'Hud',              'Hud',                            'meccan',  123),
    (12,  'يوسف',          'Yusuf',            'Joseph',                         'meccan',  111),
    (13,  'الرعد',         'Ar-Rad',           'The Thunder',                    'medinan', 43),
    (14,  'إبراهيم',       'Ibrahim',          'Abraham',                        'meccan',  52),
    (15,  'الحجر',         'Al-Hijr',          'The Rocky Tract',                'meccan',  99),
    (16,  'النحل',         'An-Nahl',          'The Bee',                        'meccan',  128),
    (17,  'الإسراء',       'Al-Isra',          'The Night Journey',              'meccan',  111),
    (18,  'الكهف',         'Al-Kahf',          'The Cave',                       'meccan',  110),
    (19,  'مريم',          'Maryam',           'Mary',                           'meccan',  98),
    (20,  'طه',            'Ta-Ha',            'Ta-Ha',                          'meccan',  135),
    (21,  'الأنبياء',      'Al-Anbiya',        'The Prophets',                   'meccan',  112),
    (22,  'الحج',          'Al-Hajj',          'The Pilgrimage',                 'medinan', 78),
    (23,  'المؤمنون',      'Al-Muminun',       'The Believers',                  'meccan',  118),
    (24,  'النور',         'An-Nur',           'The Light',                      'medinan', 64),
    (25,  'الفرقان',       'Al-Furqan',        'The Criterion',                  'meccan',  77),
    (26,  'الشعراء',       'Ash-Shuara',       'The Poets',                      'meccan',  227),
    (27,  'النمل',         'An-Naml',          'The Ant',                        'meccan',  93),
    (28,  'القصص',         'Al-Qasas',         'The Stories',                    'meccan',  88),
    (29,  'العنكبوت',      'Al-Ankabut',       'The Spider',                     'meccan',  69),
    (30,  'الروم',         'Ar-Rum',           'The Romans',                     'meccan',  60),
    (31,  'لقمان',         'Luqman',           'Luqman',                         'meccan',  34),
    (32,  'السجدة',        'As-Sajdah',        'The Prostration',                'meccan',  30),
    (33,  'الأحزاب',       'Al-Ahzab',         'The Combined Forces',            'medinan', 73),
    (34,  'سبأ',           'Saba',             'Sheba',                          'meccan',  54),
    (35,  'فاطر',          'Fatir',            'Originator',                     'meccan',  45),
    (36,  'يس',            'Ya-Sin',           'Ya-Sin',                         'meccan',  83),
    (37,  'الصافات',       'As-Saffat',        'Those Who Set the Ranks',        'meccan',  182),
    (38,  'ص',             'Sad',              'Sad',                            'meccan',  88),
    (39,  'الزمر',         'Az-Zumar',         'The Troops',                     'meccan',  75),
    (40,  'غافر',          'Ghafir',           'The Forgiver',                   'meccan',  85),
    (41,  'فصلت',          'Fussilat',         'Explained in Detail',            'meccan',  54),
    (42,  'الشورى',        'Ash-Shuraa',       'The Consultation',               'meccan',  53),
    (43,  'الزخرف',        'Az-Zukhruf',       'The Ornaments of Gold',          'meccan',  89),
    (44,  'الدخان',        'Ad-Dukhan',        'The Smoke',                      'meccan',  59),
    (45,  'الجاثية',       'Al-Jathiyah',      'The Crouching',                  'meccan',  37),
    (46,  'الأحقاف',       'Al-Ahqaf',         'The Wind-Curved Sandhills',      'meccan',  35),
    (47,  'محمد',          'Muhammad',         'Muhammad',                       'medinan', 38),
    (48,  'الفتح',         'Al-Fath',          'The Victory',                    'medinan', 29),
    (49,  'الحجرات',       'Al-Hujurat',       'The Rooms',                      'medinan', 18),
    (50,  'ق',             'Qaf',              'Qaf',                            'meccan',  45),
    (51,  'الذاريات',      'Adh-Dhariyat',     'The Winnowing Winds',            'meccan',  60),
    (52,  'الطور',         'At-Tur',           'The Mount',                      'meccan',  49),
    (53,  'النجم',         'An-Najm',          'The Star',                       'meccan',  62),
    (54,  'القمر',         'Al-Qamar',         'The Moon',                       'meccan',  55),
    (55,  'الرحمن',        'Ar-Rahman',        'The Beneficent',                 'meccan',  78),
    (56,  'الواقعة',       'Al-Waqiah',        'The Inevitable',                 'meccan',  96),
    (57,  'الحديد',        'Al-Hadid',         'The Iron',                       'medinan', 29),
    (58,  'المجادلة',      'Al-Mujadila',      'The Pleading Woman',             'medinan', 22),
    (59,  'الحشر',         'Al-Hashr',         'The Exile',                      'medinan', 24),
    (60,  'الممتحنة',      'Al-Mumtahanah',    'She that is to be Examined',     'medinan', 13),
    (61,  'الصف',          'As-Saf',           'The Ranks',                      'medinan', 14),
    (62,  'الجمعة',        'Al-Jumuah',        'Friday',                         'medinan', 11),
    (63,  'المنافقون',     'Al-Munafiqun',     'The Hypocrites',                 'medinan', 11),
    (64,  'التغابن',       'At-Taghabun',      'The Mutual Disillusion',         'medinan', 18),
    (65,  'الطلاق',        'At-Talaq',         'The Divorce',                    'medinan', 12),
    (66,  'التحريم',       'At-Tahrim',        'The Prohibition',                'medinan', 12),
    (67,  'الملك',         'Al-Mulk',          'The Sovereignty',                'meccan',  30),
    (68,  'القلم',         'Al-Qalam',         'The Pen',                        'meccan',  52),
    (69,  'الحاقة',        'Al-Haqqah',        'The Reality',                    'meccan',  52),
    (70,  'المعارج',       'Al-Maarij',        'The Ascending Stairways',        'meccan',  44),
    (71,  'نوح',           'Nuh',              'Noah',                           'meccan',  28),
    (72,  'الجن',          'Al-Jinn',          'The Jinn',                       'meccan',  28),
    (73,  'المزمل',        'Al-Muzzammil',     'The Enshrouded One',             'meccan',  20),
    (74,  'المدثر',        'Al-Muddaththir',   'The Cloaked One',                'meccan',  56),
    (75,  'القيامة',       'Al-Qiyamah',       'The Resurrection',               'meccan',  40),
    (76,  'الإنسان',       'Al-Insan',         'The Human',                      'medinan', 31),
    (77,  'المرسلات',      'Al-Mursalat',      'The Emissaries',                 'meccan',  50),
    (78,  'النبأ',         'An-Naba',          'The Announcement',               'meccan',  40),
    (79,  'النازعات',      'An-Naziat',        'Those who drag forth',           'meccan',  46),
    (80,  'عبس',           'Abasa',            'He Frowned',                     'meccan',  42),
    (81,  'التكوير',       'At-Takwir',        'The Overthrowing',               'meccan',  29),
    (82,  'الانفطار',      'Al-Infitar',       'The Cleaving',                   'meccan',  19),
    (83,  'المطففين',      'Al-Mutaffifin',    'The Defrauding',                 'meccan',  36),
    (84,  'الانشقاق',      'Al-Inshiqaq',      'The Sundering',                  'meccan',  25),
    (85,  'البروج',        'Al-Buruj',         'The Mansions of the Stars',      'meccan',  22),
    (86,  'الطارق',        'At-Tariq',         'The Nightcomer',                 'meccan',  17),
    (87,  'الأعلى',        'Al-Ala',           'The Most High',                  'meccan',  19),
    (88,  'الغاشية',       'Al-Ghashiyah',     'The Overwhelming',               'meccan',  26),
    (89,  'الفجر',         'Al-Fajr',          'The Dawn',                       'meccan',  30),
    (90,  'البلد',         'Al-Balad',         'The City',                       'meccan',  20),
    (91,  'الشمس',         'Ash-Shams',        'The Sun',                        'meccan',  15),
    (92,  'الليل',         'Al-Layl',          'The Night',                      'meccan',  21),
    (93,  'الضحى',         'Ad-Duhaa',         'The Morning Hours',              'meccan',  11),
    (94,  'الشرح',         'Ash-Sharh',        'The Relief',                     'meccan',  8),
    (95,  'التين',         'At-Tin',           'The Fig',                        'meccan',  8),
    (96,  'العلق',         'Al-Alaq',          'The Clot',                       'meccan',  19),
    (97,  'القدر',         'Al-Qadr',          'The Power',                      'meccan',  5),
    (98,  'البينة',        'Al-Bayyinah',      'The Clear Proof',                'medinan', 8),
    (99,  'الزلزلة',       'Az-Zalzalah',      'The Earthquake',                 'medinan', 8),
    (100, 'العاديات',      'Al-Adiyat',        'The Courser',                    'meccan',  11),
    (101, 'القارعة',       'Al-Qariah',        'The Calamity',                   'meccan',  11),
    (102, 'التكاثر',       'At-Takathur',      'The Rivalry in world increase',  'meccan',  8),
    (103, 'العصر',         'Al-Asr',           'The Declining Day',              'meccan',  3),
    (104, 'الهمزة',        'Al-Humazah',       'The Traducer',                   'meccan',  9),
    (105, 'الفيل',         'Al-Fil',           'The Elephant',                   'meccan',  5),
    (106, 'قريش',          'Quraysh',          'Quraysh',                        'meccan',  4),
    (107, 'الماعون',       'Al-Maun',          'The Small Kindnesses',           'meccan',  7),
    (108, 'الكوثر',        'Al-Kawthar',       'The Abundance',                  'meccan',  3),
    (109, 'الكافرون',      'Al-Kafirun',       'The Disbelievers',               'meccan',  6),
    (110, 'النصر',         'An-Nasr',          'The Divine Support',             'medinan', 3),
    (111, 'المسد',         'Al-Masad',         'The Palm Fiber',                 'meccan',  5),
    (112, 'الإخلاص',       'Al-Ikhlas',        'The Sincerity',                  'meccan',  4),
    (113, 'الفلق',         'Al-Falaq',         'The Daybreak',                   'meccan',  5),
    (114, 'الناس',         'An-Nas',           'The Mankind',                    'meccan',  6),
]

# Build lookup: surah_num → metadata dict
_SURAH_LOOKUP: dict[int, dict] = {
    num: {
        'number':         num,
        'name_arabic':    name_ar,
        'name_translit':  name_tr,
        'name_english':   name_en,
        'revelation':     rev_type,
        'ayah_count':     ayah_count,
        'external_id':    f'quran-{num:03d}',
        'collection':     re.sub(r"[^a-z0-9]+", '-', name_tr.lower()).strip('-'),
    }
    for num, name_ar, name_tr, name_en, rev_type, ayah_count in SURAH_META
}


# ── Phase A: Download ─────────────────────────────────────────────────────────

def download_text() -> Path:
    LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_FILE.exists():
        print(f'Using cached {LOCAL_FILE}')
        return LOCAL_FILE
    print(f'Downloading Tanzil Uthmani text from GitHub mirror...')
    resp = httpx.get(TANZIL_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    LOCAL_FILE.write_bytes(resp.content)
    print(f'Saved to {LOCAL_FILE} ({LOCAL_FILE.stat().st_size:,} bytes)')
    return LOCAL_FILE


# ── Phase B: Parse pipe-delimited text ───────────────────────────────────────

def parse_tanzil(path: Path) -> dict[int, list[tuple[int, str]]]:
    """
    Parse 'surah|ayah|text' lines into {surah_num: [(ayah_num, text), ...]}
    Skips comment lines (starting with #).
    """
    surah_ayat: dict[int, list[tuple[int, str]]] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('|', 2)
        if len(parts) != 3:
            continue
        try:
            s, a, text = int(parts[0]), int(parts[1]), parts[2]
        except ValueError:
            continue
        surah_ayat.setdefault(s, []).append((a, text))
    return surah_ayat


# ── Phase C: Chunking ─────────────────────────────────────────────────────────

def chunk_surah(
    surah_num: int,
    ayat: list[tuple[int, str]],
) -> list[dict]:
    """
    Group ayat into ~CHUNK_TARGET_TOKENS chunks.
    Each chunk: {text, ayah_start, ayah_end, reference}
    A sūrah is never split mid-chunk across the boundary; flush on max.
    """
    chunks: list[dict] = []
    current_ayat: list[tuple[int, str]] = []
    current_tokens = 0

    def flush():
        if not current_ayat:
            return
        text      = '\n'.join(f'({a}) {t}' for a, t in current_ayat)
        ayah_start = current_ayat[0][0]
        ayah_end   = current_ayat[-1][0]
        chunks.append({
            'text':       text,
            'ayah_start': ayah_start,
            'ayah_end':   ayah_end,
            'reference':  f'Q {surah_num}:{ayah_start}' if ayah_start == ayah_end
                          else f'Q {surah_num}:{ayah_start}–{ayah_end}',
        })

    for ayah_num, text in ayat:
        tok = _approx_tokens(text)
        if current_tokens + tok > CHUNK_MAX_TOKENS and current_ayat:
            flush()
            current_ayat  = []
            current_tokens = 0
        current_ayat.append((ayah_num, text))
        current_tokens += tok
        if current_tokens >= CHUNK_TARGET_TOKENS:
            flush()
            current_ayat  = []
            current_tokens = 0

    flush()
    return chunks


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_corpus(conn) -> int:
    row = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'quran'")
    if not row:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO source_corpora (code, name, tradition, language, license, base_url)
                VALUES ('quran', 'Tanzil Quran Corpus', 'islam', 'ara', 'CC BY 3.0',
                        'https://tanzil.net')
                ON CONFLICT (code) DO NOTHING
            """)
        conn.commit()
        row = execute_one(conn, "SELECT id FROM source_corpora WHERE code = 'quran'")
    return row['id']


def get_or_create_surah(conn, corpus_id: int, meta: dict) -> int:
    eid = meta['external_id']
    row = execute_one(
        conn, "SELECT id FROM canon_texts WHERE corpus_id = %s AND external_id = %s",
        (corpus_id, eid),
    )
    if row:
        return row['id']
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO canon_texts
                (corpus_id, external_id, title_original, title_english,
                 tradition, language, collection, number, url, word_count)
            VALUES (%s,%s,%s,%s,'islam','ara',%s,%s,%s,%s)
            RETURNING id
        """, (
            corpus_id,
            eid,
            f"{meta['name_arabic']} ({meta['name_translit']})",
            meta['name_english'],
            meta['collection'],
            str(meta['number']),
            f"https://tanzil.net/#trans/en.sahih/{meta['number']}",
            meta['ayah_count'],
        ))
        return cur.fetchone()['id']


# ── Main ingestion ────────────────────────────────────────────────────────────

def run(force: bool = False, skip_dl: bool = False, db_url: str | None = None):
    """Full Quran ingestion pipeline."""

    # Phase A: Download
    path = LOCAL_FILE if skip_dl else download_text()
    if not path.exists():
        print('[ERROR] quran-uthmani.txt not found. Run without --skip-dl first.')
        return

    # Phase B: Parse
    print('Parsing Tanzil text...')
    surah_ayat = parse_tanzil(path)
    print(f'  {len(surah_ayat)} sūrahs, {sum(len(v) for v in surah_ayat.values()):,} āyāt')

    # Phase C: Chunk + collect
    conn      = get_conn(url=db_url)
    corpus_id = _ensure_corpus(conn)

    existing_rows = execute(
        conn, "SELECT external_id, id FROM canon_texts WHERE corpus_id = %s", (corpus_id,)
    )
    eid_to_tid: dict[str, int] = {r['external_id']: r['id'] for r in existing_rows}

    done_tids: set[int] = set()
    if eid_to_tid and not force:
        done_rows = execute(
            conn,
            "SELECT DISTINCT text_id FROM document_chunks WHERE text_id = ANY(%s)",
            (list(eid_to_tid.values()),),
        )
        done_tids = {r['text_id'] for r in done_rows}

    print(f'\nQuran Phase C — chunking {len(surah_ayat)} sūrahs '
          f'({len(done_tids)} already ingested)...')

    all_items: list[dict] = []

    for surah_num in tqdm(sorted(surah_ayat.keys()), desc='chunk', unit='sūrah'):
        meta = _SURAH_LOOKUP.get(surah_num)
        if not meta:
            print(f'  [WARN] No metadata for sūrah {surah_num} — skipping')
            continue

        eid          = meta['external_id']
        existing_tid = eid_to_tid.get(eid)
        if existing_tid and existing_tid in done_tids and not force:
            continue

        if force and existing_tid:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM document_chunks WHERE text_id = %s", (existing_tid,))
            conn.commit()

        ayat   = surah_ayat[surah_num]
        chunks = chunk_surah(surah_num, ayat)
        if not chunks:
            continue

        try:
            text_id = get_or_create_surah(conn, corpus_id, meta)
            eid_to_tid[eid] = text_id
        except Exception as e:
            print(f'  [WARN] sūrah {surah_num}: DB error — {e}')
            conn.rollback()
            continue

        for i, chunk in enumerate(chunks):
            all_items.append({
                'text_id':     text_id,
                'chunk_index': i,
                'text':        chunk['text'],
                'reference':   chunk['reference'],
                'chapter':     meta['name_translit'],
                'section':     meta['revelation'],
                'word_count':  len(chunk['text'].split()),
                'token_count': _approx_tokens(chunk['text']),
                'entity_ids':  [],
                'language':    'ara',
                'tradition':   'islam',
                'corpus_code': 'quran',
                'collection':  meta['collection'],
                'is_verse':    True,   # Quranic āyāt are verse units by definition
            })

    conn.commit()

    if not all_items:
        print('Nothing new to embed.')
        conn.close()
        return

    # Phase D+E: Embed + write in sūrah-level batches
    from itertools import groupby
    all_items.sort(key=lambda x: x['text_id'])
    text_groups = [
        (tid, list(items))
        for tid, items in groupby(all_items, key=lambda x: x['text_id'])
    ]

    total_texts  = len(text_groups)
    total_chunks = len(all_items)
    total_batches = (total_texts + TEXT_BATCH - 1) // TEXT_BATCH
    print(f'\nQuran Phase D+E — embedding {total_chunks:,} chunks '
          f'across {total_texts} sūrahs ({total_batches} batches)...')

    write_errors = written_chunks = 0

    for batch_idx in range(0, total_texts, TEXT_BATCH):
        batch_groups = text_groups[batch_idx:batch_idx + TEXT_BATCH]
        batch_items  = [item for _, items in batch_groups for item in items]
        batch_num    = batch_idx // TEXT_BATCH + 1

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
                print(f'  [WARN] write error for sūrah text_id {_tid}: {e}')
                try:
                    conn.rollback()
                except Exception:
                    conn = get_conn(url=db_url)
                write_errors += n

        print(f'  [{batch_num}/{total_batches}] committed — '
              f'{written_chunks:,}/{total_chunks:,} chunks total')

    conn.close()
    total = len(all_items) - write_errors
    print(f'\nQuran done. {total:,} chunks ingested'
          + (f', {write_errors} write errors' if write_errors else '') + '.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ingest Tanzil Quran corpus')
    parser.add_argument('--skip-dl', action='store_true', help='Use cached text file')
    parser.add_argument('--force',   action='store_true', help='Re-embed all sūrahs')
    args = parser.parse_args()
    run(force=args.force, skip_dl=args.skip_dl)
