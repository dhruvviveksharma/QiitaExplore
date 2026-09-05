// Sample Aggregation — a user's named set of studies whose per_sample_FASTQ
// artifacts feed one QIIME2 V2 manifest (GET /api/aggregations/<id>/manifest,
// helpers/fastq_manifest.fetch_aggregate_manifest). Three pieces live here:
// the state hook (called once in useAppState), the Browse-card picker, and
// the "Sample Aggregation" tab.
// Globals in scope: React, useState, useEffect (utils.js), apiJson, API (utils.js),
//   useDropdown (hooks/useDropdown.js), WreathLoader (loaders.js), splitTypes (components.js)

function useAggregations() {
  const [aggregations, setAggregations] = useState(null); // null until the first GET resolves

  useEffect(() => {
    apiJson('/aggregations')
      .then(d => setAggregations(d.aggregations || []))
      .catch(() => setAggregations([]));
  }, []);

  // Every mutation route returns the full aggregation, so local state is
  // patched from the response body — never re-fetched (CLAUDE.md).
  const replace = (upd) =>
    setAggregations(list => (list || []).map(a => a.aggregation_id === upd.aggregation_id ? upd : a));

  const create = async (name) => {
    const a = await apiJson('/aggregations', { method: 'POST', body: JSON.stringify({ name }) });
    setAggregations(list => [a, ...(list || [])]);
    return a;
  };
  const rename = async (id, name) =>
    replace(await apiJson(`/aggregations/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }));
  const remove = async (id) => {
    await apiJson(`/aggregations/${id}`, { method: 'DELETE' });
    setAggregations(list => (list || []).filter(a => a.aggregation_id !== id));
  };
  const addStudy = async (id, study) =>
    replace(await apiJson(`/aggregations/${id}/studies`, { method: 'POST', body: JSON.stringify({ study }) }));
  const removeStudy = async (id, studyId) =>
    replace(await apiJson(`/aggregations/${id}/studies/${studyId}`, { method: 'DELETE' }));

  return { aggregations, create, rename, remove, addStudy, removeStudy };
}

// The header fields the server snapshots onto aggregation_studies — same
// shape AddToProjectBar (study_modal.js) sends, plus num_preps.
const _aggStudyBody = (study) => ({
  study_id: study.study_id,
  study_title: study.study_title,
  data_types: study.data_types || '',
  num_samples: study.num_samples ?? null,
  num_preps: study.num_preps ?? null,
});

const _plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Browse-card picker. Same trigger/panel/dismiss shape as ProjectPickerDropdown
// (study_modal.js), right-aligned like ChatRowMenu because it sits at the
// card's right edge. Lives inside .study-card-actions, whose onClick already
// stops propagation so the card's modal doesn't open.
function AggregateCardButton({ study, agg }) {
  const dd = useDropdown(r => ({ top: r.bottom + 4, left: Math.max(8, r.right - 200) }));
  const [err, setErr] = useState('');
  const list = agg.aggregations || [];

  const add = async (a) => {
    setErr('');
    try { await agg.addStudy(a.aggregation_id, _aggStudyBody(study)); }
    catch (e) { setErr(e.message); }
  };
  const createAndAdd = async () => {
    const name = prompt('Aggregation name:', 'Untitled');
    if (name === null) return;
    let a;
    try { a = await agg.create(name.trim() || 'Untitled'); }
    catch (e) { setErr(e.message); return; }
    add(a);
  };

  return (
    <div className="dd-root" ref={dd.rootRef}>
      <button type="button" ref={dd.btnRef} className="btn-card-add"
        onClick={e => { setErr(''); dd.toggle(e); }}>+ Aggregate</button>
      {dd.open && dd.pos && (
        <div className={dd.menuClass} style={{ top: dd.pos.top, left: dd.pos.left }} onClick={e => e.stopPropagation()}>
          <div className="cr-menu-label">Add to aggregation</div>
          {agg.aggregations === null && <div className="cr-menu-label">Loading…</div>}
          {list.map(a => {
            const inAgg = (a.studies || []).some(s => s.study_id === study.study_id);
            return (
              <button key={a.aggregation_id} className={`cr-menu-item${inAgg ? ' agg-in' : ''}`}
                disabled={inAgg} onClick={() => { dd.setOpen(false); add(a); }}>
                <span className="agg-menu-name">{a.name}</span>
                {inAgg && <span>✓</span>}
              </button>
            );
          })}
          <div className="cr-menu-sep" />
          <button className="cr-menu-item" onClick={() => { dd.setOpen(false); createAndAdd(); }}>
            + New aggregation…
          </button>
        </div>
      )}
      {err && <div className="browse-error agg-card-err">{err}</div>}
    </div>
  );
}

// ── Tab ──────────────────────────────────────────────────────────────────────

function AggregationDetail({ a, agg }) {
  const studies = a.studies || [];
  const fastqTotal = studies.reduce((n, s) => n + (s.fastq_artifact_count || 0), 0);
  // Plain link like FileLink (merge_artifacts.js) and the per-artifact manifest
  // (fastq_manifest.js): the session cookie rides along on top-level navigation.
  const href = `${API}/aggregations/${a.aggregation_id}/manifest`;

  return (
    <div className="agg-detail">
      <div className="agg-detail-head">
        <div>
          <div className="agg-detail-title">{a.name}</div>
          <div className="agg-detail-sub">
            {_plural(studies.length, 'study', 'studies')} · {_plural(fastqTotal, 'per-sample FASTQ artifact', 'per-sample FASTQ artifacts')}
          </div>
        </div>
        {fastqTotal > 0
          ? <a className="ao-file-btn" target="_blank" rel="noreferrer" href={href}>↓ Download QIIME2 manifest</a>
          : <span className="ao-file-btn agg-disabled" title="Add a study that has per-sample FASTQ artifacts">↓ Download QIIME2 manifest</span>}
      </div>
      {studies.length === 0
        ? <div className="agg-empty">No studies yet — click <strong>+ Aggregate</strong> on a study card in Browse.</div>
        : (
          <table className="prep-table">
            <thead>
              <tr><th>ID</th><th>Title</th><th>Data types</th><th>Samples</th><th>FASTQ artifacts</th><th></th></tr>
            </thead>
            <tbody>
              {studies.map(s => (
                <tr key={s.study_id}>
                  <td>{s.study_id}</td>
                  <td>{s.study_title || 'Untitled study'}</td>
                  <td>{splitTypes(s.data_types).map(t => <span key={t} className="dtype-chip">{t}</span>)}</td>
                  <td>{s.num_samples ?? '—'}</td>
                  <td>{s.fastq_artifact_count ?? '—'}</td>
                  <td>
                    <button className="agg-remove" title="Remove study"
                      onClick={() => agg.removeStudy(a.aggregation_id, s.study_id)}>×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </div>
  );
}

function AggregationsTab({ agg }) {
  const [activeId,  setActiveId]  = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editVal,   setEditVal]   = useState('');
  const list   = agg.aggregations;
  const active = (list || []).find(a => a.aggregation_id === activeId) || null;

  const createNew = async () => {
    const name = prompt('Aggregation name:', 'Untitled');
    if (name === null) return;
    const a = await agg.create(name.trim() || 'Untitled');
    setActiveId(a.aggregation_id);
  };
  // Inline rename, same Enter / blur / Escape contract as MergesTab (merge_workspace.js).
  const saveRename = async (id) => {
    const t = editVal.trim();
    setEditingId(null);
    if (t) await agg.rename(id, t);
  };
  const cancelRename = () => { setEditVal(''); setEditingId(null); };
  const del = async (a) => {
    if (!confirm(`Delete aggregation "${a.name}"?`)) return;
    await agg.remove(a.aggregation_id);
    if (activeId === a.aggregation_id) setActiveId(null);
  };

  return (
    <div className="agg-layout">
      <div className="agg-list-col">
        <div className="agg-list-head">
          <span>Aggregations</span>
          <button className="merge-btn-ghost" onClick={createNew}>+ New</button>
        </div>
        {list === null && <div className="logo-spinner" style={{ margin: '40px auto' }}><WreathLoader size={32} /></div>}
        {list && list.length === 0 && (
          <div className="agg-empty">
            <p>No aggregations yet.</p>
            <p>Click <strong>+ Aggregate</strong> on any study card, or <strong>+ New</strong> above.</p>
          </div>
        )}
        {(list || []).map(a => (
          <div key={a.aggregation_id} className={`agg-card${a.aggregation_id === activeId ? ' active' : ''}`}
            onClick={() => setActiveId(a.aggregation_id)}>
            {editingId === a.aggregation_id
              ? <input className="agg-name-input" value={editVal} autoFocus
                  onChange={e => setEditVal(e.target.value)}
                  onBlur={() => saveRename(a.aggregation_id)}
                  onKeyDown={e => { if (e.key === 'Enter') saveRename(a.aggregation_id); if (e.key === 'Escape') cancelRename(); }}
                  onClick={e => e.stopPropagation()} />
              : <span className="agg-name" title="Click to rename"
                  onClick={e => { e.stopPropagation(); setActiveId(a.aggregation_id); setEditingId(a.aggregation_id); setEditVal(a.name); }}>
                  {a.name} <span className="rename-hint">✎</span>
                </span>
            }
            <span className="agg-count">{_plural((a.studies || []).length, 'study', 'studies')}</span>
            <button className="agg-remove" title="Delete aggregation" onClick={e => { e.stopPropagation(); del(a); }}>×</button>
          </div>
        ))}
      </div>
      <div className="agg-detail-col">
        {active
          ? <AggregationDetail key={active.aggregation_id} a={active} agg={agg} />
          : <div className="agg-detail-empty">Select an aggregation to view it here</div>}
      </div>
    </div>
  );
}
