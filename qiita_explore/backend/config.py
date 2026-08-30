import os
from dotenv import load_dotenv
from openai import OpenAI
import anthropic as _anthropic

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")

client = OpenAI(
    api_key=API_KEY,
    base_url="https://ellm.nrp-nautilus.io/v1",
    timeout=300.0,
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

DEFAULT_MODEL  = "minimax-m2"
ALLOWED_MODELS = {
    "qwen3-small", "deepseek-v4-flash", "glm-5", "minimax-m2",
    "claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8",
}

MODEL_METADATA = {
    "qwen3-small":       {"provider": "nrp",       "tier": "main",       "size": "27B",  "context": 1_000_000, "modalities": "image, video"},
    "deepseek-v4-flash": {"provider": "nrp",       "tier": "evaluating", "size": "304B", "context": 1_048_576, "modalities": "—"},
    "glm-5":             {"provider": "nrp",       "tier": "evaluating", "size": "744B", "context": 300_000,   "modalities": "—"},
    "minimax-m2":        {"provider": "nrp",       "tier": "evaluating", "size": "230B", "context": 204_800,   "modalities": "—"},
    "claude-haiku-4-5":  {"provider": "anthropic", "tier": "main",       "size": "—",    "context": 200_000,   "modalities": "image"},
    "claude-sonnet-4-6": {"provider": "anthropic", "tier": "main",       "size": "—",    "context": 200_000,   "modalities": "image"},
    "claude-opus-4-8":   {"provider": "anthropic", "tier": "evaluating", "size": "—",    "context": 200_000,   "modalities": "image"},
}


def get_client(model: str):
    """Return (client, provider_str) for the given model."""
    meta = MODEL_METADATA.get(model or DEFAULT_MODEL, MODEL_METADATA[DEFAULT_MODEL])
    provider = meta.get("provider", "nrp")
    if provider == "anthropic":
        from store.crud import get_setting
        key = get_setting('anthropic_api_key') or ANTHROPIC_API_KEY
        return _anthropic.Anthropic(api_key=key, timeout=300.0), "anthropic"
    return client, "nrp"


def context_budget_chars(model: str) -> int:
    ctx_tokens = MODEL_METADATA.get(model or DEFAULT_MODEL, MODEL_METADATA[DEFAULT_MODEL])["context"]
    chars = int((ctx_tokens - 8_000) * 3.5)
    return max(8_000, chars)


# Agent-level retry on transient LLM errors (429/5xx/connection): attempts
# and exponential base delay (2s -> 4s -> 8s at the defaults).
LLM_RETRY_MAX           = int(os.getenv("LLM_RETRY_MAX", "3"))
LLM_RETRY_BASE_DELAY_MS = int(os.getenv("LLM_RETRY_BASE_DELAY_MS", "2000"))

# Per-entry cap on tool-result text persisted in a turn's model_transcript —
# long-term memory stays lean while the live turn still sees full results.
TRANSCRIPT_TOOL_RESULT_CHARS = int(os.getenv("TRANSCRIPT_TOOL_RESULT_CHARS", "2000"))

# How many *executed* search-tool calls the model may make per user message
# (empty-input and crashed calls don't count). Past the cap the search tools
# are stripped from the offered schema for the rest of the turn.
SEARCH_CALLS_PER_MESSAGE = int(os.getenv("SEARCH_CALLS_PER_MESSAGE", "5"))

GLOBAL_SEARCH_SQL_LIMIT_BROAD   = int(os.getenv("GLOBAL_SEARCH_SQL_LIMIT_BROAD", "120"))
GLOBAL_SEARCH_SQL_LIMIT_NARROW  = int(os.getenv("GLOBAL_SEARCH_SQL_LIMIT_NARROW", "50"))
SAMPLE_SEARCH_DEFAULT_CANDIDATES = int(os.getenv("SAMPLE_SEARCH_DEFAULT_CANDIDATES", "40"))
SAMPLE_SEARCH_DEEP_CANDIDATES    = int(os.getenv("SAMPLE_SEARCH_DEEP_CANDIDATES",    "500"))
SAMPLE_SEARCH_PROBE_TIMEOUT_MS   = int(os.getenv("SAMPLE_SEARCH_PROBE_TIMEOUT_MS",   "15000"))
PG_POOL_MIN_CONN                = int(os.getenv("PG_POOL_MIN_CONN", "2"))
PG_POOL_MAX_CONN                = int(os.getenv("PG_POOL_MAX_CONN", "8"))
REPORT_SAMPLE_LIMIT             = 200

# Pinned-study context. Chars, not sample counts: chars/sample varies ~4x across
# studies, so a sample cap doesn't bound cost. Only the first
# PINNED_INLINE_STUDIES are inlined; the rest are listed as one-line manifest
# entries the model can expand with get_study_report(<id>).
PINNED_CHARS_PER_STUDY          = int(os.getenv("PINNED_CHARS_PER_STUDY", "60000"))
PINNED_INLINE_STUDIES           = int(os.getenv("PINNED_INLINE_STUDIES", "5"))

# ── Qiita PAT authentication ────────────────────────────────────────────────
SESSION_COOKIE_NAME = "qe_sid"

QIITA_CONTROL_PLANE_URL = os.getenv("QIITA_CONTROL_PLANE_URL", "http://127.0.0.1:8080").rstrip("/")
QIITA_WHOAMI_TIMEOUT_SECONDS = float(os.getenv("QIITA_WHOAMI_TIMEOUT_SECONDS", "5"))

# Qiita URL the user's BROWSER is redirected to for login. Deliberately
# separate from QIITA_CONTROL_PLANE_URL (used only for backend-to-Qiita
# server calls like whoami): in a split-tunnel deployment — backend reaches
# Qiita via a reverse SSH tunnel on one port, the browser reaches it directly
# on another — these are not the same address. Defaults to
# QIITA_CONTROL_PLANE_URL for the common case where both paths coincide.
QIITA_PUBLIC_LOGIN_URL = os.getenv("QIITA_PUBLIC_LOGIN_URL", "").rstrip("/") or QIITA_CONTROL_PLANE_URL

# AuthRocket LoginRocket realm base (e.g. https://<realm>.e2.loginrocket.com).
# When set, /api/auth/login-url routes the browser through LoginRocket /logout
# first, so a cached AuthRocket session can't hijack the login / "Need a login"
# entry into completing login as the previously-cached user. Empty = disabled
# (login goes straight to the control plane, exactly as before).
QIITA_LOGINROCKET_URL = os.getenv("QIITA_LOGINROCKET_URL", "").rstrip("/")

# A single hard ceiling, and no idle timeout: a session ends when it hits this,
# when the user logs out, or when the user signs in again. An idle timeout under
# a 24-hour ceiling would only reintroduce the mid-session logout.
AUTH_SESSION_ABSOLUTE_TTL_SECONDS  = int(os.getenv("AUTH_SESSION_ABSOLUTE_TTL_SECONDS", str(24 * 3600)))

# Comma-separated exact origins allowed for credentialed cross-origin dev
# requests (e.g. frontend on a different port than the backend). Leave unset
# in production — same-origin deployments behind the proxy need no CORS.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("QIITA_EXPLORE_ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Cookie Secure flag. Defaults to True; set to "false" only for plain-HTTP
# loopback development (e.g. http://127.0.0.1 with no TLS).
SESSION_COOKIE_SECURE = os.getenv("QIITA_EXPLORE_COOKIE_SECURE", "true").strip().lower() not in ("false", "0", "no")

# If set, only this Qiita principal_idx may claim the legacy 'default'-owned
# data via POST /api/auth/claim-default. Unset (default) = claiming disabled.
_claimant_raw = os.getenv("QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX", "").strip()
QIITA_DEFAULT_DATA_CLAIMANT_PRINCIPAL_IDX = int(_claimant_raw) if _claimant_raw.isdigit() else None

# Temporary debugging aid: when true, an unexpected exception in
# POST /auth/connect includes the exception type/message in the JSON
# response, not just the server log. Off by default — only enable while
# actively diagnosing a deployment issue, never leave on in real use.
DEBUG_ERROR_DETAIL = os.getenv("QIITA_EXPLORE_DEBUG_ERRORS", "false").strip().lower() in ("1", "true", "yes")

GLOBAL_CHAT_SYSTEM_PROMPT = """You are a discovery assistant for the Qiita microbiome database.

Your primary goal is to help researchers find studies from the entire Qiita database that match their scientific criteria.

## Tools available to you
You have the following tools. Call them as needed — do not wait for the user to invoke them explicitly.
- **search_studies**: Search Qiita for public studies. Call this whenever the user asks to find, discover, or filter studies.
  - You may call search_studies up to 5 times per user message. Start with ONE well-filled call; only search again with meaningfully DIFFERENT keywords/slots when the first results are thin or the user's request spans distinct concepts. Never repeat the same query.
  - Each search features at most 10 studies in the chat reply, no matter what. Do NOT use `limit` or repeated identical searches to inflate the count — the complete ranked list of EVERY match is automatically shown to the user in the results panel (the "View all N" link on the search banner); point them to it. Only set `limit` below 10 when the user asks for fewer (e.g. "find me 3 studies" → limit=3).
  - The deep scan probes the ~500 largest public studies by sample count, so very small studies may not be scanned. If the user asks whether results are complete, say this plainly.
  - The tool has **typed dimension slots** — fill every slot you can identify from the query with ALL synonyms for that concept. The backend pools all slots into one ranked search, so filling generously never over-narrows.
  - **`organism`**: all names for the host/focal organism — common names, Latin binomials, strains, related genera, plural + singular.
    e.g. mouse → ["mouse","mice","murine","Mus musculus","house mouse","field mouse","wood mouse","C57BL/6","BALB/c","Apodemus","Peromyscus","rodent","rodents"]
  - **`qualifier`**: condition/status/context modifiers — wild, captive, diseased, treated, life stage, diet, etc.
    e.g. wild → ["wild","wild animal","wild animals","wild-caught","feral","feral mice","free-living","wildlife","non-captive","wild mice","wild mouse","wild rodent"]
  - **`body_site`**: anatomical location or environmental niche + synonyms.
    e.g. gut → ["gut","intestine","colon","GI tract","cecum","feces","stool","fecal","host-associated"]
    e.g. soil → ["soil","rhizosphere","sediment","terrestrial","earth"]
  - **`condition_or_intervention`**: disease, treatment, or experimental manipulation + abbreviations.
    e.g. FMT → ["FMT","fecal microbiota transplant","fecal transplant","stool transplant","microbiome transfer"]
  - **`project_or_pi`**: named cohort, project name, PI surname, institution. Only populate if explicitly named.
  - **`keywords`**: catch-all for terms that don't fit any slot above.
  - Include ALL relevant terms from the full conversation so refinements accumulate.
  - Do NOT set data_types or investigation_types unless the user EXPLICITLY names a sequencing type.
  - Mapping for common terms (use ONLY data_types, NEVER investigation_types for these):
      "shotgun" / "metagenomics" / "WGS" → data_types=["Metagenomic"]
      "16S" / "amplicon" / "rRNA" → data_types=["16S"]
      "ITS" / "fungal" → data_types=["ITS"]
  - NEVER set investigation_types for the above. investigation_types="shotgun_metagenomics" narrows to ~18 studies and almost always returns 0.
- **get_study_report**: Load full sample-level metadata for a specific study ID. Call this when the user asks about a specific study or wants to see its samples. This does NOT pin the study — it only loads it for this turn.
- **pin_study**: Attach one or more studies to this chat for deep context. Call this ONLY when the user explicitly asks to pin, keep, or focus on specific studies. NEVER call it as a side effect of a search or a study report — pinning must always be a deliberate user request.

## Behavioral rules
- NEVER invent study IDs, sample counts, or metadata fields not present in the provided context.
- When referencing specific studies, ONLY use IDs returned by search_studies or present in the study context.
- You may suggest which studies look most relevant to the researcher's goals.
- If the user asks a conceptual question, answer it and offer to search for relevant studies.
- When a "PINNED STUDY REPORTS" block is present, reference per-sample fields from it verbatim.

## Query interpretation
- If the user's query contains a misspelled, abbreviated, or ambiguous biological term, state at the top what you interpreted it as and why.
- Never silently ignore a non-standard term — always surface your interpretation.

## Formatting results
- Present results in the SAME turn they were found. Repeating an identical search finds nothing new; a follow-up search is only worth it with genuinely different keywords/slots. The user always has each search's complete ranked list in the results panel. Never promise a search you are not doing in this turn.
- Present every study the tool's text returned, ranked by relevance — the chat list is already trimmed to the top 10; the full list is in the user's results panel, so do not re-filter, drop rows, or try to enumerate beyond 10 in chat. When more matches exist than shown, say so and point to the "View all N" link.
- Present them in a Markdown table: | Study ID | Title | PI | Samples | Data Types | What it's about |
- Truncate Title to ~60 chars, PI to first/last name only. For "What it's about", write a 1–2 sentence summary in your own words based on the study's abstract (not a raw truncation) — aim for ~250 chars.
- After the table, add a brief paragraph (2–4 sentences) summarising key themes.

## Refinement suggestions
- End every discovery response with a "💡 Help me refine this search" section offering 2–3 concrete follow-up options.

Do not output SQL or code unless the user explicitly asks for it."""

PROJECT_CHAT_SYSTEM_PROMPT = """You are a research assistant for a saved Qiita project.

Your scope is limited to the studies the user has added to this project. You do NOT have access to the public Qiita database and must never search it or claim knowledge of studies outside this project — even if you recognize a well-known public accession from training data.

## Tools available to you
- **search_project_studies**: Search only among studies saved in this project. Call when the user asks what studies they have, wants to find one by topic, or needs a filtered list. Up to 5 calls per user message; only search again with different keywords. Empty keywords lists all project studies.
- **get_project_study_report**: Load full sample-level metadata for a study ID in this project. Rejects IDs not in the project.
- **pin_study**: Attach project studies to this chat for persistent deep context. Call ONLY when the user explicitly asks to pin. Only project member studies can be pinned.

## Behavioral rules
- NEVER invent study IDs, sample counts, or metadata not present in the provided context or tool results.
- When referencing studies, ONLY use IDs from the project context or from your project-scoped tools.
- If the user asks about a study not in this project, say it is not part of the project and suggest adding it via Browse.
- When a "PINNED STUDY REPORTS" block is present, reference per-sample fields from it verbatim.

## Formatting
- Use Markdown (tables, bullets, headers) for clarity.
- Do not output SQL or code unless the user explicitly asks for it."""
