"""Sample Aggregation SQLite CRUD (store/aggregation_crud): per-row selection, cascades,
the lazy per-sample -> per-row migration and the per-study file filter. Split out of
test_aggregations.py (500-line cap)."""

import pytest  # noqa: F401

from .test_aggregations import STUDY  # noqa: F401


@pytest.fixture
def agg_crud():
    import store.aggregation_crud as m
    return m


# s1 is in two artifacts, s2 in one — three checkable rows.
ROWS = [("s1", 10), ("s1", 11), ("s2", 10)]


def test_store_create_list_get(agg_crud):
    agg = agg_crud.create_aggregation("u1", "A")
    listed = agg_crud.list_aggregations("u1")
    assert [a["aggregation_id"] for a in listed] == [agg["aggregation_id"]]
    assert listed[0]["studies"] == []
    assert agg_crud.get_aggregation(agg["aggregation_id"], "u2") is None
    assert agg_crud.list_aggregations("u2") == []


def test_store_add_readd_remove_study(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg = agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)
    assert len(agg["studies"]) == 1
    s = agg["studies"][0]
    assert (s["study_id"], s["study_title"], s["data_types"], s["num_samples"], s["num_preps"],
            s["fastq_artifact_count"]) == (16326, "Test", "16S", 2, 1, 2)
    # header snapshot for the tab's cards
    assert (s["study_abstract"], s["pi_name"], s["pi_affiliation"], s["year"], s["is_gold"]) == \
        ("About soil", "Rob Knight", "UCSD", 2015, 1)
    assert (s["selected_rows"], s["file_rows"], s["rows_v"]) == (3, 3, 1)
    assert len(agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)["studies"]) == 1
    assert agg_crud.add_study_to_aggregation(aid, "u2", STUDY, 2, ROWS) is None
    assert agg_crud.remove_study_from_aggregation(aid, "u2", 16326) is None
    assert agg_crud.remove_study_from_aggregation(aid, "u1", 16326)["studies"] == []


def test_store_set_rows_add_remove_clear(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)

    # Unchecking one of s1's two rows leaves the other (and s2) checked.
    agg = agg_crud.set_aggregation_rows(aid, "u1", 16326, remove=[("s1", 11)])
    assert agg["studies"][0]["selected_rows"] == 2
    assert agg_crud.selected_rows_in(aid, 16326, ["s1", "s2", "zz"]) == {("s1", 10), ("s2", 10)}

    agg = agg_crud.set_aggregation_rows(aid, "u1", 16326, add=[("s1", 11), ("s3", 12)])
    assert agg["studies"][0]["selected_rows"] == 4
    assert agg_crud.selected_by_study(aid) == {16326: {("s1", 10), ("s1", 11), ("s2", 10), ("s3", 12)}}

    agg = agg_crud.set_aggregation_rows(aid, "u1", 16326, clear=True, add=[("s9", 1)])
    assert agg["studies"][0]["selected_rows"] == 1
    assert agg_crud.selected_rows_in(aid, 16326, ["s9"]) == {("s9", 1)}
    assert agg_crud.selected_rows_in(aid, 16326, []) == set()

    assert agg_crud.set_aggregation_rows(aid, "u1", 16326, clear=True)["studies"][0]["selected_rows"] == 0
    assert agg_crud.set_aggregation_rows(aid, "u2", 16326, add=[("s1", 10)]) is None


def test_store_readd_keeps_deselection(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)
    agg_crud.set_aggregation_rows(aid, "u1", 16326, remove=[("s2", 10)])
    # Re-adding a study already present must not re-check what the user unchecked.
    agg = agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)
    assert agg["studies"][0]["selected_rows"] == 2


def test_store_rename(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    assert agg_crud.rename_aggregation(aid, "u1", "B")["name"] == "B"
    assert agg_crud.rename_aggregation(aid, "u2", "C") is None
    assert agg_crud.get_aggregation(aid, "u1")["name"] == "B"


def test_store_delete_cascades(agg_crud, db_conn):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 1, ROWS)
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_files").fetchone()[0] == 3
    assert agg_crud.delete_aggregation(aid, "u2") is False
    assert agg_crud.delete_aggregation(aid, "u1") is True
    assert agg_crud.list_aggregations("u1") == []
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_studies").fetchone()[0] == 0
    # chain cascade: aggregation -> studies -> rows
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_files").fetchone()[0] == 0


def test_store_remove_study_cascades_rows(agg_crud, db_conn):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 1, ROWS)
    agg_crud.remove_study_from_aggregation(aid, "u1", 16326)
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_files").fetchone()[0] == 0


def _legacy_study(agg_crud, db_conn, checked):
    """A study as stored before per-row selection: rows_v 0, checked samples in aggregation_samples."""
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ())
    db_conn.execute("UPDATE aggregation_studies SET rows_v=0, file_rows=NULL WHERE aggregation_id=?", (aid,))
    db_conn.executemany("INSERT INTO aggregation_samples(aggregation_id, study_id, sample_id) VALUES(?,?,?)",
                        [(aid, 16326, s) for s in checked])
    db_conn.commit()
    return aid


def test_store_legacy_study_counts_samples_until_migrated(agg_crud, db_conn):
    aid = _legacy_study(agg_crud, db_conn, ["s1", "s2"])
    s = agg_crud.get_aggregation(aid, "u1")["studies"][0]
    assert (s["selected_rows"], s["file_rows"], s["rows_v"]) == (2, None, 0)   # legacy: counts checked samples


def test_store_migrate_study_rows_turns_checked_samples_into_rows(agg_crud, db_conn):
    # s1 (two artifacts) and s2 were checked; s3 was not. s9 is checked but has no file: it yields no row.
    aid = _legacy_study(agg_crud, db_conn, ["s1", "s2", "s9"])
    files = {"s1": [("16S", "Raw upload", 10, 1, 0), ("16S", "Raw upload", 11, 1, 0)],
             "s2": [("16S", "Raw upload", 10, 1, 0)], "s3": [("16S", "Raw upload", 12, 1, 0)]}
    assert agg_crud.migrate_study_rows(aid, 16326, files) is True
    s = agg_crud.get_aggregation(aid, "u1")["studies"][0]
    assert (s["selected_rows"], s["file_rows"], s["rows_v"]) == (3, 4, 1)
    assert agg_crud.selected_by_study(aid) == {16326: {("s1", 10), ("s1", 11), ("s2", 10)}}
    assert db_conn.execute("SELECT COUNT(*) FROM aggregation_samples").fetchone()[0] == 0
    # idempotent: a second call (or a new study) changes nothing
    assert agg_crud.migrate_study_rows(aid, 16326, files) is False
    assert agg_crud.migrate_study_rows(aid, 99, files) is False
    assert agg_crud.get_aggregation(aid, "u1")["studies"][0]["selected_rows"] == 3


_NO_FILTER = {"data_types": [], "processing": [], "artifacts": []}


def test_store_study_file_filter(agg_crud):
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)
    assert agg_crud.get_aggregation(aid, "u1")["studies"][0]["file_filter"] == _NO_FILTER
    f = {"data_types": ["16S"], "processing": [], "artifacts": ["11"]}
    assert agg_crud.set_study_file_filter(aid, "u1", 16326, f)["studies"][0]["file_filter"] == f
    assert agg_crud.set_study_file_filter(aid, "u2", 16326, f) is None      # not owned
    assert agg_crud.set_study_file_filter(aid, "u1", 999, f) is None        # study not in it
    # re-adding the study keeps its filter (INSERT OR IGNORE)
    assert agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)["studies"][0]["file_filter"] == f


def test_store_moves_aggregation_filter_to_studies_once(agg_crud, db_conn):
    """Pre-2026-09-30 filters lived on the aggregation; the bootstrap copies
    them to each study minus artifact picks (the cross-study bug), then clears
    the aggregation's copy so later boots and later-added studies stay as is."""
    import json
    import store.db as db
    aid = agg_crud.create_aggregation("u1", "A")["aggregation_id"]
    agg_crud.add_study_to_aggregation(aid, "u1", STUDY, 2, ROWS)
    agg_crud.add_study_to_aggregation(aid, "u1", {**STUDY, "study_id": 777}, 1, ROWS)
    old = {"data_types": ["Metagenomic"], "processing": ["Atropos v1.1.24"], "artifacts": ["119667"]}
    db_conn.execute("UPDATE aggregations SET file_filter_json=? WHERE aggregation_id=?", (json.dumps(old), aid))
    db_conn.commit()

    db._move_aggregation_filters_to_studies(db_conn)
    db_conn.commit()
    moved = {"data_types": ["Metagenomic"], "processing": ["Atropos v1.1.24"], "artifacts": []}
    assert [st["file_filter"] for st in agg_crud.get_aggregation(aid, "u1")["studies"]] == [moved, moved]
    assert db_conn.execute("SELECT file_filter_json FROM aggregations").fetchone()[0] is None

    agg_crud.add_study_to_aggregation(aid, "u1", {**STUDY, "study_id": 888}, 1, ROWS)
    db._move_aggregation_filters_to_studies(db_conn)                       # second boot: no-op
    db_conn.commit()
    assert [st["file_filter"] for st in agg_crud.get_aggregation(aid, "u1")["studies"]] == [moved, moved, _NO_FILTER]
