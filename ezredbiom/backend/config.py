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
anthropic_client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=300.0)

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
    "gemma-small":       {"provider": "nrp",       "tier": "evaluating", "size": "~8B",  "context": 131_072,   "modalities": "image, video, audio", "supports_tools": False},
    "kimi":              {"provider": "nrp",       "tier": "evaluating", "size": "1T",   "context": 262_144,   "modalities": "image, video",        "supports_tools": True},
    "glm-5":             {"provider": "nrp",       "tier": "evaluating", "size": "744B", "context": 202_752,   "modalities": "—",                   "supports_tools": True},
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


PROJECT_SUMMARY_GEN_LIMIT       = int(os.getenv("PROJECT_SUMMARY_GEN_LIMIT", "5"))
GLOBAL_SEARCH_SQL_LIMIT_BROAD   = int(os.getenv("GLOBAL_SEARCH_SQL_LIMIT_BROAD", "120"))
GLOBAL_SEARCH_SQL_LIMIT_NARROW  = int(os.getenv("GLOBAL_SEARCH_SQL_LIMIT_NARROW", "50"))
SAMPLE_SEARCH_DEFAULT_CANDIDATES = int(os.getenv("SAMPLE_SEARCH_DEFAULT_CANDIDATES", "40"))
SAMPLE_SEARCH_DEEP_CANDIDATES    = int(os.getenv("SAMPLE_SEARCH_DEEP_CANDIDATES",    "500"))
SAMPLE_SEARCH_PROBE_TIMEOUT_MS   = int(os.getenv("SAMPLE_SEARCH_PROBE_TIMEOUT_MS",   "8000"))
REPORT_SAMPLE_LIMIT             = 200
PINNED_REPORT_CONTEXT_MAX_CHARS = int(os.getenv("PINNED_REPORT_CONTEXT_MAX_CHARS", "40000"))
PINNED_REPORT_MIN_PER_STUDY     = int(os.getenv("PINNED_REPORT_MIN_PER_STUDY", "2000"))

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

GLOBAL_CHAT_SYSTEM_PROMPT = """You are a discovery assistant for the Qiita microbiome database.

Your primary goal is to help researchers find studies from the entire Qiita database that match their scientific criteria.

## Tools available to you
You have the following tools. Call them as needed — do not wait for the user to invoke them explicitly.
- **search_studies**: Search Qiita for public studies. Call this whenever the user asks to find, discover, or filter studies.
  - Issue EXACTLY ONE call per user request. NEVER fire multiple calls in one turn.
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
- **get_study_report**: Load full sample-level metadata for a specific study ID. Call this when the user asks about a specific study or wants to see its samples.
- **pin_study**: Attach one or more studies to this chat for deep context. Call this when the user says they want to keep a study or focus on specific IDs.
- **compute_diversity**: Compute alpha/beta diversity metrics. (Currently unavailable — BIOM ingestion is pending.)

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
- From all retrieved studies, SELECT the TOP 20 most relevant and rank them by relevance.
- Present them in a Markdown table: | Study ID | Title | PI | Samples | Data Types | Abstract |
- Truncate Title to ~60 chars, Abstract to ~150 chars, PI to first/last name only.
- After the table, add a brief paragraph (2–4 sentences) summarising key themes.

## Refinement suggestions
- End every discovery response with a "💡 Help me refine this search" section offering 2–3 concrete follow-up options.

Do not output SQL or code unless the user explicitly asks for it."""
