SourceTruth API

Pre-indexed canonical and classical text API. Query sacred scripture, philosophy, and classical literature with semantic search and AI-generated answers — no ingestion pipeline required.


TWO ENDPOINTS

POST /v1/search — Semantic + keyword hybrid retrieval. Returns ranked passages with source citations, tradition metadata, and relevance scores. No LLM call. Fast, low-cost. Available on all tiers including free.

POST /v1/answer — Retrieval-augmented generation. Returns a grounded answer with numbered inline citations from the corpus. Powered by Claude. Requires a paid plan.


QUICK START

Send a POST request to https://api.sourcetruth.io/v1/search with header X-API-Key: YOUR_KEY and body: {"query": "What is the nature of suffering?", "top_k": 5}

Each result includes the passage text, canonical reference (e.g. SN 56.11), tradition, language, and relevance score.


AVAILABLE CORPORA

pali-canon — Pali Canon (Tipitaka), SuttaCentral, CC-0

More corpora added weekly. Islamic, Jewish, Christian Biblical, Hindu, Tibetan, Greek, Chinese, and Sanskrit texts coming in Month 1.


AUTHENTICATION

Pass your key in the X-API-Key header. Get a free key instantly via POST /v1/keys — no credit card required.


RATE LIMITS

Free: 100 /search per day, /answer not available, $0
Starter: 5,000 /search per day, 100 /answer per day, $29/mo
Professional: 50,000 /search per day, 300 /answer per day, $149/mo


SUPPORT

api@sourcetruth.io
