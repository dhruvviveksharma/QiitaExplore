"""
Benchmark 1: Search latency
Measures p50 and p95 across representative queries.
Run with backend live: python search_latency.py
Prints numbers you can use directly on your resume.
"""

import time
import statistics
import requests

BASE = "http://localhost:5001"

QUERIES = [
    "gut microbiome",
    "soil bacteria",
    "mouse fecal",
    "human skin",
    "ocean sediment",
    "IBD Crohn's",
    "antibiotic resistance",
    "16S amplicon",
    "shotgun metagenomics",
    "coral reef",
    "infant gut",
    "diet intervention",
    "obesity",
    "Clostridium difficile",
    "saliva oral microbiome",
]


def run():
    latencies = []
    failures = 0

    print(f"Running {len(QUERIES)} queries against {BASE}/api/search ...\n")

    for q in QUERIES:
        try:
            t0 = time.perf_counter()
            r = requests.post(
                f"{BASE}/api/search",
                json={"query": q, "data_types": [], "limit": 8},
                timeout=10,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if r.status_code == 200:
                latencies.append(elapsed_ms)
                n = len(r.json().get("studies", []))
                print(f"  {q:<35} {elapsed_ms:6.0f}ms  ({n} results)")
            else:
                failures += 1
                print(f"  {q:<35} FAILED ({r.status_code})")
        except Exception as e:
            failures += 1
            print(f"  {q:<35} ERROR: {e}")

    if not latencies:
        print("\nNo successful queries — is the backend running on port 5001?")
        return

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    mn  = min(latencies)
    mx  = max(latencies)

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SEARCH LATENCY RESULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Queries run : {len(latencies)} succeeded, {failures} failed
  Min         : {mn:.0f}ms
  Median (p50): {p50:.0f}ms   ← use this on your resume
  p95         : {p95:.0f}ms
  Max         : {mx:.0f}ms

RESUME LINE (fill in your p50):
  "Delivered sub-{p50:.0f}ms median search latency across 600+ Qiita
   studies by implementing bounded JSONB queries and relevance ranking."
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    run()
