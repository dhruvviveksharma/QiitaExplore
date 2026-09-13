// Sample Aggregation — a user's named set of samples, grouped by study, whose
// per-sample sequence files feed one CSV (GET /api/aggregations/<id>/export.csv).
// Three pieces live here: the state hook (called once in useAppState), the
// Browse-card picker ("+ Aggregate" adds the whole study, every sample
// checked), and the "Sample Aggregation" tab shell. The detail side (card
// grid, sample table, metadata pane) is aggregation_detail.js.
// Globals in scope: React, useState, useEffect (utils.js), apiJson (utils.js),
//   useDropdown (hooks/useDropdown.js), WreathLoader (loaders.js),
//   AggregationDetail, SampleMetadataPane, _plural (aggregation_detail.js)

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
  // body: {add:[…], remove:[…]} or {select:'all'|'none'|'matching', q}
  const setSamples = async (id, studyId, body) =>
    replace(await apiJson(`/aggregations/${id}/studies/${studyId}/samples`, { method: 'PATCH', body: JSON.stringify(body) }));

  return { aggregations, create, rename, remove, addStudy, removeStudy, setSamples };
}

// The header the server snapshots onto aggregation_studies so the tab can
// render a Browse-style card without a Qiita round-trip. Search results carry
// no is_gold key (only the GOLD list does), hence the coercion.
const _aggStudyBody = (study) => ({
  study_id: study.study_id,
  study_title: study.study_title,
  study_abstract: study.study_abstract ?? null,
  data_types: study.data_types || '',
  num_samples: study.num_samples ?? null,
  num_preps: study.num_preps ?? null,
  pi_name: study.pi_name ?? null,
  pi_affiliation: study.pi_affiliation ?? null,
  year: study.year ?? null,
  is_gold: !!study.is_gold,
});

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

function AggregationsTab({ agg }) {
  const [activeId,  setActiveId]  = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editVal,   setEditVal]   = useState('');
  // {study_id, sample_id} whose metadata fills the right-hand pane; lives here
  // (not in the detail) so it survives collapsing the study's sample table.
  const [picked,    setPicked]    = useState(null);
  const list   = agg.aggregations;
  const active = (list || []).find(a => a.aggregation_id === activeId) || null;
  useEffect(() => { setPicked(null); }, [activeId]);

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
          ? <AggregationDetail key={active.aggregation_id} a={active} agg={agg} picked={picked} onPickSample={setPicked} />
          : <div className="agg-detail-empty">Select an aggregation to view it here</div>}
      </div>
      {picked && (
        <div className="agg-meta-col">
          <SampleMetadataPane picked={picked} onClose={() => setPicked(null)} />
        </div>
      )}
    </div>
  );
}
