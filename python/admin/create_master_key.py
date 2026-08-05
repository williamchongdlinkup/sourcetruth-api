"""
Create a RapidAPI master key in the production database.

All RapidAPI traffic proxies through a single master key — RapidAPI handles
per-user metering and billing on their side. The master key has effectively
unlimited daily limits so it never hits the quota ceiling.

Run from the api/python directory:
  python admin/create_master_key.py
"""

import hashlib
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / '.env')

import psycopg2
import psycopg2.extras

KEY_PREFIX   = os.getenv('API_KEY_PREFIX', 'st_')
DATABASE_URL = os.environ['DATABASE_URL']

raw_key  = KEY_PREFIX + secrets.token_hex(32)
key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
conn.autocommit = True

with conn.cursor() as cur:
    cur.execute(
        """INSERT INTO api_keys
               (key_hash, name, email, tier, daily_limit, answer_daily_limit)
           VALUES (%s, 'RapidAPI Master', 'api@sourcetruth.io',
                   'professional', 999999, 9999)
           RETURNING id""",
        (key_hash,),
    )
    row = cur.fetchone()

conn.close()

print(f"\nRapidAPI Master Key created (DB id={row['id']}):")
print(f"\n    {raw_key}\n")
print("Next steps:")
print("  1. Copy the key above.")
print("  2. RapidAPI Dashboard → your API → Settings → Security")
print("     Proxy Secret Header — add a 'Required Header':")
print("       Header name:  X-API-Key")
print("       Header value: <paste the key>")
print("  3. Save. All RapidAPI requests will now include the key automatically.")
print("  4. Test with a curl through the RapidAPI proxy to confirm 200.")
