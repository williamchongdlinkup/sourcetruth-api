"""
Buddhist entity resolver.

Looks up canonical entity records by any name variant across traditions.
Used by the NER module and the search layer to boost entity-matched results.
"""

from __future__ import annotations

import re
from typing import Optional

from db import get_conn


class EntityResolver:

    def __init__(self, conn=None):
        self._conn = conn or get_conn()

    def get_entity(self, entity_id: int) -> Optional[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM buddhist_entities WHERE id = %s",
                (entity_id,)
            )
            return cur.fetchone()

    def lookup_by_name(self, name: str) -> list[dict]:
        """Return all entities whose name variants fuzzy-match `name`."""
        name_clean = name.strip().lower()
        with self._conn.cursor() as cur:
            # Exact match first
            cur.execute("""
                SELECT DISTINCT be.*
                FROM entity_name_variants env
                JOIN buddhist_entities be ON be.id = env.entity_id
                WHERE lower(env.name_text) = %s
            """, (name_clean,))
            exact = cur.fetchall()
            if exact:
                return exact

            # Trigram similarity fallback (threshold 0.35)
            cur.execute("""
                SELECT DISTINCT be.*, similarity(lower(env.name_text), %s) AS sim
                FROM entity_name_variants env
                JOIN buddhist_entities be ON be.id = env.entity_id
                WHERE lower(env.name_text) %% %s
                ORDER BY sim DESC
                LIMIT 5
            """, (name_clean, name_clean))
            return cur.fetchall()

    def get_entity_ids_for_text(self, text: str, chunk_text: str) -> list[int]:
        """
        Return entity IDs whose name variants appear in chunk_text.
        Used during ingestion to tag chunks.
        """
        with self._conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT env.entity_id
                FROM entity_name_variants env
                WHERE %s ILIKE '%%' || env.name_text || '%%'
                   OR %s ILIKE '%%' || env.name_text || '%%'
                LIMIT 20
            """, (chunk_text, text))
            rows = cur.fetchall()
        return [r['entity_id'] for r in rows]

    def get_all_variants(self) -> list[dict]:
        """Return all name variants — used to build the NER pattern set."""
        with self._conn.cursor() as cur:
            cur.execute("""
                SELECT env.name_text, env.language, env.entity_id, be.entity_type
                FROM entity_name_variants env
                JOIN buddhist_entities be ON be.id = env.entity_id
                ORDER BY length(env.name_text) DESC
            """)
            return cur.fetchall()
