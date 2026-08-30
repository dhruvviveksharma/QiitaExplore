"""Pinned-study context assembly — split out of helpers/qiita_fetch.py to keep
that file under the 500-line cap (TKT-013 family).

Budget policy: a flat PINNED_CHARS_PER_STUDY per inlined study, clamped by what
the model can physically hold. Only the first PINNED_INLINE_STUDIES are inlined;
the rest become one-line manifest entries the model can expand with
get_study_report(<id>).
"""

from concurrent.futures import ThreadPoolExecutor

from config import (
    REPORT_SAMPLE_LIMIT,
    PINNED_CHARS_PER_STUDY,
    PINNED_INLINE_STUDIES,
    context_budget_chars,
)
from helpers.qiita_fetch import (
    _fetch_study_header_cached,
    _get_or_fetch_full_samples,
    _truncate,
)
from helpers.llm_helpers import _format_pi_line

# Conservative floor on chars-per-sample, used only to size the *fetch* so the
# char budget is never starved by too few rows. The render loop below enforces
# the exact budget, so over-fetching costs a larger SQL LIMIT and nothing else.
_MIN_CHARS_PER_SAMPLE = 120


def _build_full_samples_block(study_id: int, budget_chars: int, *, tools_available: bool = True,
                              report_tool_name: str = "get_study_report"):
    """Compact full-metadata block for one pinned study, clipped to budget_chars."""
    header      = _fetch_study_header_cached(study_id)
    fetch_limit = max(REPORT_SAMPLE_LIMIT, int(budget_chars) // _MIN_CHARS_PER_SAMPLE)
    samples     = _get_or_fetch_full_samples(study_id, limit=fetch_limit)
    title       = (header or {}).get("study_title") or "Untitled study"
    num_samples = (header or {}).get("num_samples") or (len(samples) if samples else 0)
    data_types  = (header or {}).get("data_types") or ""

    # PI must appear here: the UI card shows it, so a report block without it
    # leaves the model contradicting the screen it sits next to.
    lines = [
        f"### Study {study_id}: {_truncate(title, 140)}",
        f"  PI: {_format_pi_line((header or {}).get('pi_name'), (header or {}).get('pi_affiliation'))}",
        f"  Data Types: {data_types or 'Not available'} | Total samples: {num_samples} | In report: {len(samples)}",
    ]
    if not samples:
        lines.append("  _No sample metadata available._")
        return "\n".join(lines)

    skip_fields = {"qiita_study_id"}
    empty_vals  = {"none", "null", "nan", "not applicable", "not provided", ""}
    budget      = max(500, int(budget_chars))
    out         = "\n".join(lines) + "\n"
    truncated_at = None
    for idx, sample in enumerate(samples):
        sid    = sample.get("sample_id", "?")
        fields = sample.get("fields") or {}
        parts  = []
        for k, v in sorted(fields.items()):
            if k in skip_fields or v is None:
                continue
            val = str(v).strip()
            if not val or val.lower() in empty_vals:
                continue
            parts.append(f"{k}={_truncate(val, 120)}")
        line = f"  {sid}: " + ", ".join(parts) + "\n"
        if len(out) + len(line) > budget:
            truncated_at = idx
            break
        out += line
    if truncated_at is not None:
        hatch = f"; call {report_tool_name}({study_id}) for more" if tools_available else ""
        out += f"  _(showed {truncated_at} of {num_samples} samples{hatch})_\n"
    return out.rstrip()


def _pinned_per_study_budget(n_inline: int, model: str) -> int:
    """Chars each inlined pinned study may spend.

    A flat PINNED_CHARS_PER_STUDY, clamped by what the model can physically
    hold. The clamp is not a second budgeting scheme — it only stops the
    constant overflowing a small context window: 5 x 60,000 exceeds the entire
    430,752-char budget of the 131k-token models, which would fail at the API
    rather than degrade. Above ~200k tokens the clamp never fires.
    """
    capacity = int(context_budget_chars(model) * 0.65) // max(1, n_inline)
    return min(PINNED_CHARS_PER_STUDY, capacity)


def _pinned_manifest_line(study_id: int, header: dict, *,
                          report_tool_name: str = "get_study_report") -> str:
    """One line for a pinned study that wasn't inlined.

    Carries the ID and shape so the model can decide to expand it — without the
    ID it could not call the tool at all, which is how dropped studies used to
    become invisible. Takes the header rather than fetching it, so the caller can
    resolve them all in one fan-out.
    """
    h = header or {}
    parts = [f"- ID {study_id}: {_truncate(h.get('study_title') or 'Untitled study', 140)}"]
    if h.get("num_samples") is not None:
        parts.append(f"{h['num_samples']} samples")
    if (h.get("data_types") or "").strip():
        parts.append(f"types: {h['data_types'].strip()}")
    parts.append(f"call {report_tool_name}({study_id}) to load its samples")
    return " | ".join(parts)


def _build_pinned_reports_context(study_ids, model: str, *, tools_available: bool = True,
                                  report_tool_name: str = "get_study_report"):
    """Build a 'PINNED STUDY REPORTS' context block from the given pinned study IDs.

    With tools, the first PINNED_INLINE_STUDIES are inlined in full and the rest
    become manifest lines the model can expand on demand.

    Without tools there is nothing to expand *with*, so a manifest line is an
    instruction the model cannot follow — every study is inlined instead and the
    budget is split across all of them. That is the pre-manifest behaviour, and it
    is what the project-chat and /pin flows need since both stream through
    llm_chat_stream with no tools attached.
    """
    if not study_ids:
        return None
    study_ids = list(study_ids)
    inline    = study_ids[:PINNED_INLINE_STUDIES] if tools_available else study_ids
    overflow  = study_ids[PINNED_INLINE_STUDIES:] if tools_available else []
    per_study = _pinned_per_study_budget(len(inline), model)

    with ThreadPoolExecutor(max_workers=min(len(inline), 4)) as pool:
        blocks = list(pool.map(
            lambda sid: _build_full_samples_block(
                sid, per_study, tools_available=tools_available,
                report_tool_name=report_tool_name),
            inline,
        ))
        headers = list(pool.map(_fetch_study_header_cached, overflow)) if overflow else []

    text = (
        "PINNED STUDY REPORTS (full sample-level metadata for studies the user pinned):\n"
        "Use these for per-sample questions and cross-study comparisons.\n"
        + "\n\n".join(b for b in blocks if b)
    )
    if overflow:
        text += (
            f"\n\nALSO PINNED ({len(overflow)} more, not inlined — load any of them on demand):\n"
            + "\n".join(
                _pinned_manifest_line(sid, h, report_tool_name=report_tool_name)
                for sid, h in zip(overflow, headers)
            )
        )
    return text
