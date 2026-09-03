"""LLM-generated chat titles, produced off the request thread.

A chat's title starts as a fast, deterministic truncation of the user's
first message (store.crud/global_chat_crud, unchanged). This module upgrades
that provisional title to a real, LLM-written one without adding latency to
the first turn: the request thread starts a daemon thread here and keeps
streaming the answer; the thread persists its result independently via the
`persist` callback it's given, and the turn joins it for at most a few
seconds right before it emits `done` so the title can ride along on the same
frame. If the LLM call is unusually slow (llm_chat inherits the SDK's own
~300s timeout), the join simply times out — the thread keeps running and
finishes the write on its own, and the provisional title is what `done`
reports for this turn.

Deliberately store-free: this module imports nothing from `store`, only
`llm_chat`, so it needs no special handling in tests that reload `store*`
modules per-test.
"""
import logging
import re
import threading

from helpers.llm_helpers import llm_chat

logger = logging.getLogger(__name__)

_TITLE_SYSTEM_PROMPT = (
    "You name conversations with a microbiome-study discovery assistant, "
    "based on the user's opening message. Reply with a short, specific "
    "title of 3-7 words, sentence case, at most 60 characters. Plain text "
    "only: no quotes, no markdown, no trailing punctuation, no explanation. "
    "If the message is a slash command, describe the action instead (e.g. "
    "'Pin study 123')."
)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.S)
_MAX_TITLE_CHARS = 60
_MAX_INPUT_CHARS = 1500


def _clean_title(raw):
    text = _THINK_BLOCK_RE.sub("", raw or "").strip()
    line = next((s.strip() for s in text.splitlines() if s.strip()), "")
    line = line.strip("\"'*# ").rstrip(".")
    return line[:_MAX_TITLE_CHARS].strip()


def generate_chat_title(user_content, model):
    """The cleaned LLM title, or None if the call failed or returned nothing
    usable — callers fall back to the provisional title already on the row."""
    try:
        raw = llm_chat(
            [{"role": "user", "content": (user_content or "")[:_MAX_INPUT_CHARS] +
                                          "\n\nReply with the title only."}],
            study_context_text=None, system_prompt=_TITLE_SYSTEM_PROMPT, model=model,
        )
    except Exception as exc:
        logger.warning("chat title generation failed: %s", exc)
        return None
    title = _clean_title(raw)
    return title or None


def start_title_job(user_content, model, persist):
    """Fire-and-persist: generate the title, then hand it to `persist` if any
    was produced. Runs on a daemon thread so it never blocks the turn and
    never needs cleanup at process/test exit."""
    def _run():
        try:
            title = generate_chat_title(user_content, model)
            if title:
                persist(title)
        except Exception:
            logger.exception("chat title job failed")

    job = threading.Thread(target=_run, daemon=True, name="chat-title")
    job.start()
    return job


def finish_title_job(job, timeout=5.0):
    """Block briefly for the job so its result can ride along on `done`.
    A timeout leaves the thread running; it still persists on its own."""
    if job is not None:
        job.join(timeout)
