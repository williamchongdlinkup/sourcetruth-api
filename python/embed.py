"""
Voyage AI multilingual embeddings (voyage-multilingual-2, 1024 dim).

Handles Pali (romanised), Classical Chinese, Tibetan, Sanskrit, English
in a single model — the key requirement for cross-tradition retrieval.
"""

import os
from functools import lru_cache

import voyageai
from dotenv import load_dotenv

load_dotenv()

MODEL = 'voyage-multilingual-2'
DIMENSIONS = 1024
MAX_TEXTS_PER_CALL = 30       # conservative: Sanskrit chunks can be 1000+ Voyage tokens each
MAX_TOKENS_PER_CALL = 80_000  # hard stop well under Voyage's 120K limit


@lru_cache(maxsize=1)
def _client() -> voyageai.Client:
    return voyageai.Client(api_key=os.environ['VOYAGE_API_KEY'])


def _estimate_tokens(text: str) -> int:
    # UTF-8 byte length ÷ 3 is a better proxy than char ÷ 4 for multilingual text:
    # IAST Sanskrit diacriticals encode as 2 bytes each, inflating real token counts
    # vs the English assumption of ~4 chars/token.
    return max(1, len(text.encode('utf-8')) // 3)


def embed_query(text: str) -> list[float]:
    """Embed a single query string (input_type='query' for retrieval optimisation)."""
    result = _client().embed([text], model=MODEL, input_type='query')
    return result.embeddings[0]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed a batch of document passages (input_type='document').

    Batches by both item count (≤128) and estimated token count (≤100K) so
    Sanskrit and Chinese corpora — which are more token-dense than English —
    don't breach Voyage's 120K-token-per-call limit.
    """
    all_embeddings: list[list[float]] = []
    batch: list[str] = []
    batch_tokens = 0

    for text in texts:
        t = _estimate_tokens(text)
        if batch and (len(batch) >= MAX_TEXTS_PER_CALL or batch_tokens + t > MAX_TOKENS_PER_CALL):
            result = _client().embed(batch, model=MODEL, input_type='document')
            all_embeddings.extend(result.embeddings)
            batch = []
            batch_tokens = 0
        batch.append(text)
        batch_tokens += t

    if batch:
        result = _client().embed(batch, model=MODEL, input_type='document')
        all_embeddings.extend(result.embeddings)

    return all_embeddings
