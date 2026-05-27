"""Tests for project studies functionality."""
import pytest


class TestProjectStudies:
    """Test adding/removing studies from projects."""

    def test_add_study_to_project(self, crud, sample_user_id, sample_study):
        """Add a study to a project."""
        created = crud.create_project(sample_user_id, "Study Container")
        project_id = created["project_id"]

        proj = crud.add_study_to_project(project_id, sample_user_id, sample_study)
        assert proj is not None
        study_ids = [s["study_id"] for s in proj["studies"]]
        assert sample_study["study_id"] in study_ids

    def test_remove_study_from_project(self, crud, sample_user_id, sample_study):
        """Remove a study from a project."""
        created = crud.create_project(sample_user_id, "Study Container")
        project_id = created["project_id"]

        # Add study
        crud.add_study_to_project(project_id, sample_user_id, sample_study)

        # Remove study
        proj = crud.remove_study_from_project(project_id, sample_user_id, sample_study["study_id"])
        assert proj is not None
        study_ids = [s["study_id"] for s in proj["studies"]]
        assert sample_study["study_id"] not in study_ids

    def test_add_duplicate_study_no_duplicates(self, crud, sample_user_id, sample_study):
        """Adding same study twice doesn't create duplicates."""
        created = crud.create_project(sample_user_id, "Study Container")
        project_id = created["project_id"]

        crud.add_study_to_project(project_id, sample_user_id, sample_study)
        crud.add_study_to_project(project_id, sample_user_id, sample_study)

        proj = crud.get_project(project_id, sample_user_id)
        study_ids = [s["study_id"] for s in proj["studies"]]
        assert study_ids.count(sample_study["study_id"]) == 1

    def test_add_multiple_studies(self, crud, sample_user_id):
        """Add multiple different studies to a project."""
        created = crud.create_project(sample_user_id, "Multi Study")
        project_id = created["project_id"]

        studies = [
            {"study_id": 1, "study_title": "Study 1"},
            {"study_id": 2, "study_title": "Study 2"},
            {"study_id": 3, "study_title": "Study 3"},
        ]

        for study in studies:
            crud.add_study_to_project(project_id, sample_user_id, study)

        proj = crud.get_project(project_id, sample_user_id)
        assert len(proj["studies"]) == 3

    def test_study_persists_after_reload(self, crud, sample_user_id, sample_study):
        """Study remains after re-fetching project."""
        created = crud.create_project(sample_user_id, "Persistent")
        project_id = created["project_id"]

        crud.add_study_to_project(project_id, sample_user_id, sample_study)

        # Fetch again
        proj = crud.get_project(project_id, sample_user_id)
        study_ids = [s["study_id"] for s in proj["studies"]]
        assert sample_study["study_id"] in study_ids

    def test_remove_nonexistent_study(self, crud, sample_user_id):
        """Removing non-existent study returns project unchanged."""
        created = crud.create_project(sample_user_id, "Test")
        project_id = created["project_id"]

        proj = crud.remove_study_from_project(project_id, sample_user_id, 99999)
        assert proj is not None
        assert len(proj["studies"]) == 0