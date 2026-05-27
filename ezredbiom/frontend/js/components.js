// ─── CollapsibleSection: section with persisted open/closed state ─────────────
function CollapsibleSection({ id, title, subtitle, children, defaultOpen = false }) {
  const storageKey = id ? `collapse:${id}` : null;
  const [open, setOpen] = useState(() => {
    if (!storageKey) return defaultOpen;
    try {
      const v = localStorage.getItem(storageKey);
      return v === null ? defaultOpen : v === '1';
    } catch (_) { return defaultOpen; }
  });
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (storageKey) {
      try { localStorage.setItem(storageKey, next ? '1' : '0'); } catch (_) {}
    }
  };
  return (
    <div className={`collapsible-section ${open ? 'open' : ''}`}>
      <button className="collapsible-header" onClick={toggle}>
        <span className={`collapsible-chevron ${open ? 'open' : ''}`}>▸</span>
        <span className="collapsible-title">{title}</span>
        {subtitle ? <span className="collapsible-subtitle">{subtitle}</span> : null}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}

// ─── Field grouping for sample detail panel ──────────────────────────────────
const FIELD_GROUPS = [
  ['Identity',       ['sample_id','anonymized_name','qiita_study_id']],
  ['Biological',     ['env_package','body_site','env_biome','env_feature','host_taxid','host_subject_id']],
  ['Location',       ['latitude','longitude','country','elevation','depth','location']],
  ['Temporal',       ['collection_timestamp','collection_date','collection_time']],
  ['Administrative', ['description','notes']],
];

// ─── SamplesBrowser: shared two-pane / stacked sample viewer ──────────────────
function SamplesBrowser({ samples, totalSamples, layout, fetchFields }) {
  const [activeId,     setActiveId]     = useState(null);
  const [activeFields, setActiveFields] = useState(null);
  const [loading,      setLoading]      = useState(false);
  const [filterText,   setFilterText]   = useState('');
  const [showVaryingOnly, setShowVaryingOnly] = useState(false);

  const onRowClick = async (s) => {
    if (activeId === s.sample_id) { setActiveId(null); setActiveFields(null); return; }
    setActiveId(s.sample_id);
    if (s.fields && Object.keys(s.fields).length) { setActiveFields(s.fields); return; }
    if (fetchFields) {
      setLoading(true);
      try { setActiveFields(await fetchFields(s.sample_id)); }
      catch (_) { setActiveFields(null); }
      finally { setLoading(false); }
    }
  };

  const haystacks = useMemo(() => {
    return (samples || []).map(s => {
      const f     = s.fields || {};
      const parts = [s.sample_id, s.anonymized_name, s.env_package, s.collection_timestamp, ...Object.values(f)];
      return parts.map(v => v == null ? '' : String(v).toLowerCase()).join(' ');
    });
  }, [samples]);

  const filteredSamples = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    if (!q) return samples || [];
    const out = [];
    for (let i = 0; i < (samples || []).length; i++) {
      if (haystacks[i].indexOf(q) !== -1) out.push(samples[i]);
    }
    return out;
  }, [samples, haystacks, filterText]);

  const summaryChips = useMemo(() => {
    if (!samples || samples.length < 1) return [];
    const tryKey = (key) => {
      const counts = new Map();
      for (const s of samples) {
        const v = (s && s[key]) || (s && s.fields && s.fields[key]);
        if (v == null || v === '') continue;
        const sv = String(v);
        counts.set(sv, (counts.get(sv) || 0) + 1);
      }
      return counts;
    };
    let counts = tryKey('env_package');
    if (counts.size < 1) counts = tryKey('body_site');
    if (counts.size < 1) return [];
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([value, count]) => ({ value, count }));
  }, [samples]);

  const uniformFields = useMemo(() => {
    const populated = (samples || []).filter(s => s.fields && Object.keys(s.fields).length);
    if (populated.length < 2) return new Set();
    const keyVals = new Map();
    for (const s of populated) {
      for (const [k, v] of Object.entries(s.fields)) {
        if (!keyVals.has(k)) keyVals.set(k, new Set());
        keyVals.get(k).add(v == null ? '' : String(v));
      }
    }
    const uniform = new Set();
    for (const [k, vals] of keyVals.entries()) { if (vals.size === 1) uniform.add(k); }
    return uniform;
  }, [samples]);

  const groupedActiveFields = useMemo(() => {
    if (!activeFields) return [];
    const remaining = new Map(Object.entries(activeFields).filter(([, v]) => v != null && v !== ''));
    const result = [];
    for (const [groupName, keys] of FIELD_GROUPS) {
      const entries = [];
      for (const target of keys) {
        for (const k of [...remaining.keys()]) {
          if (k.toLowerCase() === target.toLowerCase()) { entries.push([k, remaining.get(k)]); remaining.delete(k); }
        }
      }
      if (entries.length) result.push([groupName, entries]);
    }
    if (remaining.size) result.push(['Other', [...remaining.entries()].sort(([a], [b]) => a.localeCompare(b))]);
    return result;
  }, [activeFields]);

  const visibleGroups = useMemo(() => {
    if (!showVaryingOnly || uniformFields.size === 0) return groupedActiveFields;
    return groupedActiveFields
      .map(([g, entries]) => [g, entries.filter(([k]) => !uniformFields.has(k))])
      .filter(([, entries]) => entries.length);
  }, [groupedActiveFields, uniformFields, showVaryingOnly]);

  const hiddenUniformCount = useMemo(() => {
    if (!showVaryingOnly || !activeFields) return 0;
    let n = 0;
    for (const k of Object.keys(activeFields)) if (uniformFields.has(k)) n++;
    return n;
  }, [activeFields, uniformFields, showVaryingOnly]);

  if (!samples || samples.length === 0) {
    return <p style={{color:'var(--text-3)', fontSize:'0.85rem'}}>No samples found.</p>;
  }

  const isStacked = layout === 'stacked';

  const toolbarEl = (
    <>
      <div className="samples-toolbar">
        <input className="samples-search" placeholder="Filter samples…" value={filterText}
          onChange={e => setFilterText(e.target.value)} />
        {filterText && (
          <span className="samples-filter-count">
            {filteredSamples.length} of {samples.length}
            <button className="samples-clear" onClick={() => setFilterText('')}>Clear</button>
          </span>
        )}
      </div>
      {summaryChips.length > 0 && (
        <div className="samples-chips">
          {summaryChips.map(({ value, count }) => (
            <button key={value} className={`samples-chip ${filterText === value ? 'active' : ''}`}
              onClick={() => setFilterText(filterText === value ? '' : value)}>
              {value} <span className="samples-chip-count">{count}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );

  const tableEl = (
    <div className="samples-table-wrap" style={isStacked ? null : {flex:'0 0 auto', maxWidth:'55%'}}>
      <table className="prep-table">
        <thead><tr><th>Sample ID</th><th>Anonymized Name</th><th>Env Package</th><th>Collection Date</th></tr></thead>
        <tbody>
          {filteredSamples.length === 0 ? (
            <tr><td colSpan={4} style={{color:'var(--text-3)', fontSize:'11.5px', padding:'10px 8px'}}>No matches.</td></tr>
          ) : filteredSamples.map(s => {
            const f = s.fields || {};
            const collectionDate = s.collection_timestamp || f.collection_timestamp || f.collection_date || '';
            return (
              <tr key={s.sample_id} onClick={() => onRowClick(s)}
                style={{cursor:'pointer', background: activeId === s.sample_id ? 'var(--accent-bg,#f0f4ff)' : ''}}>
                <td>{s.sample_id}</td>
                <td>{s.anonymized_name || f.anonymized_name || '—'}</td>
                <td>{s.env_package || f.env_package || '—'}</td>
                <td>{collectionDate ? String(collectionDate).slice(0, 10) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  const canVaryToggle = uniformFields.size > 0;
  const detailEl = loading ? (
    <div style={{flex:1, color:'var(--text-3)', fontSize:'0.85rem', paddingTop:'0.5rem'}}>Loading…</div>
  ) : activeFields ? (
    <div className="sample-preview-card">
      <div className="sample-preview-header">
        <span>{activeId}</span>
        <div style={{display:'flex', alignItems:'center', gap:8}}>
          {canVaryToggle && (
            <label className="sample-preview-toggle">
              <input type="checkbox" checked={showVaryingOnly} onChange={e => setShowVaryingOnly(e.target.checked)} />
              Show only varying
            </label>
          )}
          <button className="sample-preview-close" onClick={() => { setActiveId(null); setActiveFields(null); }}>✕</button>
        </div>
      </div>
      <div className="sample-preview-body">
        {visibleGroups.map(([groupName, entries]) => (
          <div key={groupName} className="sample-preview-group">
            <h5 className="sample-preview-group-title">{groupName}</h5>
            {entries.map(([k, v]) => (
              <div key={k} className="sample-preview-row">
                <span className="sample-preview-key">{k}</span>
                <span className="sample-preview-val">{String(v)}</span>
              </div>
            ))}
          </div>
        ))}
        {showVaryingOnly && hiddenUniformCount > 0 && (
          <div className="sample-preview-hidden-note">
            {hiddenUniformCount} uniform field{hiddenUniformCount === 1 ? '' : 's'} hidden
          </div>
        )}
      </div>
    </div>
  ) : null;

  if (isStacked) {
    return <div className="samples-browser-stacked">{toolbarEl}{tableEl}{detailEl}</div>;
  }
  return (
    <div>
      {toolbarEl}
      <div style={{display:'flex', gap:'1rem', alignItems:'flex-start'}}>{tableEl}{detailEl}</div>
    </div>
  );
}

// ─── PrepsTable ───────────────────────────────────────────────────────────────
function PrepsTable({ detail, loading, onMount }) {
  useEffect(() => { onMount && onMount(); }, []);
  if (loading && !detail) return <div className="modal-detail-loading">Loading…</div>;
  if (!detail) return null;
  const preps = detail.preps || [];
  if (preps.length === 0) return <p style={{color:'var(--text-3)', fontSize:'0.85rem'}}>No prep templates found.</p>;
  return (
    <table className="prep-table">
      <thead><tr><th>Prep ID</th><th>Data Type</th><th>Investigation</th><th>Platform</th><th>Target Gene</th><th>Status</th></tr></thead>
      <tbody>
        {preps.map(p => (
          <tr key={p.prep_template_id}>
            <td>{p.prep_template_id}</td>
            <td>{p.data_type || '—'}</td>
            <td>{p.investigation_type || '—'}</td>
            <td>{p.platform || '—'}</td>
            <td>{p.target_gene || '—'}</td>
            <td>{p.preprocessing_status || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ─── ArtifactsTable ───────────────────────────────────────────────────────────
function ArtifactsTable({ detail, loading, onMount }) {
  useEffect(() => { onMount && onMount(); }, []);
  if (loading && !detail) return <div className="modal-detail-loading">Loading…</div>;
  if (!detail) return null;
  const artifacts = detail.artifacts || [];
  if (artifacts.length === 0) return <p style={{color:'var(--text-3)', fontSize:'0.85rem'}}>No artifacts found.</p>;
  return (
    <table className="prep-table">
      <thead><tr><th>Artifact ID</th><th>Type</th><th>Data Type</th><th>File Path</th></tr></thead>
      <tbody>
        {artifacts.map(a => (
          <tr key={`${a.prep_template_id}-${a.artifact_id}`}>
            <td>{a.artifact_id}</td>
            <td>{a.artifact_type || '—'}</td>
            <td>{a.data_type || '—'}</td>
            <td className="artifact-path-cell">
              <span className="artifact-path" title={a.full_path}>
                {a.full_path ? a.full_path.split('/').slice(-2).join('/') : '—'}
              </span>
              {a.full_path && (
                <button className="btn-copy-path" title="Copy full path"
                  onClick={() => navigator.clipboard?.writeText(a.full_path)}>⎘</button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ─── SystemsStatusBubble ─────────────────────────────────────────────────────
function SystemsStatusBubble({ ui }) {
  if (!ui?.models?.length) return null;
  return (
    <div className="systems-status">
      <div className="systems-status-title">Model Status</div>
      <table className="systems-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Size</th>
            <th>Tier</th>
            <th>Context</th>
            <th>Modalities</th>
            <th>Status</th>
            <th>Latency</th>
          </tr>
        </thead>
        <tbody>
          {ui.models.map(m => (
            <tr key={m.name} className={`systems-row systems-row-${m.status}`}>
              <td><code className="systems-model-name">{m.name}</code></td>
              <td className="systems-cell-dim">{m.size}</td>
              <td><span className={`tier-badge tier-${m.tier}`}>{m.tier}</span></td>
              <td className="systems-cell-dim">{m.context != null ? m.context.toLocaleString() : '—'}</td>
              <td className="systems-cell-dim">{m.modalities}</td>
              <td>
                <span className={`status-indicator status-${m.status}`} />
                {m.status === 'ok' ? 'Online' : 'Down'}
              </td>
              <td className="systems-cell-dim">{m.latency_ms != null ? `${m.latency_ms}ms` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── SamplesReportBubble ──────────────────────────────────────────────────────
function SamplesReportBubble({ ui, messageKey }) {
  if (!ui) return null;
  const { header = {}, samples = [], study_id } = ui;
  const numSamples = header.num_samples != null ? header.num_samples : samples.length;
  const [detail,        setDetail]        = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailReqRef = useRef(false);

  const ensureDetail = useCallback(async () => {
    if (detail || detailReqRef.current) return;
    detailReqRef.current = true;
    setDetailLoading(true);
    try {
      const d = await fetchStudyDetail(study_id);
      setDetail({ preps: d.preps || [], artifacts: d.artifacts || [] });
    } catch (_) {
      detailReqRef.current = false;
    } finally {
      setDetailLoading(false);
    }
  }, [study_id, detail]);

  const keyBase = messageKey || `study-${study_id}`;
  return (
    <div className="samples-report-bubble">
      <div className="samples-report-header">
        <div className="samples-report-title">Study {study_id}: {header.study_title || 'Untitled study'}</div>
        <div className="samples-report-meta">
          {header.pi_name ? <span>PI: {header.pi_name}{header.pi_affiliation ? ` (${header.pi_affiliation})` : ''}</span> : null}
          {numSamples != null ? <span>{numSamples} samples</span> : null}
          {header.data_types ? <span>{header.data_types}</span> : null}
          {header.num_preps != null ? <span>{header.num_preps} preps</span> : null}
        </div>
        {header.study_abstract && <div className="samples-report-abstract">{header.study_abstract}</div>}
      </div>
      <CollapsibleSection id={`${keyBase}-samples`} title="Samples"
        subtitle={`${numSamples} total${samples.length < numSamples ? `, showing ${samples.length}` : ''}`}
        defaultOpen={true}>
        <SamplesBrowser samples={samples} totalSamples={numSamples} layout="stacked" />
      </CollapsibleSection>
      <CollapsibleSection id={`${keyBase}-preps`} title="Prep Templates"
        subtitle={detail ? `${(detail.preps || []).length} total` : (header.num_preps != null ? `${header.num_preps} total` : '')}
        defaultOpen={false}>
        <PrepsTable detail={detail} loading={detailLoading} onMount={ensureDetail} />
      </CollapsibleSection>
      <CollapsibleSection id={`${keyBase}-artifacts`} title="Artifacts"
        subtitle={detail ? `${(detail.artifacts || []).length} total` : ''} defaultOpen={false}>
        <ArtifactsTable detail={detail} loading={detailLoading} onMount={ensureDetail} />
      </CollapsibleSection>
    </div>
  );
}
