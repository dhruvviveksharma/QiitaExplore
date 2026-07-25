"""Pytest fixtures for qiita_explore tests."""
import pytest
import sys
import os
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))


@pytest.fixture(autouse=True)
def isolate_dotenv(monkeypatch):
    """config.py calls load_dotenv() at import, so whatever sits in
    qiita_explore/.env leaks into every test process. QIITA_EXPLORE_ALLOWED_ORIGINS
    is the one that bites: with it set, _origin_allowed() demands an Origin header
    the Flask test client never sends, and POST /api/auth/connect 403s — which
    fails every test that logs a principal in. Neutralise it so results depend on
    the code under test, not on the developer's local .env."""
    import config
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", [], raising=False)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Each test gets a fresh temporary database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("QIITA_EXPERIMENT_DB_PATH", db_path)
    # Force re-bootstrap with new path
    for mod_name in list(sys.modules.keys()):
        if 'sql_store' in mod_name or 'store' in mod_name:
            del sys.modules[mod_name]

    # Re-import to get fresh schema
    import store.db as sql_store_db
    # Verify schema created
    with sql_store_db._conn() as conn:
        conn.execute("SELECT 1 FROM projects LIMIT 1")

    return db_path


@pytest.fixture
def db_conn(fresh_db):
    """Direct database connection for raw queries."""
    import store.db as sql_store_db
    return sql_store_db._conn()


@pytest.fixture
def crud():
    """Import CRUD module after fresh_db fixture sets up isolated DB."""
    import store.crud as sql_store_crud
    return sql_store_crud


@pytest.fixture
def global_chat_crud():
    """Global-chat CRUD lives in its own module (split out of store/crud.py
    to keep it under the 500-line cap)."""
    import store.global_chat_crud as sql_store_global_chat_crud
    return sql_store_global_chat_crud


@pytest.fixture
def sample_user_id():
    return "test_user_001"


@pytest.fixture
def sample_study():
    return {
        "study_id": 12345,
        "study_title": "Test Study on Microbiome",
        "metadata_complete": True,
        "num_samples": 50,
        "num_preps": 2,
        "data_types": "16S",
        "study_alias": "test_alias",
        "study_abstract": "A test study abstract",
        "pi_name": "Dr. Test"
    }