"""LLM chat titling: helpers/chat_title.py's cleaning/fallback logic (no DB,
so no fresh_db reload needed — the module is deliberately store-free), and
store/chat_turn_persist.py's conditional persist."""
import threading

import pytest


class TestCleanTitle:

    def _clean(self, raw):
        import helpers.chat_title as ct
        return ct._clean_title(raw)

    def test_strips_think_block(self):
        assert self._clean("<think>hmm, let me consider</think>Gut Microbiome Study") == \
            "Gut Microbiome Study"

    def test_takes_first_non_empty_line(self):
        assert self._clean("\n\nSoil Sample Survey\nsome extra explanation") == "Soil Sample Survey"

    def test_strips_quotes_and_markup(self):
        assert self._clean('"Ocean Microbiome Trends"') == "Ocean Microbiome Trends"
        assert self._clean("**Wild Mouse Gut**") == "Wild Mouse Gut"

    def test_strips_trailing_period(self):
        assert self._clean("Antibiotic Resistance Study.") == "Antibiotic Resistance Study"

    def test_caps_at_sixty_chars(self):
        raw = "A" * 80
        assert len(self._clean(raw)) == 60

    def test_empty_input(self):
        assert self._clean("") == ""
        assert self._clean(None) == ""


class TestGenerateChatTitle:

    def test_returns_cleaned_title_on_success(self, monkeypatch):
        import helpers.chat_title as ct
        monkeypatch.setattr(ct, "llm_chat", lambda *a, **k: '"IBD Gut Studies"')
        assert ct.generate_chat_title("find ibd studies", "minimax-m2") == "IBD Gut Studies"

    def test_none_when_llm_chat_raises(self, monkeypatch):
        import helpers.chat_title as ct

        def boom(*a, **k):
            raise RuntimeError("nrp down")
        monkeypatch.setattr(ct, "llm_chat", boom)
        assert ct.generate_chat_title("hello", "minimax-m2") is None

    def test_none_when_llm_chat_returns_empty(self, monkeypatch):
        import helpers.chat_title as ct
        monkeypatch.setattr(ct, "llm_chat", lambda *a, **k: "   ")
        assert ct.generate_chat_title("hello", "minimax-m2") is None


class TestTitleJob:

    def test_persist_called_with_cleaned_title(self, monkeypatch):
        import helpers.chat_title as ct
        monkeypatch.setattr(ct, "llm_chat", lambda *a, **k: "Gut Microbiome Overview")
        persisted = []
        job = ct.start_title_job("find gut studies", "minimax-m2", persisted.append)
        ct.finish_title_job(job)
        assert persisted == ["Gut Microbiome Overview"]

    def test_persist_not_called_on_none(self, monkeypatch):
        import helpers.chat_title as ct
        monkeypatch.setattr(ct, "llm_chat", lambda *a, **k: "")
        persisted = []
        job = ct.start_title_job("hello", "minimax-m2", persisted.append)
        ct.finish_title_job(job)
        assert persisted == []

    def test_finish_title_job_tolerates_none_job(self):
        import helpers.chat_title as ct
        ct.finish_title_job(None)  # must not raise

    def test_job_is_a_daemon_thread(self, monkeypatch):
        import helpers.chat_title as ct
        monkeypatch.setattr(ct, "llm_chat", lambda *a, **k: "Title")
        job = ct.start_title_job("hello", "minimax-m2", lambda t: None)
        assert isinstance(job, threading.Thread)
        assert job.daemon is True
        ct.finish_title_job(job)


@pytest.fixture
def chat_turn_persist(fresh_db):
    import store.chat_turn_persist as ctp
    return ctp


class TestAutoTitlePersist:

    def test_writes_when_title_is_still_new_chat(self, chat_turn_persist, global_chat_crud, sample_user_id):
        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        assert chat_turn_persist.get_chat_title(chat_id, "global") == "New chat"
        ok = chat_turn_persist.set_auto_title(chat_id, "global", "Gut Microbiome Overview", "find gut studies")
        assert ok is True
        assert chat_turn_persist.get_chat_title(chat_id, "global") == "Gut Microbiome Overview"

    def test_writes_when_title_is_the_provisional_truncation(self, chat_turn_persist, global_chat_crud,
                                                              sample_user_id):
        from store.chat_turn_persist import append_user_message
        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        append_user_message("global", chat_id, sample_user_id, "find gut studies")
        assert chat_turn_persist.get_chat_title(chat_id, "global") == "find gut studies"
        ok = chat_turn_persist.set_auto_title(chat_id, "global", "Gut Microbiome Overview", "find gut studies")
        assert ok is True
        assert chat_turn_persist.get_chat_title(chat_id, "global") == "Gut Microbiome Overview"

    def test_skips_when_title_was_explicitly_renamed(self, chat_turn_persist, global_chat_crud, sample_user_id):
        chat_id = global_chat_crud.create_global_chat(sample_user_id)["chat_id"]
        global_chat_crud.update_global_chat_title(sample_user_id, chat_id, "My Custom Name")
        ok = chat_turn_persist.set_auto_title(chat_id, "global", "Gut Microbiome Overview", "find gut studies")
        assert ok is False
        assert chat_turn_persist.get_chat_title(chat_id, "global") == "My Custom Name"

    def test_get_chat_title_none_for_unknown_chat(self, chat_turn_persist):
        assert chat_turn_persist.get_chat_title("nope", "global") is None

    def test_set_auto_title_does_not_bump_updated_at(self, chat_turn_persist, global_chat_crud, sample_user_id):
        chat = global_chat_crud.create_global_chat(sample_user_id)
        before = chat["updated_at"]
        chat_turn_persist.set_auto_title(chat["chat_id"], "global", "Gut Microbiome Overview", "hi")
        after = global_chat_crud.get_global_chat(sample_user_id, chat["chat_id"])["updated_at"]
        assert after == before
