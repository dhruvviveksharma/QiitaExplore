"""Auto-pick BIOM artifact per study and validate namespace compatibility for merging."""

import json


def autopick_artifact(artifacts: list, data_type: str):
    """Return the best BIOM artifact for merging given a study's artifact list.

    Heuristic (per Jonathan K.):
    - 16S: prefer deblur + 150bp (score 2), else deblur only (score 1)
    - Metagenomic: prefer artifact whose path/name contains 'biom_final'
    - All others: most recent generated_timestamp
    """
    bioms = [a for a in artifacts if (a.get("artifact_type") or "").upper() == "BIOM"]
    if not bioms:
        return None

    dtype = (data_type or "").strip().lower()

    def _ts(a):
        return a.get("generated_timestamp") or ""

    if "16s" in dtype:
        def _score(a):
            name = ((a.get("prep_name") or "") + (a.get("full_path") or "")).lower()
            return (2 if ("deblur" in name and "150" in name)
                    else 1 if "deblur" in name
                    else 0)
        return max(bioms, key=lambda a: (_score(a), _ts(a)))

    if "metagenomic" in dtype or "wgs" in dtype:
        def _is_final(a):
            name = ((a.get("prep_name") or "") + (a.get("full_path") or "")).lower()
            return 1 if "biom_final" in name else 0
        return max(bioms, key=lambda a: (_is_final(a), _ts(a)))

    return max(bioms, key=_ts)


# Feature namespaces that are mutually incompatible for merging
_NS_GROUPS = {
    "16s": "16S",
    "18s": "18S",
    "its": "ITS",
    "its1": "ITS",
    "its2": "ITS",
    "metagenomic": "Metagenomic",
    "wgs": "Metagenomic",
    "metatranscriptomic": "Metatranscriptomic",
}

def _namespace(data_type: str) -> str:
    dt = (data_type or "").strip().lower()
    return _NS_GROUPS.get(dt, data_type or "Unknown")


def studies_type_intersection(data_types_strings: list) -> str:
    """Return canonical namespace common to all studies, or '' if no intersection.

    Args:
        data_types_strings: one comma-sep data_types string per study
            (e.g. ["16S, ITS", "ITS", "ITS, Metagenomic"])
    """
    sets = []
    for dt_str in data_types_strings:
        ns_set = {_namespace(t.strip()) for t in (dt_str or "").split(",") if t.strip()}
        ns_set.discard("Unknown")
        if ns_set:
            sets.append(ns_set)
    if not sets:
        return ""
    intersection = sets[0]
    for s in sets[1:]:
        intersection &= s
    return next(iter(intersection), "")


def autopick_reason(artifact: dict, data_type: str) -> str:
    """Return a human-readable explanation for why this artifact was auto-picked."""
    if not artifact:
        return ""
    dtype = (data_type or "").strip().lower()
    name = ((artifact.get("prep_name") or "") + (artifact.get("full_path") or "")).lower()
    if "16s" in dtype:
        if "deblur" in name and "150" in name:
            return "Deblur + 150 bp (preferred for 16S)"
        if "deblur" in name:
            return "Deblur (preferred for 16S)"
        return "Most recently processed"
    if "metagenomic" in dtype or "wgs" in dtype:
        if "biom_final" in name:
            return "Final BIOM (preferred for metagenomic)"
        return "Most recently processed"
    return "Most recently processed"


def check_namespace_compatibility(studies_with_artifacts: list,
                                   explicit_only: bool = False) -> dict:
    """Validate that studies can be meaningfully merged.

    Args:
        studies_with_artifacts: list of dicts with keys:
            study_id (int), artifact (dict), sample_ids (list|None),
            is_chosen (bool, optional)
        explicit_only: when True, namespace check only covers entries where
            is_chosen=True (used during validate; autopicked artifacts are
            excluded since the user hasn't committed to them yet)

    Returns dict with keys: compatible (bool), namespace_groups (dict),
        warnings (list[str]), errors (list[str])
    """
    warnings = []
    errors = []
    namespace_groups: dict[str, list] = {}

    ns_entries = (
        [e for e in studies_with_artifacts if e.get("is_chosen")]
        if explicit_only
        else studies_with_artifacts
    )
    for entry in ns_entries:
        sid = entry["study_id"]
        artifact = entry.get("artifact") or {}
        ns = _namespace(artifact.get("data_type") or "")
        namespace_groups.setdefault(ns, []).append(sid)

    if len(namespace_groups) > 1:
        ns_list = ", ".join(sorted(namespace_groups.keys()))
        errors.append(f"Selected BIOMs are of different data types: {ns_list}. "
                       "All selected BIOMs must be of the same type.")

    # Sample ID collision check
    all_sample_sets: list[tuple[int, set]] = []
    for entry in studies_with_artifacts:
        sid = entry["study_id"]
        ids = entry.get("sample_ids")
        if isinstance(ids, list) and ids:
            all_sample_sets.append((sid, set(ids)))

    for i in range(len(all_sample_sets)):
        for j in range(i + 1, len(all_sample_sets)):
            sid_a, set_a = all_sample_sets[i]
            sid_b, set_b = all_sample_sets[j]
            shared = set_a & set_b
            if shared:
                examples = sorted(shared)[:3]
                warnings.append(
                    f"Studies {sid_a} and {sid_b} share {len(shared)} sample ID(s): "
                    f"{examples}{'...' if len(shared) > 3 else ''}. "
                    "Shared IDs will be collapsed during merge."
                )

    # Sample ID length check (HDF5 dimension label limit)
    for entry in studies_with_artifacts:
        sid = entry["study_id"]
        ids = entry.get("sample_ids") or []
        long_ids = [s for s in ids if len(s) > 63]
        if long_ids:
            warnings.append(
                f"Study {sid} has {len(long_ids)} sample ID(s) exceeding 63 characters "
                "(HDF5 label limit); they may be truncated."
            )

    return {
        "compatible": len(errors) == 0,
        "namespace_groups": namespace_groups,
        "warnings": warnings,
        "errors": errors,
    }
