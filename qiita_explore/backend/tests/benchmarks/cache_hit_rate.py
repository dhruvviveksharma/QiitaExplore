"""
Benchmark 4: Cache hit rate
Simulates a realistic researcher session — some studies expanded multiple
times (repeated lookups), some expanded once (first-time).
Measures what % of detail fetches are served from the 6h SQLite cache
vs hitting PostgreSQL.

Run AFTER cache_bench.py or a normal session so some studies are cached.
Usage: python cache_hit_rate.py
"""

import requests

BASE = "http://localhost:5001"

# Mix of studies a researcher might re-open during a session.
# Adjust these to study IDs you know exist in your instance.
# Format: (study_id, times_expanded_in_session)
SESSION = [
    (10317, 3),   # researcher returns to American Gut repeatedly
    (2136,  2),   # opened twice
    (11358, 1),   # opened once
    (1841,  2),
    (850,   1),
    (10317, 1),   # same as first — should be cache hit
    (2136,  1),   # repeat
]


def fetch_detail(study_id):
    """Return (ok, cached) using the endpoint's real 'cached' field — not a guess."""
    try:
        r = requests.get(f"{BASE}/api/studies/{study_id}/detail", timeout=20)
        if r.status_code != 200:
            return False, False
        return True, bool(r.json().get("cached"))
    except Exception:
        return False, False


def run():
    total = 0
    hits = 0   # only fetches that both succeeded AND the server reported cached=True
    seen = {}

    print("Simulating researcher session (study detail expansions)...\n")
    print(f"  {'Study ID':<12} {'Access #':<10} {'Source (actual)'}")
    print(f"  {'─'*12} {'─'*10} {'─'*15}")

    for study_id, times in SESSION:
        for i in range(times):
            seen[study_id] = seen.get(study_id, 0) + 1
            access_n = seen[study_id]
            ok, cached = fetch_detail(study_id)
            total += 1
            if ok and cached:
                hits += 1
            source = "SQLite cache" if (ok and cached) else "PostgreSQL"
            status = "OK" if ok else "FAIL"
            print(f"  {study_id:<12} #{access_n:<9} {source}  [{status}]")

    hit_rate = (hits / total * 100) if total > 0 else 0

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CACHE HIT RATE RESULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total fetches   : {total}
  Cache hits      : {hits}  ({hit_rate:.0f}%)   ← from the response's real "cached" field
  PostgreSQL hits : {total - hits}  ({100 - hit_rate:.0f}%)

Note: "cache hit" = the server's own response reported cached=true, verified
per-request — not assumed from access order.

RESUME LINE:
  "Eliminated {hit_rate:.0f}% of redundant PostgreSQL metadata queries
   during active researcher sessions via a 6-hour SQLite detail cache."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    run()
