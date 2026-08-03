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

# Conservative floor on chars-per-sample, used only to size the *fetch* so the
# char budget is never starved by too few rows. The render loop below enforces
# the exact budget, so over-fetching costs a larger SQL LIMIT and nothing else.
_MIN_CHARS_PER_SAMPLE = 120


def _build_full_samples_block(study_id: int, budget_chars: int):
    """Compact full-metadata block for one pinned study, clipped to budget_chars."""
    header      = _fetch_study_header_cached(study_id)
    fetch_limit = max(REPORT_SAMPLE_LIMIT, int(budget_chars) // _MIN_CHARS_PER_SAMPLE)
    samples     = _get_or_fetch_full_samples(study_id, limit=fetch_limit)
    title       = (header or {}).get("study_title") or "Untitled study"
    num_samples = (header or {}).get("num_samples") or (len(samples) if samples else 0)
    data_types  = (header or {}).get("data_types") or ""

    lines = [
        f"### Study {study_id}: {_truncate(title, 140)}",
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
        out += (f"  _(showed {truncated_at} of {num_samples} samples; "
                f"call get_study_report({study_id}) for more)_\n")
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


def _pinned_manifest_line(study_id: int) -> str:
    """One line for a pinned study that wasn't inlined.

    Carries the ID and shape so the model can decide to expand it — without the
    ID it could not call the tool at all, which is how dropped studies used to
    become invisible.
    """
    h = _fetch_study_header_cached(study_id) or {}
    title = _truncate(h.get("study_title") or "Untitled study", 140)
    parts = [f"- ID {study_id}: {title}"]
    if h.get("num_samples") is not None:
        parts.append(f"{h['num_samples']} samples")
    if (h.get("data_types") or "").strip():
        parts.append(f"types: {h['data_types'].strip()}")
    parts.append(f"call get_study_report({study_id}) to load its samples")
    return " | ".join(parts)


def _build_pinned_reports_context(study_ids, model: str):
    """Build a 'PINNED STUDY REPORTS' context block from the given pinned study IDs.

    The first PINNED_INLINE_STUDIES are inlined with full sample metadata; the
    rest become one-line manifest entries the model can expand on demand.
    """
    if not study_ids:
        return None
    inline    = list(study_ids)[:PINNED_INLINE_STUDIES]
    overflow  = list(study_ids)[PINNED_INLINE_STUDIES:]
    per_study = _pinned_per_study_budget(len(inline), model)

    with ThreadPoolExecutor(max_workers=min(len(inline), 4)) as pool:
        blocks = list(pool.map(lambda sid: _build_full_samples_block(sid, per_study), inline))

    text = (
        "PINNED STUDY REPORTS (full sample-level metadata for studies the user pinned):\n"
        "Use these for per-sample questions and cross-study comparisons.\n"
        + "\n\n".join(b for b in blocks if b)
    )
    if overflow:
        text += (
            f"\n\nALSO PINNED ({len(overflow)} more, not inlined — load any of them on demand):\n"
            + "\n".join(_pinned_manifest_line(sid) for sid in overflow)
        )
    return text
