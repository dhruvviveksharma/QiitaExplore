"""OpenAI function-calling schemas for agent tools."""

_PIN_STUDY_PARAMETERS = {
    "type": "object",
    "properties": {
        "study_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "List of Qiita study IDs to pin.",
        },
    },
    "required": ["study_ids"],
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_studies",
            "description": (
                "Search the Qiita public microbiome database for studies. "
                "Up to 5 calls per user message — start with one well-filled call and only "
                "search again with meaningfully different keywords when results are thin. "
                "Fill every typed slot you can identify from the query with ALL synonyms for that concept. "
                "The backend pools all slots into one ranked search, so filling generously never over-narrows. "
                "Include ALL relevant terms from the full conversation so refinements accumulate. "
                "Only set data_types/investigation_types when the user EXPLICITLY names a sequencing type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "organism": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Host or focal organism. Generate ALL known synonyms: common names, "
                            "Latin binomials, strains, related genera, plural + singular. "
                            "e.g. mouse → [\"mouse\",\"mice\",\"murine\",\"Mus musculus\","
                            "\"house mouse\",\"field mouse\",\"wood mouse\",\"deer mouse\","
                            "\"C57BL/6\",\"BALB/c\",\"Apodemus\",\"Peromyscus\",\"rodent\",\"rodents\"]"
                        ),
                    },
                    "qualifier": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Condition, status, or context modifiers: wild vs captive, diseased vs healthy, "
                            "treated vs control, life stage, diet. Include all synonyms and compound forms. "
                            "e.g. wild → [\"wild\",\"wild animal\",\"wild animals\",\"wild-caught\","
                            "\"feral\",\"feral mice\",\"free-living\",\"wildlife\",\"non-captive\","
                            "\"natural habitat\",\"wild mice\",\"wild mouse\",\"wild rodent\"]"
                        ),
                    },
                    "body_site": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Anatomical location or environmental niche. Include ontology synonyms. "
                            "e.g. gut → [\"gut\",\"intestine\",\"gastrointestinal\",\"GI tract\","
                            "\"cecum\",\"ileum\",\"jejunum\",\"feces\",\"stool\",\"fecal\",\"host-associated\"]. "
                            "e.g. soil → [\"soil\",\"rhizosphere\",\"sediment\",\"terrestrial\",\"earth\"]"
                        ),
                    },
                    "condition_or_intervention": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Disease, treatment, or experimental manipulation. Include abbreviations. "
                            "e.g. antibiotic → [\"antibiotic\",\"antibiotics\",\"antimicrobial\","
                            "\"ciprofloxacin\",\"vancomycin\",\"dysbiosis\",\"perturbation\"]. "
                            "e.g. FMT → [\"FMT\",\"fecal microbiota transplant\",\"fecal transplant\","
                            "\"stool transplant\",\"microbiome transfer\"]"
                        ),
                    },
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "type": {
                                    "enum": ["pi", "project", "cohort", "institution", "unknown"],
                                },
                            },
                        },
                        "description": (
                            "Named people/groups the user explicitly mentioned. Populate ONLY if "
                            "the user explicitly names one. type='pi' for a person "
                            "(e.g. {\"text\": \"Jeff Gordon\", \"type\": \"pi\"}); type='project'/"
                            "'cohort'/'institution' for named studies/consortia/orgs "
                            "(e.g. {\"text\": \"American Gut Project\", \"type\": \"project\"}). "
                            "Only type='pi' triggers a hard PI filter after DB resolution; "
                            "other types are keyword-scored only."
                        ),
                    },
                    "project_or_pi": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Deprecated — prefer entities. Named cohort, project, PI, or institution."
                        ),
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Catch-all for terms that don't fit the typed slots above, "
                            "or for plain keyword searches without clear biological dimensions. "
                            "Also used for backward-compatible flat keyword lists."
                        ),
                    },
                    "data_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "AND filter — only set when user EXPLICITLY names a sequencing type. "
                            "Valid: '16S', '18S', 'ITS', 'Metagenomic', 'Metatranscriptomic', "
                            "'Metabolomic', 'Proteomic', 'Multiomic', 'Genome Isolate', 'Full Length Operon'. "
                            "Use 'Metagenomic' for shotgun/WGS. Omit for plain topic queries."
                        ),
                    },
                    "investigation_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Extremely narrow sub-filter (~18 studies). OMIT for common terms — "
                            "use data_types=['Metagenomic'] for shotgun/WGS instead."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "AND filter on curation tags (qiita.per_study_tags) — only set when "
                            "the user explicitly asks for a specific tag. The only confirmed value "
                            "in this database today is 'GOLD' (curated high-quality studies — e.g. "
                            "\"gold studies\", \"gold-tagged\"). Omit otherwise; do not guess other "
                            "tag values."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "How many studies to feature in the chat reply (1–10, "
                            "default 10). The complete ranked list of every match is "
                            "always shown to the user separately in the results panel — "
                            "a larger limit is not a way to get more results."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_study_report",
            "description": (
                "Load full sample-level metadata for a specific Qiita study. "
                "Shows all samples with their metadata fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "study_id": {
                        "type": "integer",
                        "description": "The Qiita study ID to fetch.",
                    },
                },
                "required": ["study_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_study",
            "description": (
                "Attach one or more studies to this chat for persistent deep context. "
                "Pinned studies are loaded in full on each message. Cap: 10 studies."
            ),
            "parameters": _PIN_STUDY_PARAMETERS,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_sample",
            "description": (
                "Search for studies where samples match specific metadata attributes. "
                "Use this when the user asks about subject characteristics: body site, disease, "
                "age, sex, BMI, host organism, tissue type, or any sample-level metadata field. "
                "Different from search_studies which searches study-level titles and abstracts — "
                "this searches the actual recorded sample records."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "field_filters": {
                        "type": "array",
                        "description": (
                            "Specific field-value pairs to match in sample metadata. "
                            "e.g. [{\"field\":\"disease\",\"value\":\"IBD\"},"
                            "{\"field\":\"body_site\",\"value\":\"rectum\"}]. "
                            "Common fields: disease, body_site, env_package, host_sex, "
                            "host_age, host_bmi, tissue_type, treatment."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["field", "value"],
                        },
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Free-text terms matched across all sample metadata fields.",
                    },
                    "data_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict to studies of these data types (e.g. '16S', 'Metagenomic').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max studies to return (default 8, max 20).",
                    },
                },
                "required": [],
            },
        },
    },
]

PROJECT_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_project_studies",
            "description": (
                "Search studies saved in this workspace only. "
                "Up to 5 calls per user message — only search again with different keywords. "
                "Empty keywords lists all workspace studies. "
                "You cannot search the public Qiita database from a workspace chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Terms matched against title, abstract, PI, data types, and summary.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max studies to return (1–20, default 10).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_study_report",
            "description": (
                "Load full sample-level metadata for a study in this workspace. "
                "Rejects study IDs not currently saved in the workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "study_id": {
                        "type": "integer",
                        "description": "The Qiita study ID (must be in this workspace).",
                    },
                },
                "required": ["study_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_study",
            "description": (
                "Attach one or more studies from this workspace to the chat for persistent deep context. "
                "Only studies currently saved in the workspace can be pinned. Cap: 10 studies."
            ),
            "parameters": _PIN_STUDY_PARAMETERS,
        },
    },
]


# ── Study detail tools (helpers/study_tools.py) — both chat scopes ───────────
# Each one is shown to the user as an interactive widget in the reply.

def _fn(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


_STUDY_ID = {"type": "integer", "description": "The Qiita study ID."}

STUDY_TOOL_SCHEMAS = [
    _fn("get_study_preps",
        "Show a study's prep templates (one per sequencing run) with their data types, sample counts, "
        "platform and status. The user sees an interactive table; clicking a prep shows its processing "
        "graph. Use for questions about a study's preps, data types or sequencing.",
        {"study_id": _STUDY_ID,
         "data_type": {"type": "string", "description": "Only preps of this data type, e.g. '16S', 'Metagenomic'."}},
        ["study_id"]),
    _fn("show_study_samples",
        "Show a study's samples to the user as a list with each clicked sample's metadata beside it. "
        "You get the count, the first sample ids and the metadata column names — not the values "
        "(use get_sample_metadata for one sample, or the study report tool to read values across samples).",
        {"study_id": _STUDY_ID,
         "prep_id": {"type": "integer", "description": "Only the samples of this prep."},
         "data_type": {"type": "string", "description": "Only samples in preps of this data type."}},
        ["study_id"]),
    _fn("get_sample_metadata",
        "All metadata fields of one sample of a study, and which preps it is in.",
        {"study_id": _STUDY_ID,
         "sample_id": {"type": "string", "description": "The sample id, e.g. '10317.000001002'."}},
        ["study_id", "sample_id"]),
    _fn("get_prep_graph",
        "Show the processing graph of one prep, like Qiita's chart: raw files -> demultiplexed -> "
        "processed -> BIOM tables, with the processing jobs between them. The user can click any node "
        "for its files or job parameters. You get an outline of the artifacts and steps.",
        {"study_id": _STUDY_ID,
         "prep_id": {"type": "integer", "description": "The prep (default: the study's first prep). "
                                                       "An artifact id shows that artifact's prep."}},
        ["study_id"]),
    _fn("list_artifact_files",
        "List the files of one artifact, or of every artifact of a prep: BIOM tables, QZA/QZV, logs, HTML, "
        "FASTQ, folders. For public artifacts you get each file's FULL server path; give paths exactly as "
        "returned, never edit or build one (a private artifact's paths are not shared). No file is opened. "
        "The user also sees the paths and download links in the widget.",
        {"study_id": _STUDY_ID,
         "artifact_id": {"type": "integer", "description": "One artifact."},
         "prep_id": {"type": "integer", "description": "Every artifact of this prep (default: the first prep)."}},
        ["study_id"]),
    _fn("add_to_chat_aggregation",
        "Add a study - or only some of its data types or preps - to THIS CHAT's temporary sample aggregation "
        "(created on first use; it exports per-sample FASTQ file lists). It is added immediately: the user "
        "sees what was added with an Undo button, and can export it or save it as a named aggregation. Use it "
        "whenever the user wants to collect, gather, add or aggregate studies, preps or samples in this chat, "
        "or to create an aggregation.",
        {"study_id": _STUDY_ID,
         "data_types": {"type": "array", "items": {"type": "string"},
                        "description": "Only these data types, e.g. ['16S']."},
         "prep_ids": {"type": "array", "items": {"type": "integer"}, "description": "Only these preps."}},
        ["study_id"]),
    _fn("save_chat_aggregation",
        "Save this chat's temporary aggregation under a name: it moves to the Sample Aggregation tab and "
        "is kept after the chat is deleted. Only when the user asks to save, keep or name it.",
        {"name": {"type": "string", "description": "The name for the saved aggregation."}},
        ["name"]),
    _fn("list_aggregations",
        "List the user's saved sample aggregations and this chat's temporary one: each one's studies, "
        "checked file rows and per-study filter. Use it when the user asks what aggregations they have or "
        "what is in one.",
        {"name": {"type": "string", "description": "Only the saved aggregation with this name."}},
        []),
    _fn("propose_aggregation_add",
        "Propose adding a study - or only some of its data types or preps - to one of the user's SAVED "
        "aggregations that they name. This adds NOTHING: the user sees a card with the scope and counts, "
        "picks the aggregation and clicks Add. Never say the study was added. When no saved aggregation is "
        "named, use add_to_chat_aggregation instead.",
        {"study_id": _STUDY_ID,
         "data_types": {"type": "array", "items": {"type": "string"},
                        "description": "Only these data types, e.g. ['16S']."},
         "prep_ids": {"type": "array", "items": {"type": "integer"}, "description": "Only these preps."},
         "aggregation_name": {"type": "string",
                              "description": "The aggregation the user named, if any (a new one if none has this name)."}},
        ["study_id"]),
    _fn("add_to_workspace",
        "Add one or more studies to one of the user's workspaces (the sidebar's saved study sets). It is "
        "added immediately; the user sees a card with Undo. Name the workspace as the user did: a name that "
        "matches no workspace creates it. In a workspace chat with no workspace named, the current one is "
        "used. Use this - never an aggregation - when the user wants studies in a workspace.",
        {"study_ids": {"type": "array", "items": {"type": "integer"},
                       "description": "The Qiita study ids (at most 10)."},
         "workspace": {"type": "string", "description": "The workspace name the user gave."}},
        ["study_ids"]),
    _fn("create_workspace",
        "Create a new workspace with this exact name, optionally with studies in it. An existing workspace "
        "with that name is reused, never duplicated. The user sees a card with Undo.",
        {"name": {"type": "string", "description": "The new workspace's name."},
         "study_ids": {"type": "array", "items": {"type": "integer"},
                       "description": "Studies to put in it (at most 10)."}},
        ["name"]),
    _fn("export_table",
        "Make a CSV or TSV file the user downloads from a card in the chat. Sources: 'samples' (a study's "
        "sample metadata: all columns or `columns`, optionally one prep or data type), 'files' (a study's "
        "public artifact files with full paths, filterable by prep, data type, artifact type such as BIOM, "
        "or file type such as biom or qza), 'aggregation' (the export of this chat's aggregation, or a "
        "saved one named in `aggregation`), or 'rows' (a table you write from tool results: `columns` and "
        "`rows`). Only when the user clearly wants a file; if they might mean adding to an aggregation, "
        "ask first.",
        {"source": {"type": "string", "enum": ["samples", "files", "aggregation", "rows"]},
         "format": {"type": "string", "enum": ["csv", "tsv"], "description": "Default csv."},
         "name": {"type": "string", "description": "A short file name, without extension."},
         "study_id": _STUDY_ID,
         "prep_id": {"type": "integer", "description": "samples / files: only this prep."},
         "data_type": {"type": "string", "description": "samples / files: only this data type, e.g. '16S'."},
         "columns": {"type": "array", "items": {"type": "string"},
                     "description": "samples: the metadata columns to include (default all). "
                                    "rows: the column names."},
         "artifact_type": {"type": "string", "description": "files: only this artifact type, e.g. 'BIOM'."},
         "file_type": {"type": "string", "description": "files: only this file type, e.g. 'biom', 'qza'."},
         "aggregation": {"type": "string", "description": "aggregation: a saved aggregation's name "
                                                          "(default this chat's)."},
         "rows": {"type": "array", "items": {"type": "array", "items": {}},
                  "description": "rows: the table's rows, each a list of cells in column order "
                                 "(at most 2,000)."}},
        ["source"]),
    _fn("resolve_study",
        "Find which study the user means when they name it in words (title, acronym such as 'AGP', PI) "
        "rather than by id. Call it before the study tools when the id isn't already settled in this "
        "conversation. If it reports ambiguity, stop: the user is shown candidates to pick from.",
        {"text": {"type": "string", "description": "The user's words that name the study."},
         "for_tool": {"type": "string", "description": "The study tool you will call next.",
                      "enum": ["get_study_preps", "show_study_samples", "get_sample_metadata",
                               "get_prep_graph", "list_artifact_files", "add_to_chat_aggregation",
                               "propose_aggregation_add", "add_to_workspace", "export_table"]}},
        ["text"]),
]

STUDY_TOOL_NAMES = frozenset(t["function"]["name"] for t in STUDY_TOOL_SCHEMAS)

TOOL_SCHEMAS += STUDY_TOOL_SCHEMAS
PROJECT_TOOL_SCHEMAS += STUDY_TOOL_SCHEMAS
