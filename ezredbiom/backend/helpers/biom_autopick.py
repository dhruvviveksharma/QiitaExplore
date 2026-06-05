"""Auto-pick BIOM artifact per study and validate namespace compatibility for merging."""

import json


def autopick_artifact(artifacts: list, data_type: str) -> dict | None:
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


def check_namespace_compatibility(studies_with_artifacts: list) -> dict:
    """Validate that studies can be meaningfully merged.

    Args:
        studies_with_artifacts: list of dicts with keys:
            study_id (int), artifact (dict), sample_ids (list|None)

    Returns dict with keys: compatible (bool), namespace_groups (dict),
        warnings (list[str]), errors (list[str])
    """
    warnings = []
    errors = []
    namespace_groups: dict[str, list] = {}

    for entry in studies_with_artifacts:
        sid = entry["study_id"]
        artifact = entry.get("artifact") or {}
        ns = _namespace(artifact.get("data_type") or "")
        namespace_groups.setdefault(ns, []).append(sid)

    if len(namespace_groups) > 1:
        ns_list = ", ".join(sorted(namespace_groups.keys()))
        errors.append(f"Studies span multiple feature namespaces: {ns_list}. "
                       "Merging across namespaces produces biologically meaningless results.")

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
