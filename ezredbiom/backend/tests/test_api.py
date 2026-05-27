"""Tests for SQLite schema and data integrity."""
import pytest


class TestSchemaIntegrity:
    """Verify database schema is correctly created."""

    def test_tables_exist(self, db_conn):
        """All expected tables exist."""
        tables = [
            "projects", "project_studies", "project_chats", "project_chat_messages",
            "project_context_summaries", "global_chats", "global_chat_messages",
            "study_detail_cache", "chat_pinned_studies", "meta"
        ]
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        existing = {row[0] for row in cursor.fetchall()}
        for table in tables:
            assert table in existing, f"Missing table: {table}"

    def test_indexes_exist(self, db_conn):
        """All expected indexes exist."""
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        existing = {row[0] for row in cursor.fetchall()}
        expected = {
            "idx_projects_user_updated", "idx_project_studies_project",
            "idx_project_studies_study", "idx_project_chats_project_updated",
            "idx_global_chats_user_updated", "idx_chat_pins"
        }
        for idx in expected:
            assert idx in existing, f"Missing index: {idx}"

    def test_foreign_keys_enabled(self, db_conn):
        """Foreign key enforcement is enabled."""
        cursor = db_conn.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1


class TestDataIntegrity:
    """Test data integrity constraints."""

    def test_cascade_delete_project(self, crud, sample_user_id):
        """Deleting project cascades to studies and chats."""
        project = crud.create_project(sample_user_id, "Cascade Test")
        project_id = project["project_id"]

        # Add study
        crud.add_study_to_project(project_id, sample_user_id, {"study_id": 1, "title": "S1"})

        # Add chat
        chat = crud.create_chat(project_id, sample_user_id)
        chat_id = chat["chat_id"]
        crud.append_chat_messages(project_id, sample_user_id, chat_id, "Hi", "Hello")

        # Verify counts
        proj = crud.get_project(project_id, sample_user_id)
        assert len(proj["studies"]) == 1
        assert len(proj["chats"]) == 1

        # Delete project
        crud.delete_project(project_id, sample_user_id)

        # Verify everything gone
        cursor = crud._conn().__enter__().execute(
            "SELECT COUNT(*) FROM project_studies WHERE project_id = ?", (project_id,)
        )
        assert cursor.fetchone()[0] == 0

    def test_unique_project_study(self, crud, sample_user_id):
        """Same study cannot be added twice to same project."""
        project = crud.create_project(sample_user_id, "Unique Test")
        project_id = project["project_id"]

        study = {"study_id": 42, "title": "Unique Study"}
        crud.add_study_to_project(project_id, sample_user_id, study)
        crud.add_study_to_project(project_id, sample_user_id, study)

        proj = crud.get_project(project_id, sample_user_id)
        count = sum(1 for s in proj["studies"] if s["study_id"] == 42)
        assert count == 1

    def test_utc_timestamps(self, crud, sample_user_id):
        """Timestamps are stored in UTC ISO format."""
        project = crud.create_project(sample_user_id, "Time Test")
        assert project["created_at"].endswith("Z")
        assert project["updated_at"].endswith("Z")