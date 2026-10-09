"""helpers/export_tools.py (export_table) against the real (temp) store with the
Qiita reads patched, plus GET /api/chat-exports/<id>.<csv|tsv>."""
import os
import sys

import pytest

from tests.conftest import stub_qiita_db_and_core

PATH = "/qmounts/qiita_data/"
COLUMNS = ["sample_type", "host_body_site", "country"]
MEMBERSHIP = {"s1": [(7, "16S")], "s2": [(7, "16S"), (8, "Metagenomic")], "s3": [(8, "Metagenomic")]}


def _art(aid, parent=None, prep=None, typ="FASTQ", vis="public", files=(("f.fastq.gz", "raw_forward_seqs"),)):
    return {"kind": "artifact", "node_id": f"a{aid}", "parent_node_id": parent, "artifact_id": aid,
            "prep_template_id": prep, "artifact_type": typ, "data_type": "16S" if aid < 5 else "Metagenomic",
            "name": f"art{aid}", "visibility": vis,
            "filepaths": [{"filepath_id": aid * 10 + i, "filename": fn, "filepath_type": ft,
                           "full_path": f"{PATH}{aid}/{fn}"} for i, (fn, ft) in enumerate(files)]}


GRAPH = [
    _art(1, prep=7),
    _art(2, parent="a1", typ="BIOM", files=(("table.biom", "biom"), ("table.qza", "qza"), ("log.txt", "log"))),
    _art(3, parent="a1", typ="BIOM", vis="private", files=(("secret.biom", "biom"),)),
    _art(5, prep=8, files=(("wgs.fastq.gz", "raw_forward_seqs"),)),
]


@pytest.fixture
def et(monkeypatch):
    import helpers.export_tools as et
    monkeypatch.setattr(et, "check_study", lambda name, sid, scope, chat_id: (int(sid), {}, None))
    monkeypatch.setattr(et, "study_columns", lambda sid: list(COLUMNS))
    monkeypatch.setattr(et, "list_study_sample_ids", lambda sid: sorted(MEMBERSHIP))
    monkeypatch.setattr(et, "prep_membership", lambda sid: MEMBERSHIP)
    et.calls = []

    def fake_table(sid, columns, sample_ids=None, limit=None):
        et.calls.append((list(columns), None if sample_ids is None else list(sample_ids), limit))
        ids = sorted(MEMBERSHIP) if sample_ids is None else list(sample_ids)
        return [(s, *[f"{c}:{s}" for c in columns]) for s in ids][:limit]

    monkeypatch.setattr(et, "fetch_sample_table", fake_table)
    monkeypatch.setattr(et, "load_preps_and_graph", lambda sid: ([], GRAPH))
    return et


def call(et, user="u1", chat="c1", scope="global", **args):
    return et.execute(args, scope=scope, chat_id=chat, user_id=user)


def stored(export_id, user="u1"):
    from store import get_export
    return get_export(export_id, user)


# ── samples ─────────────────────────────────────────────────────────────────

def test_samples_all_columns_stores_only_the_spec(et):
    r = call(et, source="samples", study_id=10317, format="tsv")
    p = r.ui_payload
    assert p["kind"] == "table_export" and p["format"] == "tsv" and p["name"] == "study_10317_samples"
    assert p["n_rows"] == 3 and p["n_columns"] == 4 and p["columns"] == ["sample_id"] + COLUMNS
    assert p["preview"][0] == ["s1", "sample_type:s1", "host_body_site:s1", "country:s1"]
    assert et.calls == [(COLUMNS, None, 5)]                                   # tool time: a preview only
    e = stored(p["export_id"])
    assert e["source"] == "samples" and e["spec"] == {"study_id": 10317} and e["rows"] is None
    assert et.build_table(e) == (["sample_id"] + COLUMNS,
                                 [[s, f"sample_type:{s}", f"host_body_site:{s}", f"country:{s}"] for s in ("s1", "s2", "s3")])
    assert "don't paste" in r.text


def test_samples_columns_and_prep_filter(et):
    p = call(et, source="samples", study_id=10317, columns=["COUNTRY", "nope"], prep_id=8, name="wgs samples").ui_payload
    assert p["name"] == "wgs_samples" and p["columns"] == ["sample_id", "country"] and p["n_rows"] == 2
    assert et.calls[-1] == (["country"], ["s2", "s3"], 5)
    assert stored(p["export_id"])["spec"] == {"study_id": 10317, "prep_id": 8, "columns": ["COUNTRY", "nope"]}


def test_samples_unknown_columns_and_no_match_are_errors(et):
    r = call(et, source="samples", study_id=10317, columns=["nope"])
    assert r.ui_payload is None and "sample_type" in r.text
    assert call(et, source="samples", study_id=10317, data_type="ITS").ui_payload is None


# ── files ───────────────────────────────────────────────────────────────────

def test_files_public_only_with_full_paths(et):
    p = call(et, source="files", study_id=10317).ui_payload
    cols, rows = et.build_table(stored(p["export_id"]))
    assert cols == et.FILE_COLUMNS and p["n_rows"] == 5
    assert [r[7] for r in rows] == ["f.fastq.gz", "log.txt", "table.biom", "table.qza", "wgs.fastq.gz"]
    assert rows[2] == [10317, 7, "16S", 2, "BIOM", "art2", "biom", "table.biom", f"{PATH}2/table.biom"]
    assert all("secret" not in r[7] for r in rows)                           # private artifact 3


def test_files_filters(et):
    def names(**f):
        p = call(et, source="files", study_id=10317, **f).ui_payload
        return [r[7] for r in et.build_table(stored(p["export_id"]))[1]]
    assert names(file_type="biom") == ["table.biom"]
    assert names(file_type="QZA") == ["table.qza"]
    assert names(artifact_type="biom") == ["log.txt", "table.biom", "table.qza"]
    assert names(prep_id=8) == ["wgs.fastq.gz"]
    assert names(data_type="metagenomic") == ["wgs.fastq.gz"]
    assert call(et, source="files", study_id=10317, file_type="bam").ui_payload is None


# ── aggregation / rows ──────────────────────────────────────────────────────

def test_aggregation_links_this_chats_or_a_saved_one(et):
    from store import create_aggregation
    create_aggregation("u1", "random")
    temp = create_aggregation("u1", "chat", chat_id="c1", chat_scope="global")
    p = call(et, source="aggregation").ui_payload
    assert p["aggregation_id"] == temp["aggregation_id"] and p["name"] == "This chat's aggregation"
    assert "export_id" not in p
    saved = call(et, source="aggregation", aggregation="RANDOM", format="tsv").ui_payload
    assert saved["name"] == "random" and saved["format"] == "tsv"
    assert '"random"' in call(et, source="aggregation", aggregation="missing").text
    assert "no aggregation yet" in call(et, chat="c2", source="aggregation").text


def test_rows_are_stored_padded_and_capped(et):
    p = call(et, source="rows", columns=["study", "samples"], rows=[[1070, 25], ["11546"], [1, 2, 3]]).ui_payload
    e = stored(p["export_id"])
    assert et.build_table(e) == (["study", "samples"], [["1070", "25"], ["11546", ""], ["1", "2"]])
    assert p["n_rows"] == 3 and p["name"] == "chat_table"
    assert "At most 2000 rows" in call(et, source="rows", columns=["a"], rows=[[1]] * 2001).text
    assert "At most 50 columns" in call(et, source="rows", columns=list("x" * 51), rows=[[1]]).text
    assert "list of cell values" in call(et, source="rows", columns=["a"], rows=["oops"]).text


def test_refusals(et):
    assert "signed-in" in call(et, user=None, source="rows", columns=["a"], rows=[[1]]).text
    assert "source must be" in call(et, source="pdf").text
    assert "csv or tsv" in call(et, source="rows", format="xlsx", columns=["a"], rows=[[1]]).text


def test_serialize(et):
    assert et.serialize(["a", "b"], [["1", "x,y"]], "csv") == 'a,b\n1,"x,y"\n'
    assert et.serialize(["a", "b"], [["1", "x,y"]], "tsv") == "a\tb\n1\tx,y\n"


def test_exports_go_with_their_chat(et, global_chat_crud):
    chat = global_chat_crud.create_global_chat("u1", "t")["chat_id"]
    p = call(et, chat=chat, source="rows", columns=["a"], rows=[[1]]).ui_payload
    assert stored(p["export_id"]) is not None
    global_chat_crud.delete_global_chat("u1", chat)
    assert stored(p["export_id"]) is None


def test_the_prompt_asks_when_csv_vs_aggregation_is_unclear():
    import config
    assert "ask which in one short question" in config.STUDY_TOOLS_PROMPT
    assert "export_table" in config.STUDY_TOOLS_PROMPT and "add_to_workspace" in config.STUDY_TOOLS_PROMPT


# ── GET /api/chat-exports/<id>.<ext> ─────────────────────────────────────────

@pytest.fixture(scope="module")
def _app(tmp_path_factory):
    os.environ["QIITA_EXPERIMENT_DB_PATH"] = str(tmp_path_factory.mktemp("exports") / "test.db")
    for name in list(sys.modules):
        if (name == "run" or name.startswith("routes.") or name == "store" or name.startswith("store.")
                or name.startswith("helpers.") or "sql_store" in name):
            del sys.modules[name]
    stub_qiita_db_and_core()
    import run
    import config
    config.ALLOWED_ORIGINS = []
    return run.app


@pytest.fixture
def signed_in(_app, monkeypatch):
    """(client, the signed-in user's id, the route module) — the route's own
    store bindings, since fresh_db re-imports store for every test."""
    import routes.auth_routes as auth_routes
    import routes.export_routes as er
    from helpers.qiita_client import WhoAmIResult
    monkeypatch.setattr(auth_routes, "whoami", lambda pat: WhoAmIResult(ok=True, identity={
        "principal_idx": 90002, "email": "export@test.local", "system_role": "user", "scopes": [],
        "profile_complete": True}))
    client = _app.test_client()
    assert client.post("/api/auth/connect", json={"token": "qk_test"}).status_code == 200
    conn = er.get_export.__globals__["_conn"]
    with conn() as c:
        user_id = c.execute("SELECT user_id FROM users WHERE principal_idx = 90002").fetchone()[0]
    return client, user_id, er


def test_download_csv_and_tsv_for_the_owner_only(signed_in):
    client, user_id, er = signed_in
    create = er.get_export.__globals__["create_export"]
    eid = create(user_id, "c1", "global", "my_table", "rows", {}, {"columns": ["a", "b"], "rows": [["1", "x,y"]]})
    r = client.get(f"/api/chat-exports/{eid}.csv")
    assert r.status_code == 200 and r.data == b'a,b\n1,"x,y"\n' and r.mimetype == "text/csv"
    assert 'filename="my_table.csv"' in r.headers["Content-Disposition"]
    r = client.get(f"/api/chat-exports/{eid}.tsv")
    assert r.data == b"a\tb\n1\tx,y\n" and r.mimetype == "text/tab-separated-values"
    assert client.get(f"/api/chat-exports/{eid}.xlsx").status_code == 404
    other = create("someone-else", "c1", "global", "x", "rows", {}, {"columns": ["a"], "rows": [["1"]]})
    assert client.get(f"/api/chat-exports/{other}.csv").status_code == 404


def test_download_refuses_a_study_no_longer_public(signed_in, monkeypatch):
    client, user_id, er = signed_in
    eid = er.get_export.__globals__["create_export"](user_id, "c1", "global", "s", "samples", {"study_id": 999})
    monkeypatch.setattr(er, "is_study_public", lambda sid: False)
    assert client.get(f"/api/chat-exports/{eid}.csv").status_code == 404
