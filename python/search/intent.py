# -*- coding: utf-8 -*-
"""
Intent detection for Bible chapter references.

Detects patterns like "Isaiah 40", "Matthew chapter 24", "1 Corinthians 13",
"Psalm 23" in a query string. Returns a (canonical_title, chapter_int) tuple
that callers can use to scope a hybrid search to a single chapter.

Returns None when there are zero or multiple references (ambiguous intent).
Callers should fall back to unfiltered search in those cases.
"""

from __future__ import annotations

import re
from typing import Optional

# lowercase alias → canonical title_english (as stored in canon_texts.title_english)
_ALIASES: dict[str, str] = {
    # Pentateuch
    'genesis': 'Genesis', 'gen': 'Genesis',
    'exodus': 'Exodus', 'exod': 'Exodus',
    'leviticus': 'Leviticus', 'lev': 'Leviticus',
    'numbers': 'Numbers', 'num': 'Numbers',
    'deuteronomy': 'Deuteronomy', 'deut': 'Deuteronomy',
    # Historical
    'joshua': 'Joshua', 'josh': 'Joshua',
    'judges': 'Judges', 'judg': 'Judges',
    'ruth': 'Ruth',
    '1 samuel': '1 Samuel', '1samuel': '1 Samuel', '1sam': '1 Samuel',
    '2 samuel': '2 Samuel', '2samuel': '2 Samuel', '2sam': '2 Samuel',
    '1 kings': '1 Kings', '1kings': '1 Kings', '1kgs': '1 Kings',
    '2 kings': '2 Kings', '2kings': '2 Kings', '2kgs': '2 Kings',
    '1 chronicles': '1 Chronicles', '1chronicles': '1 Chronicles', '1chr': '1 Chronicles',
    '2 chronicles': '2 Chronicles', '2chronicles': '2 Chronicles', '2chr': '2 Chronicles',
    'ezra': 'Ezra',
    'nehemiah': 'Nehemiah', 'neh': 'Nehemiah',
    'esther': 'Esther',
    # Wisdom
    'job': 'Job',
    'psalms': 'Psalms', 'psalm': 'Psalms', 'psa': 'Psalms', 'ps': 'Psalms',
    'proverbs': 'Proverbs', 'prov': 'Proverbs',
    'ecclesiastes': 'Ecclesiastes', 'eccl': 'Ecclesiastes', 'ecc': 'Ecclesiastes',
    'song of solomon': 'Song of Solomon', 'song': 'Song of Solomon', 'sos': 'Song of Solomon',
    # Prophets
    'isaiah': 'Isaiah', 'isa': 'Isaiah',
    'jeremiah': 'Jeremiah', 'jer': 'Jeremiah',
    'lamentations': 'Lamentations', 'lam': 'Lamentations',
    'ezekiel': 'Ezekiel', 'ezek': 'Ezekiel',
    'daniel': 'Daniel', 'dan': 'Daniel',
    'hosea': 'Hosea', 'hos': 'Hosea',
    'joel': 'Joel',
    'amos': 'Amos',
    'obadiah': 'Obadiah',
    'jonah': 'Jonah',
    'micah': 'Micah', 'mic': 'Micah',
    'nahum': 'Nahum', 'nah': 'Nahum',
    'habakkuk': 'Habakkuk', 'hab': 'Habakkuk',
    'zephaniah': 'Zephaniah', 'zeph': 'Zephaniah',
    'haggai': 'Haggai', 'hag': 'Haggai',
    'zechariah': 'Zechariah', 'zech': 'Zechariah',
    'malachi': 'Malachi', 'mal': 'Malachi',
    # Gospels & Acts
    'matthew': 'Matthew', 'matt': 'Matthew',
    'mark': 'Mark',
    'luke': 'Luke',
    'john': 'John',
    'acts': 'Acts',
    # Pauline epistles
    'romans': 'Romans', 'rom': 'Romans',
    '1 corinthians': '1 Corinthians', '1corinthians': '1 Corinthians', '1cor': '1 Corinthians',
    '2 corinthians': '2 Corinthians', '2corinthians': '2 Corinthians', '2cor': '2 Corinthians',
    'galatians': 'Galatians', 'gal': 'Galatians',
    'ephesians': 'Ephesians', 'eph': 'Ephesians',
    'philippians': 'Philippians', 'phil': 'Philippians', 'php': 'Philippians',
    'colossians': 'Colossians', 'col': 'Colossians',
    '1 thessalonians': '1 Thessalonians', '1thessalonians': '1 Thessalonians', '1thess': '1 Thessalonians',
    '2 thessalonians': '2 Thessalonians', '2thessalonians': '2 Thessalonians', '2thess': '2 Thessalonians',
    '1 timothy': '1 Timothy', '1timothy': '1 Timothy', '1tim': '1 Timothy',
    '2 timothy': '2 Timothy', '2timothy': '2 Timothy', '2tim': '2 Timothy',
    'titus': 'Titus',
    'philemon': 'Philemon', 'phlm': 'Philemon',
    'hebrews': 'Hebrews', 'heb': 'Hebrews',
    # General epistles
    'james': 'James', 'jas': 'James',
    '1 peter': '1 Peter', '1peter': '1 Peter', '1pet': '1 Peter',
    '2 peter': '2 Peter', '2peter': '2 Peter', '2pet': '2 Peter',
    '1 john': '1 John', '1john': '1 John', '1jn': '1 John',
    '2 john': '2 John', '2john': '2 John', '2jn': '2 John',
    '3 john': '3 John', '3john': '3 John', '3jn': '3 John',
    'jude': 'Jude',
    'revelation': 'Revelation', 'rev': 'Revelation',
}

# Sort by length descending so multi-word aliases like "song of solomon" and
# "1 corinthians" are tried before shorter overlapping ones.
_BOOK_PATTERN = '|'.join(
    re.escape(alias) for alias in sorted(_ALIASES, key=len, reverse=True)
)

# Match: BOOK + optional whitespace/comma + optional "chapter" keyword + chapter digit(s)
# Examples:  "Isaiah 40"  "1 Peter, chapter 5"  "Matthew chapter 24"  "Psalm 23"
_REF_RE = re.compile(
    rf'\b({_BOOK_PATTERN})\b[\s,]*(?:chapter\s+)?(\d{{1,3}})\b',
    re.IGNORECASE,
)


def detect_bible_ref(query: str) -> Optional[tuple[str, int]]:
    """Return (canonical_book_title, chapter_int) for an unambiguous single chapter reference.

    Returns None when zero or multiple references are detected — callers should
    fall back to unfiltered search in those cases.
    """
    matches = _REF_RE.findall(query)
    if len(matches) != 1:
        return None
    name_raw, chapter_raw = matches[0]
    canonical = _ALIASES.get(name_raw.lower())
    if not canonical:
        return None
    return (canonical, int(chapter_raw))
