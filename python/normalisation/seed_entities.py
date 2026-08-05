"""
Seed the buddhist_entities and entity_name_variants tables with ~200 core terms.

Run once:  python normalisation/seed_entities.py

Covers: The Four Noble Truths, Eightfold Path, Three Marks, Five Aggregates,
Twelve Links, Three Jewels, major schools, key persons, key texts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_conn

# fmt: off
ENTITIES: list[dict] = [
    # ── Core doctrinal concepts ───────────────────────────────────────────────
    {"type": "concept", "pali": "nibbāna",        "sanskrit": "nirvāṇa",       "zh": "涅槃",  "tib": "myang 'das",    "en": "nirvana",             "alt": ["liberation", "extinguishing", "cessation", "awakening"]},
    {"type": "concept", "pali": "dukkha",          "sanskrit": "duḥkha",        "zh": "苦",    "tib": "sdug bsngal",   "en": "suffering",            "alt": ["unsatisfactoriness", "dis-ease", "stress"]},
    {"type": "concept", "pali": "anicca",          "sanskrit": "anitya",        "zh": "無常",  "tib": "mi rtag pa",    "en": "impermanence",         "alt": ["transience", "impermanent"]},
    {"type": "concept", "pali": "anattā",          "sanskrit": "anātman",       "zh": "無我",  "tib": "bdag med",      "en": "non-self",             "alt": ["no-self", "selflessness", "anatta"]},
    {"type": "concept", "pali": "dhamma",          "sanskrit": "dharma",        "zh": "法",    "tib": "chos",          "en": "dharma",               "alt": ["teaching", "truth", "phenomenon", "Dhamma"]},
    {"type": "concept", "pali": "kamma",           "sanskrit": "karma",         "zh": "業",    "tib": "las",           "en": "karma",                "alt": ["action", "intentional action"]},
    {"type": "concept", "pali": "saṃsāra",        "sanskrit": "saṃsāra",       "zh": "輪迴",  "tib": "'khor ba",      "en": "samsara",              "alt": ["cycle of rebirth", "wheel of existence"]},
    {"type": "concept", "pali": "paṭicca-samuppāda","sanskrit":"pratītyasamutpāda","zh":"緣起","tib":"rten cing 'brel bar 'byung ba","en":"dependent origination","alt":["dependent arising","interdependent origination"]},
    {"type": "concept", "pali": "sīla",           "sanskrit": "śīla",          "zh": "戒",    "tib": "tshul khrims",  "en": "ethical conduct",      "alt": ["virtue", "morality", "precepts"]},
    {"type": "concept", "pali": "samādhi",        "sanskrit": "samādhi",       "zh": "定",    "tib": "ting nge 'dzin","en": "meditative concentration","alt": ["meditation", "concentration", "absorption"]},
    {"type": "concept", "pali": "paññā",          "sanskrit": "prajñā",        "zh": "慧",    "tib": "shes rab",      "en": "wisdom",               "alt": ["insight", "prajna", "discernment"]},
    {"type": "concept", "pali": "mettā",          "sanskrit": "maitrī",        "zh": "慈",    "tib": "byams pa",      "en": "loving-kindness",      "alt": ["lovingkindness", "metta", "love"]},
    {"type": "concept", "pali": "karuṇā",        "sanskrit": "karuṇā",        "zh": "悲",    "tib": "snying rje",    "en": "compassion",           "alt": ["karuna"]},
    {"type": "concept", "pali": "muditā",         "sanskrit": "muditā",        "zh": "喜",    "tib": "dga' ba",       "en": "sympathetic joy",      "alt": ["mudita", "appreciative joy"]},
    {"type": "concept", "pali": "upekkhā",        "sanskrit": "upekṣā",        "zh": "捨",    "tib": "btang snyoms",  "en": "equanimity",           "alt": ["upekkha"]},
    {"type": "concept", "pali": "bodhicitta",     "sanskrit": "bodhicitta",    "zh": "菩提心","tib": "byang chub kyi sems","en":"bodhicitta",         "alt": ["awakening mind", "mind of enlightenment"]},
    {"type": "concept", "pali": "suññatā",        "sanskrit": "śūnyatā",       "zh": "空",    "tib": "stong pa nyid", "en": "emptiness",            "alt": ["shunyata", "voidness", "openness"]},
    {"type": "concept", "pali": "bhāvanā",        "sanskrit": "bhāvanā",       "zh": "修習",  "tib": "sgom pa",       "en": "meditation practice",  "alt": ["cultivation", "development"]},
    {"type": "concept", "pali": "vipassanā",      "sanskrit": "vipaśyanā",     "zh": "觀",    "tib": "lhag mthong",   "en": "insight meditation",   "alt": ["insight", "vipassana", "vipashyana"]},
    {"type": "concept", "pali": "samatha",        "sanskrit": "śamatha",       "zh": "止",    "tib": "zhi gnas",      "en": "calm abiding",         "alt": ["tranquility meditation", "shamatha"]},

    # ── Four Noble Truths ─────────────────────────────────────────────────────
    {"type": "concept", "pali": "cattāri ariyasaccāni","sanskrit":"catvāry āryasatyāni","zh":"四聖諦","tib":"'phags pa'i bden pa bzhi","en":"Four Noble Truths","alt":["four truths","four realities"]},
    {"type": "concept", "pali": "dukkha-sacca",   "sanskrit": "duḥkha-satya",  "zh": "苦諦",  "tib": "sdug bsngal gyi bden pa", "en": "truth of suffering", "alt": []},
    {"type": "concept", "pali": "samudaya-sacca", "sanskrit": "samudaya-satya","zh": "集諦",   "tib": "kun 'byung gi bden pa",  "en": "truth of the origin of suffering", "alt": []},
    {"type": "concept", "pali": "nirodha-sacca",  "sanskrit": "nirodha-satya", "zh": "滅諦",  "tib": "'gog pa'i bden pa",      "en": "truth of the cessation of suffering", "alt": []},
    {"type": "concept", "pali": "magga-sacca",    "sanskrit": "mārga-satya",   "zh": "道諦",  "tib": "lam gyi bden pa",        "en": "truth of the path", "alt": []},

    # ── Eightfold Path ────────────────────────────────────────────────────────
    {"type": "concept", "pali": "ariya aṭṭhaṅgika magga","sanskrit":"āryāṣṭāṅgamārga","zh":"八正道","tib":"'phags pa'i lam yan lag brgyad","en":"Noble Eightfold Path","alt":["Eightfold Path","eight-fold path"]},
    {"type": "concept", "pali": "sammā diṭṭhi",   "sanskrit": "samyag-dṛṣṭi",  "zh": "正見",  "tib": "yang dag pa'i lta ba",   "en": "right view",         "alt": []},
    {"type": "concept", "pali": "sammā saṅkappa", "sanskrit": "samyak-saṅkalpa","zh": "正思惟","tib": "yang dag pa'i rtog pa",   "en": "right intention",    "alt": ["right thought"]},
    {"type": "concept", "pali": "sammā vācā",     "sanskrit": "samyag-vāc",    "zh": "正語",  "tib": "yang dag pa'i ngag",     "en": "right speech",       "alt": []},
    {"type": "concept", "pali": "sammā kammanta", "sanskrit": "samyak-karmānta","zh": "正業",  "tib": "yang dag pa'i las kyi mtha'","en": "right action",   "alt": []},
    {"type": "concept", "pali": "sammā ājīva",    "sanskrit": "samyag-ājīva",  "zh": "正命",  "tib": "yang dag pa'i 'tsho ba", "en": "right livelihood",   "alt": []},
    {"type": "concept", "pali": "sammā vāyāma",   "sanskrit": "samyag-vyāyāma","zh": "正精進", "tib": "yang dag pa'i rtsol ba", "en": "right effort",       "alt": []},
    {"type": "concept", "pali": "sammā sati",     "sanskrit": "samyak-smṛti",  "zh": "正念",  "tib": "yang dag pa'i dran pa",  "en": "right mindfulness",  "alt": []},
    {"type": "concept", "pali": "sammā samādhi",  "sanskrit": "samyak-samādhi","zh": "正定",  "tib": "yang dag pa'i ting nge 'dzin","en": "right concentration","alt": []},

    # ── Five Aggregates ───────────────────────────────────────────────────────
    {"type": "concept", "pali": "pañcakkhandha",  "sanskrit": "pañcaskandha",  "zh": "五蘊",  "tib": "phung po lnga",          "en": "five aggregates",    "alt": ["five skandhas", "five heaps"]},
    {"type": "concept", "pali": "rūpa",           "sanskrit": "rūpa",          "zh": "色",    "tib": "gzugs",                  "en": "form",               "alt": ["materiality", "matter"]},
    {"type": "concept", "pali": "vedanā",         "sanskrit": "vedanā",        "zh": "受",    "tib": "tshor ba",               "en": "feeling tone",       "alt": ["sensation", "feeling"]},
    {"type": "concept", "pali": "saññā",          "sanskrit": "saṃjñā",        "zh": "想",    "tib": "'du shes",               "en": "perception",         "alt": []},
    {"type": "concept", "pali": "saṅkhāra",      "sanskrit": "saṃskāra",      "zh": "行",    "tib": "'du byed",               "en": "mental formations",  "alt": ["volitional formations", "formations", "sankharas"]},
    {"type": "concept", "pali": "viññāṇa",       "sanskrit": "vijñāna",       "zh": "識",    "tib": "rnam par shes pa",       "en": "consciousness",      "alt": ["vinnana"]},

    # ── Three Jewels ──────────────────────────────────────────────────────────
    {"type": "concept", "pali": "tiratana",       "sanskrit": "triratna",      "zh": "三寶",  "tib": "dkon mchog gsum",        "en": "Three Jewels",       "alt": ["Three Refuges", "Triple Gem"]},
    {"type": "concept", "pali": "buddha",         "sanskrit": "buddha",        "zh": "佛",    "tib": "sangs rgyas",            "en": "Buddha",             "alt": ["Awakened One", "Enlightened One"]},
    {"type": "concept", "pali": "sangha",         "sanskrit": "saṅgha",        "zh": "僧",    "tib": "dge 'dun",               "en": "sangha",             "alt": ["community", "monastic community"]},

    # ── Key persons ───────────────────────────────────────────────────────────
    {"type": "person",  "pali": "Gotama",         "sanskrit": "Gautama",       "zh": "喬達摩", "tib": "Gautama",               "en": "Gautama Buddha",     "alt": ["Shakyamuni", "Śākyamuni", "Siddhattha", "Siddhartha", "the Buddha"]},
    {"type": "person",  "pali": "Ānanda",         "sanskrit": "Ānanda",        "zh": "阿難",   "tib": "Kun dga' bo",           "en": "Ananda",             "alt": []},
    {"type": "person",  "pali": "Sāriputta",      "sanskrit": "Śāriputra",     "zh": "舍利弗", "tib": "Shā ri'i bu",           "en": "Sariputta",          "alt": ["Sariputra"]},
    {"type": "person",  "pali": "Moggallāna",     "sanskrit": "Maudgalyāyana", "zh": "目犍連", "tib": "Mo'u 'gal gyi bu",      "en": "Moggallana",         "alt": ["Maudgalyayana"]},
    {"type": "person",  "pali": "Mahākassapa",    "sanskrit": "Mahākāśyapa",   "zh": "大迦葉", "tib": "Oe chen",               "en": "Mahakassapa",        "alt": ["Mahakashyapa"]},
    {"type": "person",  "pali": None,             "sanskrit": "Nāgārjuna",     "zh": "龍樹",   "tib": "Klu sgrub",             "en": "Nagarjuna",          "alt": []},
    {"type": "person",  "pali": None,             "sanskrit": "Vasubandhu",    "zh": "世親",   "tib": "dByig gnyen",           "en": "Vasubandhu",         "alt": []},
    {"type": "person",  "pali": None,             "sanskrit": "Asaṅga",        "zh": "無著",   "tib": "Thogs med",             "en": "Asanga",             "alt": []},
    {"type": "person",  "pali": None,             "sanskrit": None,            "zh": "玄奘",   "tib": None,                    "en": "Xuanzang",           "alt": ["Hsuan-tsang", "Hiuen Tsiang"]},

    # ── Key texts ─────────────────────────────────────────────────────────────
    {"type": "text",    "pali": "Dhammapada",      "sanskrit": "Dharmapada",    "zh": "法句經", "tib": "Chos kyi tshigs su bcad pa","en": "Dhammapada",      "alt": []},
    {"type": "text",    "pali": "Majjhima Nikāya", "sanskrit": None,            "zh": "中阿含經","tib": None,                   "en": "Majjhima Nikaya",    "alt": ["MN", "Middle Length Discourses"]},
    {"type": "text",    "pali": "Dīgha Nikāya",   "sanskrit": "Dīrgha Āgama",  "zh": "長阿含經","tib": None,                   "en": "Digha Nikaya",       "alt": ["DN", "Long Discourses"]},
    {"type": "text",    "pali": "Saṃyutta Nikāya","sanskrit": "Saṃyukta Āgama","zh": "雜阿含經","tib": None,                   "en": "Samyutta Nikaya",    "alt": ["SN", "Connected Discourses"]},
    {"type": "text",    "pali": "Aṅguttara Nikāya","sanskrit":"Ekottara Āgama","zh": "增一阿含經","tib": None,                  "en": "Anguttara Nikaya",   "alt": ["AN", "Numerical Discourses"]},
    {"type": "text",    "pali": "Vinaya Piṭaka",  "sanskrit": "Vinaya Piṭaka", "zh": "律藏",   "tib": "'Dul ba",               "en": "Vinaya Pitaka",      "alt": ["Vinaya", "monastic code"]},
    {"type": "text",    "pali": None,             "sanskrit": "Prajñāpāramitā Hṛdaya","zh":"般若波羅蜜多心經","tib":"Shes rab snying po","en":"Heart Sutra","alt":["Heart of Perfect Wisdom", "Hṛdaya"]},
    {"type": "text",    "pali": None,             "sanskrit": "Vimalakīrtinirdeśa","zh":"維摩詰所說經","tib":"Dri ma med par grags pas bstan pa","en":"Vimalakirti Sutra","alt":[]},
    {"type": "text",    "pali": None,             "sanskrit": "Saddharmapuṇḍarīka","zh":"妙法蓮華經","tib":"Dam pa'i chos pad ma dkar po","en":"Lotus Sutra","alt":["Lotus Sutra"]},
    {"type": "text",    "pali": None,             "sanskrit": "Avataṃsaka",    "zh": "華嚴經", "tib": "Phal po che",           "en": "Avatamsaka Sutra",   "alt": ["Flower Ornament Sutra", "Flower Garland Sutra"]},

    # ── Schools ───────────────────────────────────────────────────────────────
    {"type": "school",  "pali": "Theravāda",       "sanskrit": "Sthaviravāda",  "zh": "上座部", "tib": "gNas brtan pa",         "en": "Theravada",          "alt": ["School of the Elders"]},
    {"type": "school",  "pali": None,              "sanskrit": "Mahāyāna",      "zh": "大乘",   "tib": "Theg pa chen po",       "en": "Mahayana",           "alt": ["Great Vehicle"]},
    {"type": "school",  "pali": None,              "sanskrit": "Vajrayāna",     "zh": "金剛乘", "tib": "rDo rje theg pa",       "en": "Vajrayana",          "alt": ["Diamond Vehicle", "Tantric Buddhism"]},
    {"type": "school",  "pali": None,              "sanskrit": "Madhyamaka",    "zh": "中觀",   "tib": "dBu ma",                "en": "Madhyamaka",         "alt": ["Middle Way school"]},
    {"type": "school",  "pali": None,              "sanskrit": "Yogācāra",      "zh": "瑜伽行", "tib": "rNal 'byor spyod pa",   "en": "Yogacara",           "alt": ["Mind-Only", "Vijnanavada", "Consciousness-Only"]},
    {"type": "school",  "pali": None,              "sanskrit": "Chan",          "zh": "禪",     "tib": "bSam gtan",             "en": "Chan / Zen",         "alt": ["Zen", "Seon", "Thien"]},
    {"type": "school",  "pali": None,              "sanskrit": None,            "zh": "淨土",   "tib": "bDe ba can",            "en": "Pure Land",          "alt": ["Jodo", "Ching-t'u"]},
]
# fmt: on


def seed(conn=None):
    if conn is None:
        conn = get_conn()

    inserted = 0
    skipped = 0

    for e in ENTITIES:
        en = e['en']
        pali = e.get('pali')
        skt = e.get('sanskrit')
        zh = e.get('zh')
        tib = e.get('tib')
        alt = e.get('alt', [])
        etype = e['type']
        traditions = {
            'concept': ['theravada', 'mahayana', 'vajrayana'],
            'person': [],
            'text': [],
            'school': [],
            'place': [],
            'deity': [],
            'practice': ['theravada', 'mahayana', 'vajrayana'],
        }.get(etype, [])

        with conn.cursor() as cur:
            # Check for duplicate
            cur.execute(
                "SELECT id FROM buddhist_entities WHERE english_preferred = %s",
                (en,)
            )
            existing = cur.fetchone()
            if existing:
                skipped += 1
                continue

            cur.execute("""
                INSERT INTO buddhist_entities
                    (entity_type, pali, sanskrit, classical_chinese, tibetan,
                     english_preferred, english_alternates, traditions)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (etype, pali, skt, zh, tib, en, alt, traditions))
            entity_id = cur.fetchone()['id']

        # Insert name variants
        variants: list[tuple[str, str]] = []
        if pali:
            variants.append((pali, 'pli'))
        if skt:
            variants.append((skt, 'san'))
        if zh:
            variants.append((zh, 'lzh'))
        if tib:
            variants.append((tib, 'bo'))
        variants.append((en, 'en'))
        for a in alt:
            variants.append((a, 'en'))

        with conn.cursor() as cur:
            for name_text, lang in variants:
                cur.execute("""
                    INSERT INTO entity_name_variants (entity_id, name_text, language, is_primary)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (entity_id, name_text, language) DO NOTHING
                """, (entity_id, name_text, lang, name_text == en))

        conn.commit()
        inserted += 1

    print(f'Seeded {inserted} entities ({skipped} already present).')


if __name__ == '__main__':
    seed()
