// Sample Aggregation tab — the detail side: the aggregation's studies as
// Browse-style cards (3 per row, 2 while the metadata pane is open), the
// expanded study's sample table (checkbox = in the aggregation, sample id =
// open its metadata, Data type + FASTQ/FASTA columns, a files-first Show
// filter), and the metadata pane itself. The header's Data type / Processing
// pickers are the aggregation's saved file_filter: every table and both
// exports follow it. Loads before aggregations.js, whose AggregationsTab
// renders these.
// Globals in scope: React, useState, useEffect, useRef (utils.js), apiJson, API (utils.js),
//   StudyCard (study_card.js), FacetMultiSelect (browse_filters.js)

const _plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const _AGG_PAGE = 200;
const _NO_FILTER = { data_types: [], processing: [] };
const _chunk = (arr, n) => {
  const out = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
};

function AggregationDetail({ a, agg, picked, onPickSample }) {
  const [expandedId, setExpandedId] = useState(null);
  const [facets, setFacets] = useState(null); // GET …/file-facets; kept while a refetch runs
  const [err, setErr] = useState('');
  const studies = a.studies || [];
  const filt = a.file_filter || _NO_FILTER;
  const filterKey = JSON.stringify(filt);
  const totalSelected = studies.reduce((n, s) => n + (s.selected_samples || 0), 0);
  const artifactTotal = studies.reduce((n, s) => n + (s.fastq_artifact_count || 0), 0);
  // Rows are chunked explicitly (not grid-auto-flow: dense) so the expanded
  // study's sample table sits right under its own row, DOM order = visual order.
  const perRow = picked ? 2 : 3;

  // Picker options + the exportable count depend on the filter and on what
  // is checked, so both are in the key. The per-study maps are cached
  // server-side; a refetch is cheap after the first.
  const selKey = studies.map(s => `${s.study_id}:${s.selected_samples}`).join(',');
  useEffect(() => {
    let live = true;
    apiJson(`/aggregations/${a.aggregation_id}/file-facets`)
      .then(d => { if (live) setFacets(d); })
      .catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [a.aggregation_id, filterKey, selKey]);

  const setFilter = (key, names) => {
    setErr('');
    agg.setFileFilter(a.aggregation_id, { ...filt, [key]: names }).catch(e => setErr(e.message));
  };
  const exportable = facets?.exportable;
  const canExport = totalSelected > 0 && exportable !== 0;
  const disabledTitle = totalSelected === 0
    ? 'Check at least one sample'
    : 'No checked sample has a file for this Data type / Processing';
  // Plain links like the per-artifact manifest (fastq_manifest.js): the
  // session cookie rides along on top-level navigation.
  const base = `${API}/aggregations/${a.aggregation_id}`;
  const download = (href, label, title) => canExport
    ? <a className="ao-file-btn" target="_blank" rel="noreferrer" href={href} title={title}>{label}</a>
    : <span className="ao-file-btn agg-disabled" title={disabledTitle}>{label}</span>;

  return (
    <div className="agg-detail">
      <div className="agg-detail-head">
        <div>
          <div className="agg-detail-title">{a.name}</div>
          <div className="agg-detail-sub">
            {_plural(studies.length, 'study', 'studies')}
            {' · '}{_plural(totalSelected, 'selected sample', 'selected samples')}
            {exportable != null && (
              <span title="Checked samples with a file matching the Data type / Processing filter — what the export contains">
                {' · '}{exportable.toLocaleString()} with files
              </span>
            )}
            {' · '}{_plural(artifactTotal, 'sequence-file artifact', 'sequence-file artifacts')}
          </div>
        </div>
        <div className="agg-download-col">
          <div className="agg-download-row">
            <FacetMultiSelect label="Data type" options={facets?.data_types} selected={filt.data_types}
              onChange={names => setFilter('data_types', names)} disabled={!facets} />
            <FacetMultiSelect label="Processing" options={facets?.processing} selected={filt.processing}
              onChange={names => setFilter('processing', names)} disabled={!facets} />
            {download(`${base}/export.xlsx`, '↓ Excel (.xlsx)', 'For Excel / Numbers: sample ids stay text')}
            {download(`${base}/export.csv`, '↓ CSV (for scripts)', 'For pandas / scripts')}
          </div>
          {err && <div className="browse-error">{err}</div>}
        </div>
      </div>
      {studies.length === 0
        ? <div className="agg-empty">No studies yet — click <strong>+ Aggregate</strong> on a study card in Browse.</div>
        : _chunk(studies, perRow).map((row, i) => {
          const expanded = row.find(s => s.study_id === expandedId);
          return (
            <React.Fragment key={i}>
              <div className={`agg-grid${picked ? ' two' : ''}`}>
                {row.map(s => (
                  <StudyCard key={s.study_id} study={s}
                    className={s.study_id === expandedId ? 'agg-expanded' : ''}
                    onClick={() => setExpandedId(id => id === s.study_id ? null : s.study_id)}
                    actions={<>
                      <span className="agg-sel-count">
                        {(s.selected_samples ?? 0).toLocaleString()} / {(s.num_samples ?? 0).toLocaleString()} samples
                      </span>
                      <button className="agg-remove" title="Remove study"
                        onClick={() => agg.removeStudy(a.aggregation_id, s.study_id)}>×</button>
                    </>} />
                ))}
              </div>
              {expanded && (
                <AggregationSampleTable key={`samples-${expanded.study_id}`} a={a} agg={agg} study={expanded}
                  filt={filt} picked={picked} onPickSample={onPickSample} />
              )}
            </React.Fragment>
          );
        })}
    </div>
  );
}

// One study's samples, paged from Qiita with the aggregation's checked state
// and file availability (under the saved file_filter) merged in
// server-side, sorted files-first. Load-more shape from merge_detail.js
// StudySampleTable.
function AggregationSampleTable({ a, agg, study, filt, picked, onPickSample }) {
  const [rows,      setRows]      = useState([]);
  const [total,     setTotal]     = useState(study.num_samples ?? 0);
  const [withFiles, setWithFiles] = useState(0);
  const [columns,   setColumns]   = useState([]);
  const [q,         setQ]         = useState('');
  const [show,      setShow]      = useState('all'); // 'all' | 'with_files' | 'without_files'
  const [loading,   setLoading]   = useState(false);
  const [err,       setErr]       = useState('');
  const seq = useRef(0);
  const aid = a.aggregation_id, sid = study.study_id;
  const selected = study.selected_samples ?? 0;

  const load = async (offset, query, showVal, append) => {
    const mine = ++seq.current;   // a slower earlier page must not overwrite a newer one
    setLoading(true); setErr('');
    try {
      const d = await apiJson(
        `/aggregations/${aid}/studies/${sid}/samples?offset=${offset}&limit=${_AGG_PAGE}` +
        `&q=${encodeURIComponent(query)}&show=${showVal}`);
      if (mine !== seq.current) return;
      setRows(prev => append ? [...prev, ...(d.rows || [])] : (d.rows || []));
      setTotal(d.total ?? 0);
      setWithFiles(d.with_files ?? 0);
      setColumns(d.columns || []);
    } catch (e) {
      if (mine === seq.current) setErr(e.message);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  };

  // First page on mount; a filter, Show or file_filter change reloads page 0.
  // The text filter is debounced 300 ms (it's an ILIKE over the whole sample
  // table — ~1 s on 40k rows); the others are discrete clicks.
  const filterKey = JSON.stringify(filt);
  useEffect(() => {
    const t = setTimeout(() => load(0, q.trim(), show, false), q ? 300 : 0);
    return () => clearTimeout(t);
  }, [q, show, filterKey]);

  const toggle = async (r) => {
    const next = !r.selected;
    const flip = (v) => setRows(prev => prev.map(x => x.sample_id === r.sample_id ? { ...x, selected: v } : x));
    flip(next);
    try { await agg.setSamples(aid, sid, next ? { add: [r.sample_id] } : { remove: [r.sample_id] }); }
    catch (e) { flip(!next); setErr(e.message); }
  };
  const bulk = async (body) => {
    setErr('');
    try { await agg.setSamples(aid, sid, body); await load(0, q.trim(), show, false); }
    catch (e) { setErr(e.message); }
  };

  const hasMore = rows.length < total;
  const isPicked = (r) => !!picked && picked.study_id === sid && picked.sample_id === r.sample_id;
  const fastqLabel = (r) => r.fastq === 'paired' ? 'R1+R2' : r.fastq === 'single' ? 'R1' : '—';
  // A data type the header filter excludes stays visible, dimmed: the sample
  // has such a file, it just isn't what the export will contain.
  const dtKept = (dt) => !filt.data_types.length || filt.data_types.includes(dt);
  return (
    <div className="agg-samples-panel">
      <div className="agg-samples-toolbar">
        <span className="agg-samples-title">Samples of <strong>{study.study_title || `study ${sid}`}</strong></span>
        <input className="samples-search" placeholder="Filter by sample id or any metadata value…"
          value={q} onChange={e => setQ(e.target.value)} />
        <div className="agg-seg">
          <button className={show === 'all' ? 'on' : ''} onClick={() => setShow('all')}>All</button>
          <button className={show === 'with_files' ? 'on' : ''} onClick={() => setShow('with_files')}>
            With files ({withFiles.toLocaleString()})
          </button>
          <button className={show === 'without_files' ? 'on' : ''} onClick={() => setShow('without_files')}>
            Without files
          </button>
        </div>
        <span className="agg-samples-count">
          {selected.toLocaleString()} of {(study.num_samples ?? total).toLocaleString()} selected
          {' · '}{withFiles.toLocaleString()} with files
        </span>
        <button className="merge-btn-ghost" onClick={() => bulk({ select: 'all' })}>Select all</button>
        <button className="merge-btn-ghost" onClick={() => bulk({ select: 'none' })}>Select none</button>
        <button className="merge-btn-ghost" title="Check exactly the samples the CSV can contain"
          onClick={() => bulk({ select: 'with_files', q: q.trim() })}>
          Select all with files
        </button>
        {q.trim() && (
          <button className="merge-btn-ghost" onClick={() => bulk({ select: 'matching', q: q.trim() })}>
            Select matching ({total.toLocaleString()})
          </button>
        )}
      </div>
      {err && <div className="browse-error">{err}</div>}
      <div className="agg-samples-wrap">
        <table className="prep-table">
          <thead>
            <tr><th></th><th>Sample ID</th><th>Data type</th><th>FASTQ</th><th>FASTA</th>
              {columns.map(c => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.sample_id} className={isPicked(r) ? 'agg-row-active' : ''}>
                <td><input type="checkbox" checked={!!r.selected} onChange={() => toggle(r)} /></td>
                <td>
                  <button className="agg-sample-id" title="Show metadata"
                    onClick={() => onPickSample({ study_id: sid, sample_id: r.sample_id })}>{r.sample_id}</button>
                </td>
                <td>
                  {(r.data_types || []).length ? (
                    <span className="agg-dt-cell">
                      {r.data_types.map(dt => {
                        const hasFile = (r.file_data_types || []).includes(dt);
                        const title = hasFile
                          ? `Processing:\n${(r.processing || []).join('\n')}`
                          : `In a ${dt} prep, but no per-sample sequence file — Qiita keeps these ` +
                            'reads in one multiplexed / Demultiplexed file per prep, not exportable here.';
                        return (
                          <span key={dt} title={title}
                            className={`dtype-chip${hasFile ? '' : ' agg-dt-nofile'}${dtKept(dt) ? '' : ' agg-dt-dim'}`}>
                            {dt}
                          </span>
                        );
                      })}
                    </span>
                  ) : <span className="agg-file-no">—</span>}
                </td>
                <td className={r.fastq ? 'agg-file-yes' : 'agg-file-no'}>{fastqLabel(r)}</td>
                <td className={r.fasta ? 'agg-file-yes' : 'agg-file-no'}>{r.fasta ? '✓' : '—'}</td>
                {columns.map(c => <td key={c}>{r.fields?.[c] ?? ''}</td>)}
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={columns.length + 5} className="agg-samples-empty">No samples match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="agg-samples-foot">
        {loading && <span className="agg-samples-msg">Loading…</span>}
        {!loading && hasMore && (
          <button className="merge-btn-ghost" onClick={() => load(rows.length, q.trim(), show, true)}>
            Load more ({rows.length.toLocaleString()} of {total.toLocaleString()})
          </button>
        )}
        {!loading && !hasMore && rows.length > 0 && (
          <span className="agg-samples-msg">
            {total.toLocaleString()} {(q.trim() || show !== 'all') ? 'matching ' : ''}samples
          </span>
        )}
      </div>
    </div>
  );
}

// Every metadata field of one sample (GET /api/studies/<sid>/samples/<id>),
// in the right-hand column while a sample id is picked.
function SampleMetadataPane({ picked, onClose }) {
  const [fields, setFields] = useState(null);
  const [err,    setErr]    = useState('');

  useEffect(() => {
    let alive = true;
    setFields(null); setErr('');
    apiJson(`/studies/${picked.study_id}/samples/${encodeURIComponent(picked.sample_id)}`)
      .then(d => { if (alive) setFields(d.fields || {}); })
      .catch(e => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [picked.study_id, picked.sample_id]);

  const entries = fields ? Object.entries(fields).sort(([x], [y]) => x.localeCompare(y)) : [];
  return (
    <div className="agg-meta-pane">
      <div className="agg-meta-head">
        <div>
          <div className="agg-meta-sample">{picked.sample_id}</div>
          <div className="agg-meta-study">study {picked.study_id} · {fields ? _plural(entries.length, 'field', 'fields') : '…'}</div>
        </div>
        <button className="sample-preview-close" title="Close" onClick={onClose}>×</button>
      </div>
      {err && <div className="browse-error">{err}</div>}
      {!fields && !err && <div className="agg-samples-msg">Loading…</div>}
      {fields && (
        <div className="sample-preview-body">
          {entries.map(([k, v]) => (
            <div key={k} className="sample-preview-row">
              <span className="sample-preview-key">{k}</span>
              <span className="sample-preview-val">{v == null || v === '' ? '—' : String(v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
