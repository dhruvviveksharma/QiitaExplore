# Installation

## Prerequisites

- Python 3.9+ (3.9 recommended for numpy/pandas pins)
- Access to the Qiita PostgreSQL database (read-only)
- Redis (used by qiita_core for config; must be reachable)
- A Qiita config file (sets DB credentials — see below)
- An NRP-Nautilus API key for the LLM endpoint

## Python setup

```bash
# Create and activate a virtual environment (or conda env)
conda create -n qiita-web python=3.9
conda activate qiita-web

# Install all dependencies
pip install -r ezredbiom/requirements.txt
```

## Frontend setup

No build step required. The frontend uses React via Babel standalone loaded from CDN. Just serve the `ezredbiom/frontend/` directory — the Flask dev server does this automatically when you run locally.

## Configuration

### 1. Qiita config file

Set the `QIITA_CONFIG_FP` environment variable to point at your Qiita config file (the one with `[main]`, `[postgres]`, `[redis]` sections). If unset, defaults to `qiita_core/support_files/config_test.cfg`.

```bash
export QIITA_CONFIG_FP="/path/to/your/qiita_config.cfg"
```

### 2. LLM API key

```bash
export OPENAI_API_KEY="your-nrp-nautilus-key"
# or
export API_KEY="your-nrp-nautilus-key"
```

### 3. (Optional) SQLite database path

By default the local SQLite store lives at `ezredbiom/backend/data/projects.db`. Override with:

```bash
export QIITA_EXPERIMENT_DB_PATH="/path/to/projects.db"
```

## Running (development)

```bash
python ezredbiom/backend/run.py
# Starts Flask on http://localhost:5002
```

## Running (production / barnacle)

```bash
bash ezredbiom/start_barnacle.sh
# Starts gunicorn on port 5002, 4 workers, 2 threads each
```

For nginx proxying, update the `root` path in `ezredbiom/nginx.conf` to your absolute path to `ezredbiom/frontend/`.

## Port conventions

| Context | Port |
|---------|------|
| Dev / barnacle testing | 5002 |
| Production (master branch) | 5001 |
