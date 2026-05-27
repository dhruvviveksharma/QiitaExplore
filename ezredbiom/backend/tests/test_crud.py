"""Tests for CRUD operations on projects."""
import pytest


class TestProjectCRUD:
    """Test project create, read, update, delete."""

    def test_create_project(self, crud, sample_user_id):
        """Create a new project and verify it exists."""
        proj = crud.create_project(sample_user_id, "My Test Project")
        assert proj is not None
        assert proj["name"] == "My Test Project"
        assert proj["user_id"] == sample_user_id
        assert "project_id" in proj

    def test_list_projects_empty(self, crud, sample_user_id):
        """List projects for new user returns empty list."""
        projects = crud.list_projects(sample_user_id)
        assert projects == []

    def test_list_projects_after_create(self, crud, sample_user_id):
        """List projects returns created project."""
        crud.create_project(sample_user_id, "Project One")
        crud.create_project(sample_user_id, "Project Two")

        projects = crud.list_projects(sample_user_id)
        assert len(projects) == 2
        # Most recently updated first
        assert projects[0]["name"] == "Project Two"

    def test_get_single_project(self, crud, sample_user_id):
        """Get a single project by ID."""
        created = crud.create_project(sample_user_id, "Single Project")
        project_id = created["project_id"]

        proj = crud.get_project(project_id, sample_user_id)
        assert proj is not None
        assert proj["name"] == "Single Project"
        assert proj["project_id"] == project_id
        assert "studies" in proj
        assert "chats" in proj

    def test_get_nonexistent_project(self, crud, sample_user_id):
        """Get non-existent project returns None."""
        proj = crud.get_project("fake-id-123", sample_user_id)
        assert proj is None

    def test_update_project_name(self, crud, sample_user_id):
        """Update project name."""
        created = crud.create_project(sample_user_id, "Old Name")
        project_id = created["project_id"]

        proj = crud.update_project(project_id, sample_user_id, name="New Name")
        assert proj is not None
        assert proj["name"] == "New Name"

    def test_delete_project(self, crud, sample_user_id):
        """Delete a project."""
        created = crud.create_project(sample_user_id, "To Delete")
        project_id = created["project_id"]

        result = crud.delete_project(project_id, sample_user_id)
        assert result is True

        # Verify deleted
        proj = crud.get_project(project_id, sample_user_id)
        assert proj is None

    def test_projects_isolated_by_user(self, crud, sample_user_id):
        """Projects are isolated by user_id."""
        crud.create_project(sample_user_id, "User A Project")
        crud.create_project("other_user", "User B Project")

        projects = crud.list_projects(sample_user_id)
        assert len(projects) == 1
        assert projects[0]["name"] == "User A Project"

    def test_project_study_count(self, crud, sample_user_id):
        """Project includes study count via studies_count field."""
        created = crud.create_project(sample_user_id, "Counted Project")
        project_id = created["project_id"]

        projects = crud.list_projects(sample_user_id)
        assert projects[0]["studies_count"] == 0

        # Add a study directly to test the count
        crud.add_study_to_project(project_id, sample_user_id, {"study_id": 999, "title": "Test"})

        projects = crud.list_projects(sample_user_id)
        assert projects[0]["studies_count"] == 1