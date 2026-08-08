"""E2E tests for the project journey: create a project, add a study to it
(no-refresh — the response body is the full updated project), remove a
study, delete the project.
"""
import pytest


@pytest.mark.e2e
class TestProjectLifecycle:
    def test_create_project_starts_empty(self, client, run_prefix):
        r = client.post("/api/projects", json={"name": f"{run_prefix} lifecycle"})
        assert r.status_code == 200
        proj = r.json()
        assert proj["name"] == f"{run_prefix} lifecycle"
        assert proj["studies"] == []
        assert proj["chats"] == []

        client.delete(f"/api/projects/{proj['project_id']}")

    def test_project_appears_in_list(self, client, project):
        r = client.get("/api/projects")
        assert r.status_code == 200
        ids = {p["project_id"] for p in r.json()["projects"]}
        assert project["project_id"] in ids

    def test_delete_project_removes_it(self, client, run_prefix):
        r = client.post("/api/projects", json={"name": f"{run_prefix} to-delete"})
        proj = r.json()

        r = client.delete(f"/api/projects/{proj['project_id']}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r = client.get(f"/api/projects/{proj['project_id']}")
        assert r.status_code == 404


@pytest.mark.e2e
class TestStudiesInProject:
    """The no-refresh pattern: add/remove-study responses ARE the full
    updated project — the client never needs a follow-up GET to see the
    study land (store/crud.py:226,247)."""

    def test_add_study_appears_in_response_immediately(self, client, project, public_study_ids):
        study_id = public_study_ids[0]
        r = client.post(
            f"/api/projects/{project['project_id']}/studies",
            json={"study": {"study_id": study_id}},
        )
        assert r.status_code == 200
        proj = r.json()
        ids_in_project = {s["study_id"] for s in proj["studies"]}
        assert study_id in ids_in_project, (
            f"Study {study_id} missing from add-study response: {proj['studies']}"
        )

    def test_added_study_persists_on_get(self, client, project, public_study_ids):
        study_id = public_study_ids[0]
        client.post(
            f"/api/projects/{project['project_id']}/studies",
            json={"study": {"study_id": study_id}},
        )
        r = client.get(f"/api/projects/{project['project_id']}")
        ids_in_project = {s["study_id"] for s in r.json()["studies"]}
        assert study_id in ids_in_project

    def test_add_study_missing_study_id_is_rejected(self, client, project):
        r = client.post(
            f"/api/projects/{project['project_id']}/studies",
            json={"study": {}},
        )
        assert r.status_code == 400

    def test_remove_study_drops_it_from_response(self, client, project, public_study_ids):
        study_id = public_study_ids[0]
        client.post(
            f"/api/projects/{project['project_id']}/studies",
            json={"study": {"study_id": study_id}},
        )
        r = client.delete(f"/api/projects/{project['project_id']}/studies/{study_id}")
        assert r.status_code == 200
        proj = r.json()
        ids_in_project = {s["study_id"] for s in proj["studies"]}
        assert study_id not in ids_in_project

    def test_enrich_all_populates_sample_counts(self, client, project, public_study_ids):
        """num_samples/data_types are filled asynchronously after add (a
        background thread — project_routes.py:26-70,118); enrich-all blocks
        until that's done (up to 30s/study) so a test can assert on it."""
        study_id = public_study_ids[0]
        client.post(
            f"/api/projects/{project['project_id']}/studies",
            json={"study": {"study_id": study_id}},
        )
        r = client.post(f"/api/projects/{project['project_id']}/studies/enrich-all")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        enriched = {s["study_id"]: s for s in body["project"]["studies"]}
        assert study_id in enriched
        assert (enriched[study_id].get("num_samples") or 0) > 0, (
            f"Expected enrich-all to populate num_samples for {study_id}: {enriched[study_id]}"
        )
