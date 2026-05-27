# ezredbiom Test Suite

Tests core CRUD operations, data integrity, and schema correctness.

## Run Tests

```bash
cd ezredbiom/Experiment/backend
pip install -r tests/requirements.txt
python -m pytest tests/ -v
```

## Test Coverage

| File | Coverage |
|------|----------|
| `test_crud.py` | Project CRUD, user isolation, ordering |
| `test_studies.py` | Add/remove studies, deduplication |
| `test_chats.py` | Chat CRUD, message persistence |
| `test_api.py` | Schema integrity, foreign keys, cascade delete |

## Notes

- Tests use isolated temporary SQLite databases (one per test via `fresh_db` fixture)
- No external dependencies (Qiita DB, LLM) required
- Tests are fast (~0.3s for full suite)