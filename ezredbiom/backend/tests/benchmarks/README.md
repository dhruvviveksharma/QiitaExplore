# Benchmarks

Run these with the backend live (`bash ezredbiom/start_barnacle.sh`) to get real numbers for your resume.

## Quick start

```bash
cd ezredbiom/backend/tests/benchmarks
pip install requests   # only dependency

python search_latency.py     # p50/p95 search latency
python cache_bench.py        # cold vs warm metadata expansion
python concurrent_bench.py   # latency under 5/10/15 concurrent users
python cache_hit_rate.py     # % of fetches served from SQLite cache
```

## What each script measures

| Script | Metric | Resume use |
|---|---|---|
| `search_latency.py` | p50, p95 across 15 queries | "sub-Xms median search latency" |
| `cache_bench.py` | cold vs warm speedup (Nx) | "Nx faster on cache hit" |
| `concurrent_bench.py` | p95 under N concurrent users | "handles N users at Xms p95" |
| `cache_hit_rate.py` | % queries from SQLite vs PG | "X% of queries served from cache" |

Each script prints a ready-to-use resume line at the end.
