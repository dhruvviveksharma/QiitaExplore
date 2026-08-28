"""Pytest fixtures for qiita_explore tests."""
import pytest
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

_FAKE_QIITA_DB_STUBBED = False


def stub_qiita_db_and_core():
    """qiita_db/qiita_core are vendored classic-Qiita packages this sandbox
    can't resolve. Stub them so importing `run` (or anything reaching
    helpers/pg_pool.py) succeeds; tests that use this never touch those paths."""
    global _FAKE_QIITA_DB_STUBBED
    if _FAKE_QIITA_DB_STUBBED:
        return
    import importlib.metadata
    import types

    class _FakeTRN:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def add(self, *a, **k): pass
        def execute_fetchindex(self): return []

    fake_sql_connection = types.ModuleType("qiita_db.sql_connection")
    fake_sql_connection.TRN = _FakeTRN()
    fake_qiita_db = types.ModuleType("qiita_db")
    fake_qiita_db.sql_connection = fake_sql_connection
    sys.modules["qiita_db"] = fake_qiita_db
    sys.modules["qiita_db.sql_connection"] = fake_sql_connection

    class _FakeConfig:
        pass

    fake_qiita_settings = types.ModuleType("qiita_core.qiita_settings")
    fake_qiita_settings.qiita_config = _FakeConfig()
    fake_qiita_core = types.ModuleType("qiita_core")
    fake_qiita_core.qiita_settings = fake_qiita_settings
    sys.modules["qiita_core"] = fake_qiita_core
    sys.modules["qiita_core.qiita_settings"] = fake_qiita_settings

    # Flask 2.2.5's test client reads werkzeug.__version__, removed upstream.
    import werkzeug
    if not hasattr(werkzeug, "__version__"):
        werkzeug.__version__ = importlib.metadata.version("werkzeug")

    _FAKE_QIITA_DB_STUBBED = True


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Each test gets a fresh temporary database.

    Also installs the qiita_db/qiita_core stubs, so any test module can simply
    `import helpers.<mod>` — several fixtures used to repeat that dance.
    """
    stub_qiita_db_and_core()
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("QIITA_EXPERIMENT_DB_PATH", db_path)
    # Force re-bootstrap with new path
    for mod_name in list(sys.modules.keys()):
        if 'sql_store' in mod_name or 'store' in mod_name or mod_name in (
            'helpers.qiita_fetch', 'helpers.agent_tools',
        ):
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
def chat_move():
    """Cross-table chat-move logic — its own module for the same reason as
    global_chat_crud (keeps store/crud.py under the 500-line cap)."""
    import store.chat_move as sql_store_chat_move
    return sql_store_chat_move


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