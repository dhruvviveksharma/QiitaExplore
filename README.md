# ezredbiom

A local web frontend for exploring and analyzing microbiome studies from the [Qiita](https://qiita.ucsd.edu/) database.

## What it does

- Browse and search Qiita studies (by data type, sample count, PI, etc.)
- Build personal research projects by collecting studies
- Chat with an LLM (gemma3 via NRP-Nautilus) using your selected studies as context
- Global discovery mode: search the full Qiita DB and chat across all results

## Quick start

See [INSTALL.md](INSTALL.md) for setup instructions.

## Architecture

See the `# Architecture` section in [CLAUDE.md](CLAUDE.md) for a full stack overview.

## Structure

```
ezredbiom/
  backend/     Flask API server
  frontend/    React UI (Babel standalone — no build step)
qiita_db/      Trimmed Qiita DB layer (sql_connection.py only)
qiita_core/    Trimmed Qiita core (config + exceptions only)
```
