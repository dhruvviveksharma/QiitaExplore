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
                "Issue EXACTLY ONE call per user request — never multiple calls with different filters. "
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
                            "e.g. gut → [\"gut\",\"intestine\",\"colon\",\"gastrointestinal\",\"GI tract\","
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
                "Search studies saved in this project only. "
                "Issue EXACTLY ONE call per user request. "
                "Empty keywords lists all project studies. "
                "You cannot search the public Qiita database from project chat."
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
                "Load full sample-level metadata for a study in this project. "
                "Rejects study IDs not currently saved in the project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "study_id": {
                        "type": "integer",
                        "description": "The Qiita study ID (must be in this project).",
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
                "Attach one or more studies from this project to the chat for persistent deep context. "
                "Only studies currently saved in the project can be pinned. Cap: 10 studies."
            ),
            "parameters": _PIN_STUDY_PARAMETERS,
        },
    },
]
