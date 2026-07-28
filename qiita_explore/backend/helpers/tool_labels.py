"""In-flight human-readable labels for tool calls, keyed by tool name.

A standalone module with no agent_tools/pg_pool/qiita_core imports, so
pi_translate.py — a pure module — can format tool labels without dragging in
agent_tools's Postgres-touching import chain (agent_tools -> study_service ->
pg_pool -> qiita_core) just to format a label string.
"""


def _tool_label(name: str, args: dict) -> str:
    """Human-readable step label for a tool call while it runs."""
    if name == "search_studies":
        kws = (args.get("organism") or args.get("keywords") or
               args.get("qualifier") or args.get("body_site") or [])[:3]
        return f"Searching: {', '.join(kws)}…" if kws else "Searching Qiita…"
    if name == "get_study_report":
        return f"Loading report for study {args.get('study_id', '?')}…"
    if name == "pin_study":
        ids = args.get("study_ids") or []
        return f"Pinning {len(ids)} {'study' if len(ids) == 1 else 'studies'}…"
    if name == "search_by_sample":
        ff  = args.get("field_filters") or []
        kws = args.get("keywords") or []
        parts = [f"{f['field']}={f['value']}" for f in ff[:2]] + kws[:2]
        return f"Sample search: {', '.join(parts)}…" if parts else "Searching sample metadata…"
    return f"Running {name}…"
