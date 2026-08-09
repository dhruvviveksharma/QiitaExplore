// Study detail modal — extracted from app_render.js (TKT-011)
// Globals in scope: React, useState, useEffect, useRef (utils.js),
//   apiFetch, apiPost, apiPatch (utils.js), PrepsTable, SamplesBrowser,
//   ArtifactsTable (components.js), ArtifactOutputsView, prepReachableSet
//   (merge_artifacts.js), ProvenanceForest (merge_tree.js)

// ── Add to project ────────────────────────────────────────────────────────────

function AddToProjectBar({ study }) {
  const [projects,   setProjects]   = useState(null);
  const [selected,   setSelected]   = useState('');
  const [newName,    setNewName]    = useState('');
  const [adding,     setAdding]     = useState(false);
  const [msg,        setMsg]        = useState('');

  useEffect(() => {
    apiFetch('/projects')
      .then(r => r.ok ? r.json() : { projects: [] })
      .then(d => {
        const list = d.projects || [];
        setProjects(list);
        if (list.length > 0) setSelected(list[0].project_id);
      });
  }, []);

  async function handleAdd() {
    setAdding(true); setMsg('');
    try {
      let projId = selected;
      let projName = '';
      if (!projId) {
        const name = newName.trim() || 'New Project';
        const res = await apiPost('/projects', { name });
        if (!res.ok) { setMsg('Failed to create project.'); setAdding(false); return; }
        const p = await res.json();
        projId = p.project_id;
        projName = p.name;
      } else {
        projName = projects.find(p => p.project_id === projId)?.name || projId;
      }
      const addRes = await apiPost(`/projects/${projId}/studies`, {
        study: {
          study_id: study.study_id,
          study_title: study.study_title,
          data_types: study.data_types || '',
          num_samples: study.num_samples || 0,
        },
      });
      if (!addRes.ok) {
        const err = await addRes.json().catch(() => ({}));
        setMsg(err.error || `Error ${addRes.status}`);
        setAdding(false); return;
      }
      setMsg(`Added to "${projName}"`);
    } catch (e) {
      setMsg('Unexpected error.');
    }
    setAdding(false);
  }

  if (projects === null) return null;
  const isNew = selected === '';
  return (
    <div className="modal-ws-row">
      <span className="modal-ws-label">Add to project</span>
      <select className="merge-dt-select" value={selected}
        onChange={e => { setSelected(e.target.value); setMsg(''); }}>
        {projects.map(p => (
          <option key={p.project_id} value={p.project_id}>
            {p.name} ({(p.studies || []).length})
          </option>
        ))}
        <option value="">+ New project…</option>
      </select>
      {isNew && (
        <input className="merge-name-filter" placeholder="Project name"
          value={newName} onChange={e => setNewName(e.target.value)} />
      )}
      <button className="merge-btn-primary" style={{ padding: '4px 10px', fontSize: '0.8rem' }}
        disabled={adding} onClick={handleAdd}>
        {adding ? 'Adding…' : 'Add'}
      </button>
      {msg && <span className="modal-ws-msg">{msg}</span>}
    </div>
  );
}

// ── Add to merge workspace ─────────────────────────────────────────────────────

function AddToMergeBar({ study }) {
  const [workspaces, setWorkspaces] = useState(null);
  const [selected,   setSelected]   = useState('');
  const [newName,    setNewName]    = useState('');
  const [adding,     setAdding]     = useState(false);
  const [msg,        setMsg]        = useState('');

  useEffect(() => {
    apiFetch('/merge-workspaces')
      .then(r => r.ok ? r.json() : [])
      .then(list => {
        setWorkspaces(list);
        if (list.length > 0) setSelected(list[0].workspace_id);
      });
  }, []);

  async function handleAdd() {
    setAdding(true); setMsg('');
    try {
      let wsId = selected;
      let wsName = '';
      if (!wsId) {
        const name = newName.trim() || 'New Merge';
        const res = await apiPost('/merge-workspaces', { name });
        if (!res.ok) { setMsg('Failed to create merge.'); setAdding(false); return; }
        const ws = await res.json();
        wsId = ws.workspace_id;
        wsName = ws.name;
      } else {
        wsName = workspaces.find(w => w.workspace_id === wsId)?.name || wsId;
      }
      const addRes = await apiPost(`/merge-workspaces/${wsId}/studies`, {
        study_id: study.study_id,
        study_title: study.study_title,
        data_types: study.data_types || '',
        num_samples: study.num_samples || 0,
      });
      if (!addRes.ok) {
        const err = await addRes.json().catch(() => ({}));
        setMsg(err.error || `Error ${addRes.status}`);
        setAdding(false); return;
      }
      setMsg(`Added to "${wsName}"`);
    } catch (e) {
      setMsg('Unexpected error.');
    }
    setAdding(false);
  }

  if (workspaces === null) return null;
  const isNew = selected === '';
  return (
    <div className="modal-ws-row">
      <span className="modal-ws-label">Add to merge</span>
      <select className="merge-dt-select" value={selected}
        onChange={e => { setSelected(e.target.value); setMsg(''); }}>
        {workspaces.map(w => (
          <option key={w.workspace_id} value={w.workspace_id}>
            {w.name} ({(w.studies || []).length})
          </option>
        ))}
        <option value="">+ New merge…</option>
      </select>
      {isNew && (
        <input className="merge-name-filter" placeholder="Merge name"
          value={newName} onChange={e => setNewName(e.target.value)} />
      )}
      <button className="merge-btn-primary" style={{ padding: '4px 10px', fontSize: '0.8rem' }}
        disabled={adding} onClick={handleAdd}>
        {adding ? 'Adding…' : 'Add'}
      </button>
      {msg && <span className="modal-ws-msg">{msg}</span>}
    </div>
  );
}

function StudyActionBar({ study }) {
  return (
    <div className="modal-ws-bar">
      <AddToProjectBar study={study} />
      <AddToMergeBar study={study} />
    </div>
  );
}

// ── Study outputs section (tree + workspace bar) ───────────────────────────────

function StudyModalOutputs({ study, detail, loading }) {
  const [sampleCounts, setSampleCounts] = useState({});
  const [prepFilter,   setPrepFilter]   = useState('');

  useEffect(() => {
    if (!detail?.artifact_graph) return;
    const biomIds = detail.artifact_graph
      .filter(n => n.kind === 'artifact' && n.artifact_type === 'BIOM' && n.artifact_id)
      .map(n => n.artifact_id);
    if (!biomIds.length) return;
    apiPost('/artifacts/sample-counts', { study_id: study.study_id, artifact_ids: biomIds })
      .then(r => r.ok ? r.json() : {})
      .then(counts => setSampleCounts(prev => ({ ...prev, ...counts })));
  }, [detail]);

  const graph = detail?.artifact_graph || [];
  const reachable = prepReachableSet(graph);
  const hasOrphans = graph.length > 0 && graph.some(n => !reachable.has(n.node_id));

  return (
    <div>
      {detail && (detail.preps || []).length > 1 && (
        <div style={{ marginBottom: 8 }}>
          <select className="merge-dt-select" value={prepFilter}
            onChange={e => {
              const v = e.target.value;
              setPrepFilter(v === '' ? '' : v === 'other' ? 'other' : +v);
            }}>
            <option value="">All preps</option>
            {(detail.preps || []).map(p => (
              <option key={p.prep_template_id} value={p.prep_template_id}>
                Prep {p.prep_template_id} · {p.data_type || '?'}
              </option>
            ))}
            {hasOrphans && <option value="other">Other</option>}
          </select>
        </div>
      )}
      <ArtifactOutputsView
        detail={detail} loading={loading}
        chosenIds={[]} onToggleArtifact={() => {}}
        prepFilter={prepFilter} recommendedId={null}
        sampleCounts={sampleCounts} studyId={study.study_id}
        selectable={false}
      />
      <StudyActionBar study={study} />
    </div>
  );
}

// ── Study modal ────────────────────────────────────────────────────────────────

function StudyModal({ study, detail, loading, onClose, shareUrl }) {
  const [fullscreen, setFullscreen] = useState(false);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className={`modal-card${fullscreen ? ' modal-fullscreen' : ''}`}
        onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>×</button>
        <button className="modal-expand" title={fullscreen ? 'Restore' : 'Fullscreen'}
          onClick={() => setFullscreen(p => !p)}>
          {fullscreen ? '⤡' : '⤢'}
        </button>
        {shareUrl && (
          <div className="modal-copy-link">
            <CopyResponseButton title="Copy study link" text={shareUrl} />
          </div>
        )}
        <div className="modal-id">Study ID {study.study_id}</div>
        <div className="modal-title">{study.study_title || 'Untitled study'}</div>

        {!loading && detail?.isPrivate ? (
          <div style={{display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'3rem 1rem', gap:'1rem', color:'#aaa'}}>
            <span style={{fontSize:'4rem', lineHeight:1}}>✕</span>
            <span style={{fontSize:'1rem', fontStyle:'italic', textAlign:'center'}}>This is a private study and its data is not publicly available.</span>
          </div>
        ) : (
          <>
            {(study.data_types || study.num_samples != null || study.num_preps != null) && (
              <div className="modal-stats">
                {splitTypes(study.data_types).map(t => (
                  <span key={t} className="dtype-chip">{t}</span>
                ))}
                {study.num_samples != null && <span className="modal-stat">{study.num_samples} samples</span>}
                {study.num_preps   != null && <span className="modal-stat">{study.num_preps} preps</span>}
              </div>
            )}

            {study.study_abstract && (
              <div className="modal-section"><h4>Abstract</h4><p>{study.study_abstract}</p></div>
            )}
            {study.pi_name && (
              <div className="modal-section">
                <h4>Principal Investigator</h4>
                <p>{study.pi_name}{study.pi_affiliation ? ` — ${study.pi_affiliation}` : ''}</p>
              </div>
            )}
            {study.pi_email && (
              <div className="modal-section"><h4>Contact</h4><p>{study.pi_email}</p></div>
            )}

            <div className="modal-section">
              <h4>Prep Templates</h4>
              <PrepsTable detail={detail} loading={loading} />
            </div>

            {!loading && detail && (
              <div className="modal-section">
                <h4>Samples{detail.total_samples != null ? ` (${detail.total_samples} total${detail.total_samples > 200 ? ', showing first 200' : ''})` : ''}</h4>
                <SamplesBrowser
                  samples={detail.samples || []}
                  layout="two-pane"
                  fetchFields={async (sampleId) => {
                    const res = await apiFetch(`/studies/${study.study_id}/samples/${encodeURIComponent(sampleId)}`);
                    if (!res.ok) return null;
                    const d = await res.json();
                    return d.fields || null;
                  }}
                />
              </div>
            )}

            <div className="modal-section">
              <h4>Outputs</h4>
              <StudyModalOutputs study={study} detail={detail} loading={loading} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
