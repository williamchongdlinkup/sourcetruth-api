"""
BDRC (Buddhist Digital Resource Center) — Ingestion Scoping

This file documents what a BDRC ingestion pipeline would require.
It is NOT runnable yet; it serves as the research artifact for planning Phase 3+.

Status: SCOPED (not implemented)
Target: Tibetan-language canonical texts — Kangyur, Tengyur (complement to 84000 English)
API:    https://library.bdrc.io · SPARQL: https://ldspdi.bdrc.io/sparql

────────────────────────────────────────────────────────────────────────────
WHAT BDRC HAS
────────────────────────────────────────────────────────────────────────────

BDRC holds 200,000+ volumes of Buddhist material in their digital library:
- Kangyur: ~108 volumes (Buddha's words in Tibetan)
- Tengyur: ~224 volumes (commentarial literature)
- Plus thousands of volumes of other Tibetan works

Formats available:
- Scanned PDFs (IIIF images): all volumes have scans
- ETEXT (digital transcriptions): a subset — important for NLP use
- RDF metadata (Linked Data): all works have machine-readable metadata

For CanonRAG we want ETEXT, not scans.

────────────────────────────────────────────────────────────────────────────
BDRC API ACCESS
────────────────────────────────────────────────────────────────────────────

1. SPARQL endpoint (no auth, rate-limited):
   https://ldspdi.bdrc.io/sparql

   Query etexts in the Kangyur:
   ```sparql
   PREFIX bdr: <http://purl.bdrc.io/resource/>
   PREFIX bdrc: <http://purl.bdrc.io/ontology/core/>
   SELECT ?work ?etext ?title WHERE {
       ?work a bdrc:Work ;
             bdrc:workIsAbout bdr:TopicKangyur ;
             bdrc:workHasInstance ?instance .
       ?instance bdrc:instanceHasVolume ?vol .
       ?vol bdrc:volumeHasEtext ?etext .
       OPTIONAL { ?work bdrc:workTitle ?title . FILTER(lang(?title) = "bo") }
   }
   LIMIT 1000
   ```

2. Linked Data resource endpoint (no auth):
   https://purl.bdrc.io/resource/{id}    → HTML or JSON-LD
   https://purl.bdrc.io/resource/{id}.jsonld

3. etext content:
   https://purl.bdrc.io/etext/{etext-id}/plain   → UTF-8 Tibetan plain text
   https://purl.bdrc.io/etext/{etext-id}/json    → structured JSON with segments

4. IIIF manifests (scans, not useful for NLP):
   https://iiif.bdrc.io/bdr:{work-id}::manifest

────────────────────────────────────────────────────────────────────────────
WHAT A BDRC PIPELINE WOULD LOOK LIKE
────────────────────────────────────────────────────────────────────────────

Step 1 — Catalog SPARQL query:
  Query for all Kangyur/Tengyur works that have etext instances.
  Store work_id, etext_id, toh_number (if mapped), title_bo.

Step 2 — Toh mapping:
  BDRC works are identified by their own IDs (e.g., W22084 = Derge Kangyur).
  Need to join against a Toh-number mapping table (BDRC provides this in RDF).

Step 3 — etext download:
  For each etext_id: GET https://purl.bdrc.io/etext/{id}/json
  Response: { "pages": [{"content": "...", "pagination": "F.1.a"}, ...] }

Step 4 — chunking:
  Group etext pages into ~400-token chunks (1 Tibetan syllable ≈ 3 chars).
  Use pagination marks (F.1.a, F.1.b, ...) as references.

Step 5 — embedding:
  Voyage multilingual-2 handles Tibetan Unicode script — no change needed.

Step 6 — tradition/language:
  tradition = 'vajrayana', language = 'bo' (Tibetan)

────────────────────────────────────────────────────────────────────────────
OPEN QUESTIONS BEFORE BUILDING
────────────────────────────────────────────────────────────────────────────

Q1. How many Kangyur/Tengyur texts have etext (digital text) vs. scan only?
    BDRC has been digitising, but many texts are still scan-only.
    Rough estimate: ~50% of Kangyur, ~25% of Tengyur have etexts.

Q2. Which recension?
    Derge (W22084) is the standard scholarly reference.
    Also available: Narthang, Peking, Choné.
    Start with Derge.

Q3. Authentication?
    BDRC's SPARQL and etext endpoints are public, but heavy usage may need
    an API key. Email: tech@bdrc.io to register as a research user.

Q4. Volume vs. chunk count?
    Kangyur: ~4,000 texts, but many are short (1–5 pages).
    Estimated chunks: 40,000–80,000 → ~1–2 GB storage.
    This DOES NOT fit Account 1 (500 MB free tier limit).
    Plan: Account 3 dedicated to Tibetan originals.

Q5. License?
    BDRC content: Open access for non-commercial use.
    Check individual work licenses via SPARQL:
    ?work bdrc:workLicense ?license

────────────────────────────────────────────────────────────────────────────
RECOMMENDED NEXT ACTIONS
────────────────────────────────────────────────────────────────────────────

1. Run the catalog SPARQL query to count how many Kangyur etexts exist.
2. Fetch 5–10 sample etexts via /etext/{id}/json to verify format.
3. Check if Toh-number → BDRC work-id mapping is in their RDF.
4. Spin up Supabase Account 3 for Tibetan originals (~1–2 GB).
5. Build pipeline modelled on eighty4000.py (same 4-phase structure).

────────────────────────────────────────────────────────────────────────────
QUICK SPARQL SMOKE TEST (run this to verify access)
────────────────────────────────────────────────────────────────────────────

curl -H "Accept: application/sparql-results+json" \
     --data-urlencode "query=SELECT (COUNT(*) AS ?n) WHERE { ?s a <http://purl.bdrc.io/ontology/core/Etext> }" \
     https://ldspdi.bdrc.io/sparql

Expected: a count in the thousands, confirming the endpoint is live.
"""

# ── BDRC SPARQL helper (smoke test) ──────────────────────────────────────────

SPARQL_ENDPOINT = 'https://ldspdi.bdrc.io/sparql'

CATALOG_QUERY = """
PREFIX bdr:  <http://purl.bdrc.io/resource/>
PREFIX bdrc: <http://purl.bdrc.io/ontology/core/>
SELECT ?work ?etext ?toh WHERE {
    ?work bdrc:workHasInstance ?instance .
    ?instance bdrc:instanceHasVolume ?vol .
    ?vol bdrc:volumeHasEtext ?etext .
    OPTIONAL { ?work bdrc:workCatalogInfo ?cat .
               ?cat bdrc:catalogInfoNote ?toh .
               FILTER(CONTAINS(str(?toh), "Toh")) }
}
LIMIT 100
"""


def smoke_test():
    """Verify BDRC SPARQL endpoint is reachable and returns etext count."""
    import requests
    resp = requests.post(
        SPARQL_ENDPOINT,
        data={'query': 'SELECT (COUNT(*) AS ?n) WHERE { ?s a <http://purl.bdrc.io/ontology/core/Etext> }'},
        headers={'Accept': 'application/sparql-results+json'},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    count = result['results']['bindings'][0]['n']['value']
    print(f'BDRC etext count: {count}')
    return int(count)


if __name__ == '__main__':
    smoke_test()
