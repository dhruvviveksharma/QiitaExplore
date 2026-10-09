"""Row model of the Sample Aggregation table.

A row is one (sample_id, artifact_id) pair — a sample's file in one artifact —
and an artifact belongs to exactly one prep, so a row also fixes its prep. A
sample with no file under the study's filter is shown as one placeholder row
(artifact_id None) so file-less samples stay visible; it can't be checked.

order_rows turns the in-scope sample ids into the ordered row keys the table
pages through: grouping, sorting and the group-header counts all live here.
"""

from helpers.fastq_manifest import group_matches


def file_entries(all_files, sample_id, file_filter):
    """The sample's availability entries (data_type, processing, artifact_id,
    fastq, fasta) that pass the study's filter, by artifact id."""
    return sorted((e for e in all_files.get(sample_id, [])
                   if group_matches(file_filter, e[0], e[1], e[2])), key=lambda e: e[2])


def _sort_key(value, desc):
    """A row's prep / artifact id, negated for descending; rows with none last
    in both directions."""
    return (value is None, 0 if value is None else -value if desc else value)


def order_rows(ids, all_files, membership, artifact_prep, file_filter, group, sort, desc):
    """(keys, groups). `ids` arrive scoped, Show-filtered and files-first / id
    ordered; every sort here is stable, so ties keep that order. keys is
    [(sample_id, artifact_id | None, prep_id | None)]:
    - a file row per entry passing the filter, its prep = the artifact's;
    - a sample without one: a placeholder (artifact None) — ungrouped a single
      row (prep None); grouped, one under each prep of its that passes the Data
      type filter, or under None ("No prep").
    sort=prep|artifact orders by that column; grouped, rows are clustered by
    prep ascending (descending for sort=prep&dir=desc) and sort=artifact orders
    the rows inside each group. groups (grouped only) is [{prep_id, data_type,
    count}] in group order, count = rows."""
    want_dts = file_filter.get("data_types") or []
    keys = []
    for sid in ids:
        entries = file_entries(all_files, sid, file_filter)
        if entries:
            keys += [(sid, e[2], artifact_prep.get(e[2])) for e in entries]
        elif group:
            preps = [p for p, dt in membership.get(sid, []) if not want_dts or dt in want_dts]
            keys += [(sid, None, p) for p in (preps or [None])]
        else:
            keys.append((sid, None, None))
    if sort == "artifact":
        keys.sort(key=lambda k: _sort_key(k[1], desc))
    if group or sort == "prep":
        keys.sort(key=lambda k: _sort_key(k[2], desc and sort == "prep"))
    if not group:
        return keys, None
    prep_dt = {p: dt for preps in membership.values() for p, dt in preps}
    counts = {}
    for _sid, _aid, prep in keys:
        counts[prep] = counts.get(prep, 0) + 1
    return keys, [{"prep_id": p, "data_type": prep_dt.get(p), "count": n} for p, n in counts.items()]
