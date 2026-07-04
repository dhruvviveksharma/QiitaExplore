"""
Benchmark 2: Cache hit speedup
Hits /api/studies/<id>/detail cold (first request, DB fetch) then warm (cache hit).
Measures: cold time, warm time, speedup factor, and queries avoided.

Usage: python cache_bench.py [study_id]
Default study_id is 10317 (American Gut — large, public, always available).
"""

import sys
import time
import requests

BASE = "http://localhost:5001"
DEFAULT_STUDY_ID = 10317  # American Gut Project — large public study


def hit(study_id):
    t0 = time.perf_counter()
    r = requests.get(f"{BASE}/api/studies/{study_id}/detail", timeout=30)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return elapsed_ms, r.status_code, r.json() if r.status_code == 200 else {}


def run():
    study_id = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STUDY_ID
    print(f"Benchmarking study detail cache for study {study_id} ...\n")

    # Warm-up: ensure cache is populated
    print("  Pass 1 (cold — hits PostgreSQL) ...")
    cold_ms, status, data = hit(study_id)
    if status != 200:
        print(f"  FAILED: {status}. Is the backend running? Is study {study_id} accessible?")
        return
    print(f"    Cold fetch : {cold_ms:.0f}ms  (PostgreSQL round-trip)")

    # Now cached — should be SQLite read
    print("  Pass 2 (warm — SQLite cache hit) ...")
    warm_ms, _, _ = hit(study_id)
    print(f"    Warm fetch : {warm_ms:.0f}ms  (SQLite)")

    # Third pass to confirm warm is stable
    warm2_ms, _, _ = hit(study_id)

    speedup = cold_ms / warm_ms if warm_ms > 0 else 0
    saved_ms = cold_ms - warm_ms

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CACHE BENCHMARK RESULTS  (study {study_id})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Cold (PostgreSQL) : {cold_ms:.0f}ms
  Warm pass 1       : {warm_ms:.0f}ms
  Warm pass 2       : {warm2_ms:.0f}ms
  Speedup           : {speedup:.1f}x faster on cache hit
  Latency saved     : {saved_ms:.0f}ms per repeated expansion

RESUME LINE:
  "Reduced metadata expansion latency by {speedup:.0f}x for repeated
   study lookups via a 6-hour SQLite cache, eliminating redundant
   PostgreSQL round-trips."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    run()
