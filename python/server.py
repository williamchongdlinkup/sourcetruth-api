"""
Corpus API — FastAPI server.

Endpoints:
  POST /v1/search        — hybrid passage retrieval (all tiers including free)
  POST /v1/answer        — grounded Q&A with citations (paid tiers only)
  GET  /v1/text/{uid}    — full document text by external ID
  GET  /v1/entity/{id}   — entity metadata
  POST /v1/keys          — self-issue a free-tier API key
  GET  /health

Run:
  uvicorn server:app --port 8001 --reload

Fixes over CanonRAG prototype
  Fix 1 — ThreadedConnectionPool via get_primary_conn(): auth/logging/entity
           queries are now thread-safe under concurrent load.
  Fix 2 — BackgroundTasks for _log_usage: usage logging no longer adds
           latency to responses; it runs after the response is sent.
  Fix 3 — daily_quota table with atomic upsert: quota check is an O(1)
           indexed lookup instead of a growing COUNT(*) view scan.
  Fix 4 — /v1/answer open to paid API keys; internal-secret gate removed;
           free tier gets a 403 with upgrade prompt.
"""

import hashlib
import os
import re
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote as urlquote

import anthropic
import stripe
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from db import get_conn, get_primary_conn, init_primary_pool

_conns  = []    # long-lived per-account search connections (managed by MultiAccountSearch)
_search = None
_ner    = None
_claude = None

KEY_PREFIX = os.getenv('API_KEY_PREFIX', 'st_')

TIER_LIMITS = {
    'free':         {'daily_limit': 100,    'answer_daily_limit': None},
    'starter':      {'daily_limit': 1_000,  'answer_daily_limit': 100},
    'professional': {'daily_limit': 10_000, 'answer_daily_limit': 300},
}

stripe.api_key = os.getenv('STRIPE_SECRET_KEY', '')
_PRICE_TO_TIER = {
    price_id: tier
    for tier, price_id in {
        'starter':      os.getenv('STRIPE_STARTER_PRICE_ID', ''),
        'professional': os.getenv('STRIPE_PROFESSIONAL_PRICE_ID', ''),
    }.items()
    if price_id
}

SYSTEM_PROMPT = (
    "You are a scholarly reference assistant specialising in canonical and classical texts. "
    "Your role is to synthesise retrieved passages into clear, grounded answers with full citations.\n\n"
    "Rules:\n"
    "- Every factual claim must be attributed to a specific retrieved passage.\n"
    "- Never invent references, text identifiers, or doctrinal statements from memory.\n"
    "- If the retrieved passages do not answer the question, say so explicitly.\n"
    "- Use the tradition and language metadata to contextualise your answer.\n"
    "- End every answer with a structured citations section listing each source "
    "by its bracketed number.\n"
    "- Keep answers concise and scholarly in register.\n"
    "- Do not offer personal spiritual advice — only explain what the texts say."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _conns, _search, _ner, _claude

    from normalisation.entity_resolver import EntityResolver
    from normalisation.ner import BuddhistNER
    from search.multi_search import MultiAccountSearch

    # Fix 1: primary pool for auth / logging / entity queries
    init_primary_pool(os.environ['DATABASE_URL'], minconn=2, maxconn=20)

    # Long-lived search connections (one per corpus account)
    primary_conn = get_conn()
    _conns = [primary_conn]
    accounts = [(1, primary_conn, os.environ['DATABASE_URL'])]

    for acc_num, url_var in [(2, 'DATABASE_URL_2'), (3, 'DATABASE_URL_3')]:
        url = os.environ.get(url_var)
        if url:
            try:
                c = get_conn(url)
                _conns.append(c)
                accounts.append((acc_num, c, url))
                print(f'  Corpus account {acc_num} connected ({url_var})')
            except Exception as e:
                print(f'  Corpus account {acc_num} unavailable ({url_var}): {e}')

    _search = MultiAccountSearch(accounts)
    _ner    = BuddhistNER(EntityResolver(primary_conn))
    _claude = anthropic.Anthropic()

    print(f'Corpus API ready — {len(accounts)} corpus account(s) online.')
    yield

    for c in _conns:
        try:
            c.close()
        except Exception:
            pass


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title='Corpus API',
    description='Pre-indexed canonical and classical text API — semantic search and grounded Q&A',
    version='1.0.0',
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv('ALLOWED_ORIGINS', 'http://localhost:3000').split(','),
    allow_methods=['GET', 'POST'],
    allow_headers=['*'],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cbeta_deep_url(external_id: str, lb_ref: str, chunk_text: str) -> str:
    stem    = re.sub(r'[a-z]$', '', external_id)
    snippet = urlquote(chunk_text[:40], safe='')
    return f'https://cbetaonline.dila.edu.tw/zh/{stem}_p{lb_ref}?q={snippet}'


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _check_api_key(key: str) -> dict:
    """
    Validate an API key. Returns the key record on success.
    Raises 401 (invalid/inactive) or 429 (quota exhausted).

    Fix 1: uses get_primary_conn() — thread-safe pool checkout/return.
    Fix 3: reads daily_quota (O(1) index lookup) instead of api_usage_today view.
    """
    key_hash = _hash_key(key.strip())
    with get_primary_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM api_keys WHERE key_hash = %s AND is_active = TRUE",
                (key_hash,),
            )
            record = cur.fetchone()
        if not record:
            raise HTTPException(status_code=401, detail='Invalid or inactive API key.')

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count FROM daily_quota WHERE key_id = %s AND date = CURRENT_DATE",
                (record['id'],),
            )
            quota = cur.fetchone()
        if (quota['count'] if quota else 0) >= record['daily_limit']:
            raise HTTPException(status_code=429, detail='Daily request limit reached.')

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET last_used = NOW() WHERE id = %s",
                (record['id'],),
            )
    return dict(record)


def _log_usage(
    key_id: int,
    endpoint: str,
    query: str,
    traditions: list,
    languages: list,
    count: int,
    latency_ms: int,
) -> None:
    """
    Record a usage event and atomically increment the daily quota counter.

    Fix 2: called via BackgroundTasks — runs after response is sent, adds no latency.
    Fix 3: upserts daily_quota with ON CONFLICT — single atomic increment.
    Failures are printed (not raised) so a logging error never surfaces to the caller.
    """
    try:
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO api_usage
                           (key_id, endpoint, query_text, traditions, languages,
                            results_count, latency_ms)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (key_id, endpoint, query[:500], traditions, languages, count, latency_ms),
                )
                cur.execute(
                    """INSERT INTO daily_quota (key_id, date, count)
                       VALUES (%s, CURRENT_DATE, 1)
                       ON CONFLICT (key_id, date)
                       DO UPDATE SET count = daily_quota.count + 1""",
                    (key_id,),
                )
    except Exception as e:
        print(f'[log_usage] Failed for key_id={key_id}: {e}')


def _check_answer_quota(key_record: dict) -> None:
    """Raise 403 if no /answer access, 429 if daily /answer limit is exhausted."""
    limit = key_record.get('answer_daily_limit')
    if limit is None:
        raise HTTPException(
            status_code=403,
            detail='The /answer endpoint requires a paid plan (starter or professional).',
        )
    with get_primary_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count FROM answer_daily_quota WHERE key_id = %s AND date = CURRENT_DATE",
                (key_record['id'],),
            )
            row = cur.fetchone()
    if (row['count'] if row else 0) >= limit:
        raise HTTPException(status_code=429, detail='Daily /answer limit reached.')


def _log_answer_usage(key_id: int) -> None:
    """Atomically increment the /answer daily quota counter (background task)."""
    try:
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO answer_daily_quota (key_id, date, count)
                       VALUES (%s, CURRENT_DATE, 1)
                       ON CONFLICT (key_id, date)
                       DO UPDATE SET count = answer_daily_quota.count + 1""",
                    (key_id,),
                )
    except Exception as e:
        print(f'[log_answer_usage] Failed for key_id={key_id}: {e}')


def _get_key_by_raw(raw_key: str) -> dict:
    """Look up a key record without quota check (used for billing operations)."""
    key_hash = _hash_key(raw_key.strip())
    with get_primary_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM api_keys WHERE key_hash = %s AND is_active = TRUE",
                (key_hash,),
            )
            record = cur.fetchone()
    if not record:
        raise HTTPException(status_code=401, detail='Invalid or inactive API key.')
    return dict(record)


def get_key_record(x_api_key: str = Header(default='')) -> dict | None:
    """FastAPI dependency: returns key record if header present, None for anonymous."""
    if not x_api_key:
        return None
    return _check_api_key(x_api_key)


# ── Request / response models ─────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    corpus_codes: Optional[list[str]] = None   # most precise: e.g. ["sahih-bukhari", "quran"]
    traditions:   Optional[list[str]] = None   # e.g. ["theravada", "islam"]
    languages:    Optional[list[str]] = None   # ISO: ["en", "pi", "ar"]
    collections:  Optional[list[str]] = None
    top_k: int = 8


class AnswerRequest(BaseModel):
    question: str
    corpus_codes: Optional[list[str]] = None
    traditions:   Optional[list[str]] = None
    languages:    Optional[list[str]] = None
    max_passages: int = 6


class KeyRequest(BaseModel):
    name: str
    email: str


class CheckoutRequest(BaseModel):
    api_key:     str
    price_id:    str
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post('/v1/search')
@limiter.limit('60/minute')
def search(
    req: SearchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    key_record: dict | None = Depends(get_key_record),
):
    """Semantic + keyword hybrid search across indexed corpora. Available to all tiers."""
    import time
    if not req.query.strip():
        raise HTTPException(status_code=400, detail='Query cannot be empty.')

    t0 = time.monotonic()
    entity_ids = _ner.extract_entity_ids(req.query)

    results = _search.search(
        query=req.query,
        entity_ids=entity_ids,
        corpus_codes=req.corpus_codes,
        traditions=req.traditions,
        languages=req.languages,
        collections=req.collections,
        top_k=req.top_k,
        rerank='auto',
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    # Entity resolution — batched under a single pool checkout
    resolved_entities = []
    if entity_ids:
        with get_primary_conn() as conn:
            for eid in entity_ids:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM buddhist_entities WHERE id = %s", (eid,))
                    e = cur.fetchone()
                if e:
                    resolved_entities.append({
                        'id':       eid,
                        'english':  e['english_preferred'],
                        'pali':     e.get('pali'),
                        'sanskrit': e.get('sanskrit'),
                        'chinese':  e.get('classical_chinese'),
                        'tibetan':  e.get('tibetan'),
                        'type':     e['entity_type'],
                    })

    passages = []
    for r in results:
        lb_ref = r.get('section') or ''
        url = (
            _cbeta_deep_url(r['external_id'], lb_ref, r['chunk_text'])
            if r['corpus_code'] == 'cbeta' and lb_ref
            else r.get('url')
        )
        passages.append({
            'chunk_id':      r['id'],
            'text':          r['chunk_text'],
            'account':       r.get('account', 1),
            'reference':     r['reference'],
            'sutta_id':      r['external_id'],
            'title_english': r.get('title_english'),
            'title_pali':    r.get('title_pali'),
            'tradition':     r['tradition'],
            'language':      r['language'],
            'collection':    r['collection'],
            'translator':    r.get('translator'),
            'corpus':        r['corpus_code'],
            'url':           url,
            'score':         r['score'],
        })

    # Fix 2: log after response is sent
    if key_record:
        background_tasks.add_task(
            _log_usage, key_record['id'], '/v1/search', req.query,
            req.traditions or [], req.languages or [],
            len(passages), latency_ms,
        )

    return {
        'query':             req.query,
        'resolved_entities': resolved_entities,
        'passages':          passages,
        'search_accounts':   len(_conns),
        'latency_ms':        latency_ms,
    }


@app.post('/v1/answer')
@limiter.limit('20/minute')
def answer(
    req: AnswerRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    key_record: dict | None = Depends(get_key_record),
):
    """
    Grounded Q&A: retrieves passages then generates a cited answer via Claude.

    Fix 4: open to paid API keys (starter / professional).
           Free tier receives 403 with upgrade message.
           Internal-secret gate from CanonRAG prototype removed.
    """
    import time

    if not key_record:
        raise HTTPException(
            status_code=401,
            detail='An API key is required for /v1/answer.',
        )
    _check_answer_quota(key_record)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail='Question cannot be empty.')

    t0 = time.monotonic()
    entity_ids = _ner.extract_entity_ids(req.question)

    passages = _search.search(
        query=req.question,
        entity_ids=entity_ids,
        corpus_codes=req.corpus_codes,
        traditions=req.traditions,
        languages=req.languages,
        top_k=req.max_passages,
        rerank='auto',
    )

    if not passages:
        return {
            'question':      req.question,
            'answer':        'No relevant passages were found in the indexed corpus for this question.',
            'citations':     [],
            'passages_used': [],
            'latency_ms':    0,
        }

    context_blocks = []
    for i, p in enumerate(passages, 1):
        ref  = p.get('reference') or p.get('external_id', '')
        src  = p.get('corpus_code', '')
        trad = p.get('tradition', '')
        lang = p.get('language', '')
        context_blocks.append(
            f"[{i}] {p.get('external_id', '')} · {ref} · {src} ({trad}, {lang})\n{p['chunk_text']}"
        )
    context = '\n\n---\n\n'.join(context_blocks)

    response = _claude.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1024,
        system=[{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}],
        messages=[{
            'role': 'user',
            'content': (
                f"Question: {req.question}\n\n"
                f"Retrieved passages:\n\n{context}\n\n"
                "Answer the question by synthesising the relevant passages above, "
                "citing each source by its bracketed number."
            ),
        }],
    )

    answer_text = response.content[0].text
    latency_ms  = int((time.monotonic() - t0) * 1000)

    citations = []
    for i, p in enumerate(passages):
        lb_ref = p.get('section') or ''
        url = (
            _cbeta_deep_url(p['external_id'], lb_ref, p['chunk_text'])
            if p.get('corpus_code') == 'cbeta' and lb_ref
            else p.get('url')
        )
        citations.append({
            'index':         i + 1,
            'sutta_id':      p.get('external_id'),
            'reference':     p.get('reference'),
            'title_english': p.get('title_english'),
            'translator':    p.get('translator'),
            'corpus':        p.get('corpus_code'),
            'url':           url,
            'tradition':     p.get('tradition'),
        })

    # Fix 2: log after response is sent
    background_tasks.add_task(
        _log_usage, key_record['id'], '/v1/answer', req.question,
        req.traditions or [], req.languages or [],
        len(passages), latency_ms,
    )
    background_tasks.add_task(_log_answer_usage, key_record['id'])

    return {
        'question':      req.question,
        'answer':        answer_text,
        'citations':     citations,
        'passages_used': [p['chunk_text'][:200] + '…' for p in passages],
        'latency_ms':    latency_ms,
    }


@app.get('/v1/corpora')
def list_corpora():
    """Return all indexed corpora — use returned codes as corpus_codes filter values."""
    with get_primary_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT code, name, tradition, language, license, base_url
                   FROM source_corpora
                   ORDER BY tradition, code"""
            )
            rows = cur.fetchall()
    return {'corpora': [dict(r) for r in rows]}


@app.get('/v1/text/{uid}')
def get_text(uid: str, lang: str = 'en'):
    """Return the full chunked text of a document by its external ID."""
    with get_primary_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ct.*, sc.code AS corpus_code
                   FROM canon_texts ct
                   JOIN source_corpora sc ON sc.id = ct.corpus_id
                   WHERE ct.external_id = %s AND ct.language = %s""",
                (uid, lang),
            )
            text = cur.fetchone()

        if not text:
            raise HTTPException(status_code=404, detail=f'Text {uid} (lang={lang}) not found.')

        with conn.cursor() as cur:
            cur.execute(
                """SELECT chunk_index, chunk_text, reference, chapter, section
                   FROM document_chunks
                   WHERE text_id = %s
                   ORDER BY chunk_index""",
                (text['id'],),
            )
            chunks = cur.fetchall()

    return {
        'uid':           text['external_id'],
        'title_english': text.get('title_english'),
        'title_pali':    text.get('title_pali'),
        'tradition':     text['tradition'],
        'language':      text['language'],
        'collection':    text['collection'],
        'translator':    text.get('translator'),
        'url':           text.get('url'),
        'corpus':        text['corpus_code'],
        'chunks':        [dict(c) for c in chunks],
    }


@app.get('/v1/entity/{entity_id}')
def get_entity(entity_id: int):
    """Return entity metadata and all name variants for a given entity ID."""
    with get_primary_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM buddhist_entities WHERE id = %s", (entity_id,))
            entity = cur.fetchone()

        if not entity:
            raise HTTPException(status_code=404, detail=f'Entity {entity_id} not found.')

        with conn.cursor() as cur:
            cur.execute(
                """SELECT name_text, language, script, is_primary
                   FROM entity_name_variants
                   WHERE entity_id = %s
                   ORDER BY is_primary DESC, language""",
                (entity_id,),
            )
            variants = cur.fetchall()

    return {
        **dict(entity),
        'name_variants': [dict(v) for v in variants],
    }


@app.post('/v1/keys')
@limiter.limit('5/hour')
def create_key(req: KeyRequest, request: Request):
    """Self-issue a free-tier API key. Upgrade to paid tiers via the dashboard."""
    name  = req.name.strip()
    email = req.email.strip().lower()

    if not name:
        raise HTTPException(status_code=400, detail='Name is required.')
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        raise HTTPException(status_code=400, detail='Valid email address required.')

    raw_key  = KEY_PREFIX + secrets.token_hex(32)
    key_hash = _hash_key(raw_key)

    with get_primary_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO api_keys (key_hash, name, email, tier, daily_limit) "
                    "VALUES (%s, %s, %s, 'free', 100)",
                    (key_hash, name, email),
                )
        except Exception:
            raise HTTPException(status_code=500, detail='Failed to create API key.')

    return {
        'key':         raw_key,
        'tier':        'free',
        'daily_limit': 100,
        'message':     'Store this key safely — it will not be shown again.',
    }


@app.post('/v1/stripe/checkout')
@limiter.limit('5/hour')
def stripe_checkout(req: CheckoutRequest, request: Request):
    """Create a Stripe Checkout session to upgrade an API key to a paid plan."""
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail='Billing not configured.')

    key_record = _get_key_by_raw(req.api_key)
    email = key_record.get('email', '')
    if not email:
        raise HTTPException(status_code=400, detail='API key has no email — cannot create billing account.')

    # Find or create Stripe customer, then persist the customer ID on the key
    customer_id = key_record.get('stripe_customer_id')
    if not customer_id:
        existing = stripe.Customer.list(email=email, limit=1)
        if existing.data:
            customer_id = existing.data[0].id
        else:
            customer = stripe.Customer.create(
                email=email,
                metadata={'key_id': str(key_record['id'])},
            )
            customer_id = customer.id
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE api_keys SET stripe_customer_id = %s WHERE id = %s",
                    (customer_id, key_record['id']),
                )

    app_url = os.getenv('NEXT_PUBLIC_APP_URL', 'https://api.sourcetruth.io')
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode='subscription',
        line_items=[{'price': req.price_id, 'quantity': 1}],
        subscription_data={'metadata': {'key_id': str(key_record['id'])}},
        success_url=req.success_url or f'{app_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=req.cancel_url or f'{app_url}/billing/cancel',
    )
    return {'checkout_url': session.url}


@app.post('/v1/stripe/portal')
@limiter.limit('10/hour')
def stripe_portal(request: Request, key_record: dict | None = Depends(get_key_record)):
    """Create a Stripe Customer Portal session to manage an existing subscription."""
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail='Billing not configured.')
    if not key_record:
        raise HTTPException(status_code=401, detail='An API key is required.')

    customer_id = key_record.get('stripe_customer_id')
    if not customer_id:
        raise HTTPException(status_code=400, detail='No billing account linked to this key.')

    app_url = os.getenv('NEXT_PUBLIC_APP_URL', 'https://api.sourcetruth.io')
    params: dict = {
        'customer':   customer_id,
        'return_url': f'{app_url}/billing',
    }
    portal_config = os.getenv('STRIPE_PORTAL_CONFIG_ID')
    if portal_config:
        params['configuration'] = portal_config

    session = stripe.billing_portal.Session.create(**params)
    return {'portal_url': session.url}


@app.post('/v1/stripe/webhook')
async def stripe_webhook(request: Request):
    """Receive and verify Stripe webhook events for subscription lifecycle management."""
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail='Billing not configured.')

    payload = await request.body()
    sig = request.headers.get('stripe-signature', '')
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, os.environ.get('STRIPE_WEBHOOK_SECRET', '')
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail='Webhook verification failed.')

    etype = event['type']
    obj   = event['data']['object']

    if etype in ('customer.subscription.created', 'customer.subscription.updated'):
        key_id_str = (obj.get('metadata') or {}).get('key_id')
        if not key_id_str:
            return {'status': 'ignored', 'reason': 'no key_id in subscription metadata'}

        price_id = obj['items']['data'][0]['price']['id']
        tier = _PRICE_TO_TIER.get(price_id)
        if not tier:
            return {'status': 'ignored', 'reason': f'unknown price_id {price_id}'}

        limits = TIER_LIMITS[tier]
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE api_keys
                       SET tier = %s,
                           daily_limit = %s,
                           answer_daily_limit = %s,
                           stripe_customer_id = COALESCE(stripe_customer_id, %s)
                       WHERE id = %s""",
                    (tier, limits['daily_limit'], limits['answer_daily_limit'],
                     obj.get('customer'), int(key_id_str)),
                )
        print(f'[stripe_webhook] Upgraded key_id={key_id_str} to {tier}')

    elif etype == 'customer.subscription.deleted':
        key_id_str = (obj.get('metadata') or {}).get('key_id')
        if key_id_str:
            with get_primary_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE api_keys
                           SET tier = 'free',
                               daily_limit = %s,
                               answer_daily_limit = NULL
                           WHERE id = %s""",
                        (TIER_LIMITS['free']['daily_limit'], int(key_id_str)),
                    )
            print(f'[stripe_webhook] Downgraded key_id={key_id_str} to free')

    elif etype == 'invoice.payment_failed':
        print(f"[stripe_webhook] Payment failed for customer={obj.get('customer')}")

    return {'status': 'ok', 'type': etype}


@app.get('/health')
def health():
    return {'status': 'ok', 'version': '1.0.0'}


# Developer documentation — served at /docs-site/
# Configure docs.sourcetruth.io as a CNAME to api.sourcetruth.io with a Cloudflare
# Transform Rule rewriting docs.sourcetruth.io/* → api.sourcetruth.io/docs-site/$1
_docs_dir = Path(__file__).parent / 'docs'
if _docs_dir.exists():
    app.mount('/docs-site', StaticFiles(directory=str(_docs_dir), html=True), name='docs-site')
