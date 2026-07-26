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

DEFAULT_MODEL  = "gemma"
ALLOWED_MODELS = {
    "qwen3", "qwen3-small", "gpt-oss",
    "gemma", "gemma-small",
    "kimi", "glm-5", "minimax-m2",
    "claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8",
}

MODEL_METADATA = {
    "qwen3":             {"provider": "nrp",       "tier": "main",       "size": "397B", "context": 1_010_000, "modalities": "image, video",        "supports_tools": True},
    "qwen3-small":       {"provider": "nrp",       "tier": "main",       "size": "27B",  "context": 1_010_000, "modalities": "image, video",        "supports_tools": True},
    "gpt-oss":           {"provider": "nrp",       "tier": "main",       "size": "120B", "context": 131_072,   "modalities": "—",                   "supports_tools": True},
    "gemma":             {"provider": "nrp",       "tier": "main",       "size": "31B",  "context": 262_144,   "modalities": "image, video",        "supports_tools": True},
    "gemma-small":       {"provider": "nrp",       "tier": "evaluating", "size": "12B",  "context": 262_144,   "modalities": "image, video, audio", "supports_tools": True},
    "kimi":              {"provider": "nrp",       "tier": "evaluating", "size": "1T",   "context": 262_144,   "modalities": "image, video",        "supports_tools": True},
    "glm-5":             {"provider": "nrp",       "tier": "evaluating", "size": "744B", "context": 524_288,   "modalities": "—",                   "supports_tools": True},
    "minimax-m2":        {"provider": "nrp",       "tier": "evaluating", "size": "230B", "context": 204_800,   "modalities": "—",                   "supports_tools": True},
    "claude-haiku-4-5":  {"provider": "anthropic", "tier": "main",       "size": "—",    "context": 200_000,   "modalities": "image",               "supports_tools": True},
    "claude-sonnet-4-6": {"provider": "anthropic", "tier": "main",       "size": "—",    "context": 200_000,   "modalities": "image",               "supports_tools": True},
    "claude-opus-4-8":   {"provider": "anthropic", "tier": "evaluating", "size": "—",    "context": 200_000,   "modalities": "image",               "supports_tools": True},
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


def model_supports_tools(model: str) -> bool:
    return MODEL_METADATA.get(model or DEFAULT_MODEL, {}).get("supports_tools", False)


def context_budget_chars(model: str) -> int:
    ctx_tokens = MODEL_METADATA.get(model or DEFAULT_MODEL, MODEL_METADATA[DEFAULT_MODEL])["context"]
    chars = int((ctx_tokens - 8_000) * 3.5)
    return max(8_000, chars)


GLOBAL_SEARCH_SQL_LIMIT_BROAD   = int(os.getenv("GLOBAL_SEARCH_SQL_LIMIT_BROAD", "120"))
GLOBAL_SEARCH_SQL_LIMIT_NARROW  = int(os.getenv("GLOBAL_SEARCH_SQL_LIMIT_NARROW", "50"))
SAMPLE_SEARCH_DEFAULT_CANDIDATES = int(os.getenv("SAMPLE_SEARCH_DEFAULT_CANDIDATES", "40"))
SAMPLE_SEARCH_DEEP_CANDIDATES    = int(os.getenv("SAMPLE_SEARCH_DEEP_CANDIDATES",    "500"))
SAMPLE_SEARCH_PROBE_TIMEOUT_MS   = int(os.getenv("SAMPLE_SEARCH_PROBE_TIMEOUT_MS",   "8000"))
PG_POOL_MIN_CONN                = int(os.getenv("PG_POOL_MIN_CONN", "2"))
PG_POOL_MAX_CONN                = int(os.getenv("PG_POOL_MAX_CONN", "8"))
REPORT_SAMPLE_LIMIT             = 200
PINNED_REPORT_CONTEXT_MAX_CHARS = int(os.getenv("PINNED_REPORT_CONTEXT_MAX_CHARS", "40000"))
PINNED_REPORT_MIN_PER_STUDY     = int(os.getenv("PINNED_REPORT_MIN_PER_STUDY", "2000"))

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

# Fernet key encrypting PATs at rest in auth_sessions.pat_encrypted. Required —
# there is no insecure fallback; helpers/pat_crypto.py raises loudly if unset.
# Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
PAT_ENCRYPTION_KEY = os.getenv("QIITA_EXPLORE_PAT_ENCRYPTION_KEY")

AUTH_PAT_REVERIFY_INTERVAL_SECONDS = int(os.getenv("AUTH_PAT_REVERIFY_INTERVAL_SECONDS", str(15 * 60)))
AUTH_SESSION_ABSOLUTE_TTL_SECONDS  = int(os.getenv("AUTH_SESSION_ABSOLUTE_TTL_SECONDS", str(30 * 24 * 3600)))
AUTH_SESSION_IDLE_TTL_SECONDS      = int(os.getenv("AUTH_SESSION_IDLE_TTL_SECONDS", str(7 * 24 * 3600)))

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

# ── pi sidecar (agentic chat backend) ───────────────────────────────────────
# The localhost default is a DEV convenience only. In the deployed topology the
# sidecar runs on the intermediate node and Flask runs on barnacle, so this is a
# genuine cross-machine hop — set it explicitly there. Nothing in the internal
# tool surface may assume a loopback caller (see PI_ALLOWED_TOOL_CALLERS).
PI_SIDECAR_URL = os.getenv("PI_SIDECAR_URL", "http://127.0.0.1:5100").rstrip("/")

# Shared secret the sidecar must present (X-Pi-Secret) on EVERY internal tool
# request — schema reads and tool calls alike. No insecure fallback:
# helpers/scope_token.py and internal_tool_routes.py raise/401 loudly if unset,
# same posture as PAT_ENCRYPTION_KEY above.
PI_SIDECAR_SECRET = os.getenv("PI_SIDECAR_SECRET")

# Source-IP allowlist for /api/internal/tools/*. Comma-separated exact remote
# addresses (the intermediate node). Unset = no IP restriction, which is right
# for local dev but should always be set in deployment: the shared secret and
# the scope token are the primary gates, this is the third one that makes a
# leaked secret alone insufficient from an arbitrary host.
PI_ALLOWED_TOOL_CALLERS = [
    h.strip() for h in os.getenv("PI_ALLOWED_TOOL_CALLERS", "").split(",") if h.strip()
]

# Signs the short-lived per-turn scope token (helpers/scope_token.py) that
# authorizes the sidecar's tool calls back into Flask. This — not the sidecar
# — is what makes project-chat scoping a hard boundary rather than a prompt
# request.
PI_SCOPE_TOKEN_KEY = os.getenv("PI_SCOPE_TOKEN_KEY")

PI_SCOPE_TOKEN_TTL_SECONDS = int(os.getenv("PI_SCOPE_TOKEN_TTL_SECONDS", "600"))

# Per-chat-type flags. pi is now the DEFAULT runtime for both; the legacy
# Python paths (helpers/agent.py :: stream_agent for global, llm_chat_stream for
# project) remain reachable by setting either of these false, as a rollback that
# needs no deploy.
#
# Because the default flipped, the legacy paths are no longer exercised by
# anything in normal use and will drift. They are kept deliberately, not because
# they are believed correct — re-verify before relying on one.
def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


PI_BACKEND_GLOBAL = _flag("PI_BACKEND_GLOBAL")
PI_BACKEND_PROJECT = _flag("PI_BACKEND_PROJECT")


def pi_config_errors() -> list:
    """Config that is only optional while pi is off. Returns human-readable
    problems; empty list means the active configuration can actually serve chat.

    Exists because the failure mode is otherwise invisible at boot and awful at
    runtime: with pi as the default path and PI_SIDECAR_SECRET unset, Flask
    starts clean, the UI loads, and every single chat turn dies on a 401 from
    internal_tool_routes._guard() — which reads as "the chat is broken", not as
    "a secret is missing"."""
    if not (PI_BACKEND_GLOBAL or PI_BACKEND_PROJECT):
        return []
    problems = []
    if not PI_SIDECAR_SECRET:
        problems.append(
            "PI_SIDECAR_SECRET is unset — the sidecar cannot authenticate to "
            "/api/internal/tools/*, so every tool call would 401."
        )
    if not PI_SCOPE_TOKEN_KEY:
        problems.append(
            "PI_SCOPE_TOKEN_KEY is unset — per-turn scope tokens cannot be "
            "signed, so no tool call can be authorized."
        )
    # PI_SIDECAR_URL defaults to the sidecar's own default port. If one of them
    # was overridden and the other was not, Flask posts turns into a closed
    # port and every chat fails with a connection error.
    sidecar_port = os.getenv("PI_SIDECAR_PORT")
    if sidecar_port and not os.getenv("PI_SIDECAR_URL"):
        problems.append(
            f"PI_SIDECAR_PORT={sidecar_port} is set but PI_SIDECAR_URL is not — "
            f"Flask would still post turns to {PI_SIDECAR_URL}."
        )
    return problems

# Temporary debugging aid: when true, an unexpected exception in
# POST /auth/connect includes the exception type/message in the JSON
# response, not just the server log. Off by default — only enable while
# actively diagnosing a deployment issue, never leave on in real use.
DEBUG_ERROR_DETAIL = os.getenv("QIITA_EXPLORE_DEBUG_ERRORS", "false").strip().lower() in ("1", "true", "yes")

CHAT_SYSTEM_PROMPT = """You are a helpful assistant for researchers using the Qiita microbiome database.

Your primary goals:
- Help users reason about microbiome concepts, analysis strategies, and how to use Qiita.
- NEVER invent specific Qiita study IDs, titles, sample counts, metadata fields, or publication details.
- When you mention specific studies, ONLY use the study IDs and titles that are explicitly provided to you in the project context.

Behavioral rules:
- If the user asks about a study that is not present in the provided context, say that you do not have that study's details and suggest using the Qiita search interface in this app.
- NEVER list "available studies" unless they are explicitly present in the provided study context for this request.
- If no study context is provided, explicitly say that no studies are currently loaded in chat context and ask the user to use search/select studies.
- NEVER invent external accession IDs (for example PRJEB/PRJNA) or claim database records that were not provided in context.
- When a study context includes metadata fields (for example abstract, PI name, affiliation, lab contact), use them directly to answer user questions and study overviews.
- If a requested field is missing in context, explicitly say it is unavailable instead of guessing.
- If you are unsure about any factual detail, clearly say you are unsure instead of guessing.
- It is always acceptable to answer at a high-level (conceptual explanation) without naming specific studies.
- If the user asks about obviously out-of-domain or fictional entities, make it clear that these are not Qiita studies and do NOT fabricate any matching study records.
- When a "PINNED STUDY REPORTS" block is present, you may reference per-sample fields from it verbatim. For cross-study comparisons, only compare studies that appear in pinned reports or in the study context.

When answering:
- Prefer concise, technically accurate explanations.
- Format all responses using Markdown (bold, bullets, code blocks, headers where appropriate).
- Do not output SQL or code unless the user explicitly asks for it."""

PROJECT_CHAT_AGENT_SYSTEM_PROMPT = """You are a research assistant for a specific project workspace in the Qiita microbiome database.

Your primary goal is to help the researcher work with the studies in THIS PROJECT'S WORKSPACE — not the whole Qiita database. A short list of the workspace's current studies is provided as part of your context each turn.

## Hard boundary — read this carefully
Every tool call you make is scoped server-side to this project's workspace. You CANNOT retrieve, search, or pin studies outside it, even if you know a study ID exists in Qiita generally — the tools will refuse and tell you so. Do not treat a refusal as an error to route around; it means the study genuinely isn't part of this workspace. Tell the user plainly and suggest they add it to the project (or ask in a global/discovery chat) if they want it.

## Tools available to you
- **search_studies**: Search the studies already in this workspace. Call this when the user asks to find or filter among the project's own studies. Issue EXACTLY ONE call per user request.
  - Same typed dimension slots as elsewhere (organism, qualifier, body_site, condition_or_intervention, project_or_pi, keywords, data_types, investigation_types) — fill every slot you can identify.
  - Results are ranked among workspace studies only — this is not a database-wide search.
- **search_by_sample**: Search the workspace's studies by sample-level metadata (body site, disease, host organism, etc.), scoped the same way.
- **get_study_report**: Load full sample-level metadata for a specific study ID — but ONLY if that study is in this workspace.
- **pin_study**: Attach a workspace study to this chat for persistent deep context. Only call this when the user explicitly asks to pin, keep, or focus on specific studies — never as a side effect of a search or report.

## Behavioral rules
- NEVER invent study IDs, sample counts, or metadata fields not present in the provided workspace context or a tool result.
- When referencing specific studies, ONLY use IDs that are in the workspace list, returned by search_studies/search_by_sample, or present in a pinned report.
- If the user asks about a study not in this workspace, say so plainly rather than guessing or fabricating an answer.
- When a "PINNED STUDY REPORTS" block is present, reference per-sample fields from it verbatim.
- It is always acceptable to answer conceptual microbiome/analysis questions at a high level without naming specific studies.

## Formatting
- Prefer concise, technically accurate explanations. Format responses using Markdown.
- Do not output SQL or code unless the user explicitly asks for it."""

GLOBAL_CHAT_SYSTEM_PROMPT = """You are a discovery assistant for the Qiita microbiome database.

Your primary goal is to help researchers find studies from the entire Qiita database that match their scientific criteria.

## Tools available to you
You have the following tools. Call them as needed — do not wait for the user to invoke them explicitly.
- **search_studies**: Search Qiita for public studies. Call this whenever the user asks to find, discover, or filter studies.
  - Issue EXACTLY ONE call per user request. NEVER fire multiple calls in one turn.
  - Set `limit` to the number of studies the user asked for (e.g. "find me 10 studies" → limit=10). If they didn't specify a count, use 10.
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
- Present results in the SAME turn that search_studies returns them. NEVER chain a second search before showing the user what the first one found — if the results look thin, present them first, THEN offer a refined search as a follow-up suggestion.
- Present every study the tool returned, ranked by relevance — the tool has already trimmed to `limit`, so do not re-filter or drop rows.
- Present them in a Markdown table: | Study ID | Title | PI | Samples | Data Types | What it's about |
- Truncate Title to ~60 chars, PI to first/last name only. For "What it's about", write a 1–2 sentence summary in your own words based on the study's abstract (not a raw truncation) — aim for ~250 chars.
- After the table, add a brief paragraph (2–4 sentences) summarising key themes.

## Refinement suggestions
- End every discovery response with a "💡 Help me refine this search" section offering 2–3 concrete follow-up options.

Do not output SQL or code unless the user explicitly asks for it."""
