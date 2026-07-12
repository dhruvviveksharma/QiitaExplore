"""
Benchmark 3: Concurrent user load
Simulates N researchers sending search queries simultaneously.
Measures throughput and p95 latency under concurrent load.

Usage: python concurrent_bench.py [max_users]
Default: tests at 5, 10, 15 concurrent users.
"""

import sys
import time
import statistics
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://localhost:5001"

QUERIES = [
    "gut microbiome",
    "soil bacteria",
    "mouse fecal",
    "human skin IBD",
    "antibiotic resistance",
    "shotgun metagenomics",
    "infant gut development",
    "oral microbiome saliva",
    "ocean sediment bacteria",
    "diet obesity intervention",
    "Clostridium difficile",
    "coral reef microbiome",
    "16S amplicon sequencing",
    "skin wound infection",
    "vaginal microbiome",
]


def single_search(query):
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{BASE}/api/search",
            json={"query": query, "data_types": [], "limit": 8},
            timeout=15,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return elapsed_ms if r.status_code == 200 else None
    except Exception:
        return None


def run_at_concurrency(n):
    queries = (QUERIES * 3)[:n]
    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(single_search, q) for q in queries]
        results = [f.result() for f in as_completed(futures)]

    wall_ms = (time.perf_counter() - t_start) * 1000
    latencies = [r for r in results if r is not None]
    failures = len(results) - len(latencies)

    if not latencies:
        return None

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    return {"n": n, "p50": p50, "p95": p95, "wall_ms": wall_ms, "failures": failures}


def run():
    max_users = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    levels = [u for u in [5, 10, 15] if u <= max_users]

    print(f"Concurrent load test against {BASE}/api/search\n")
    print(f"  {'Users':<8} {'p50':>8} {'p95':>8} {'Wall':>10} {'Failures':>10}")
    print(f"  {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*10}")

    rows = []
    for n in levels:
        result = run_at_concurrency(n)
        if result:
            rows.append(result)
            print(f"  {n:<8} {result['p50']:>7.0f}ms {result['p95']:>7.0f}ms {result['wall_ms']:>9.0f}ms {result['failures']:>10}")

    if not rows:
        print("No results — is the backend running?")
        return

    best = rows[-1]
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONCURRENT LOAD RESULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  At {best['n']} concurrent users:
    p50 latency : {best['p50']:.0f}ms
    p95 latency : {best['p95']:.0f}ms
    failures    : {best['failures']}/{best['n']}

RESUME LINE:
  "Sustained sub-{best['p95']:.0f}ms p95 search latency under {best['n']}
   concurrent researcher sessions via Gunicorn multi-worker architecture
   and bounded JSONB query design."
   ({best['n'] - best['failures']}/{best['n']} requests succeeded at this
    concurrency level — {best['failures']} failed/timed out)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    run()
