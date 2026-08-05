"""
Buddhist Named Entity Recognition.

Scans a query or chunk text and returns the entity IDs of any Buddhist
terms found. Prioritises longer matches (e.g. "Four Noble Truths" before "Noble").
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from normalisation.entity_resolver import EntityResolver


class BuddhistNER:

    def __init__(self, resolver: 'EntityResolver'):
        self.resolver = resolver
        self._patterns: list[tuple[re.Pattern, int]] = []
        self._build_patterns()

    def _build_patterns(self):
        variants = self.resolver.get_all_variants()
        seen: dict[str, int] = {}
        for v in variants:
            text = v['name_text']
            eid = v['entity_id']
            if text in seen:
                continue
            seen[text] = eid
            # Escape for regex, word-boundary where possible
            escaped = re.escape(text)
            try:
                pat = re.compile(
                    r'(?<!\w)' + escaped + r'(?!\w)',
                    re.IGNORECASE | re.UNICODE
                )
                self._patterns.append((pat, eid))
            except re.error:
                pass

    def extract_entity_ids(self, text: str) -> list[int]:
        """Return de-duplicated entity IDs found in text, ordered by first occurrence."""
        found: dict[int, int] = {}   # entity_id -> position of first match
        for pat, eid in self._patterns:
            m = pat.search(text)
            if m and eid not in found:
                found[eid] = m.start()
        return [eid for eid, _ in sorted(found.items(), key=lambda x: x[1])]

    def reload(self):
        """Rebuild patterns after new entities are added."""
        self._patterns = []
        self._build_patterns()
