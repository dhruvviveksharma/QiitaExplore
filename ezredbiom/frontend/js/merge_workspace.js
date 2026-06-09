// Merge Workspace panel — browse studies, pick BIOMs, validate, merge

function MergePanelNameField({ name, workspaceId, onRename }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(name);
  async function save() {
    const t = val.trim(); setEditing(false);
    if (!t || t === name) { setVal(name); return; }
    const res = await apiPatch(`/merge-workspaces/${workspaceId}`, { name: t });
    if (res.ok) onRename(t); else setVal(name);
  }
  if (editing) return <input className="merge-name-edit-input" value={val} autoFocus
    onChange={e => setVal(e.target.value)} onBlur={save}
    onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setEditing(false); setVal(name); } }} />;
  return <div className="merge-panel-name editable" onClick={() => setEditing(true)} title="Click to rename">{name} <span className="rename-hint">✎</span></div>;
}

function MergeWorkspacePanel({ workspaceId, setWorkspaceId, pendingStudy, clearPendingStudy, onClose }) {
  const [workspace,    setWorkspace]    = useState(null);
  const [validation,   setValidation]   = useState(null);
  const [validating,   setValidating]   = useState(false);
  const [jobId,        setJobId]        = useState(null);
  const [wsNameInput,  setWsNameInput]  = useState('Merge Workspace');
  const [merging,      setMerging]      = useState(false);
  const [error,        setError]        = useState('');
  const [existingWs,   setExistingWs]   = useState(null);
  const [studyDetails, setStudyDetails] = useState(new Map());

  function handleDetailLoaded(studyId, detail) {
    setStudyDetails(prev => new Map(prev).set(studyId, detail));
  }

  function handleGlobalApply(selections) {
    selections.forEach(({ studyId, artifactId }) => handleToggleArtifact(studyId, artifactId));
  }

  // Consume any study queued before the panel was mounted
  useEffect(() => {
    if (pendingStudy) {
      handleAddStudy(pendingStudy);
      clearPendingStudy();
    }
  }, [pendingStudy]);

  // Load pre-existing workspace on mount only.
  // Do NOT depend on workspaceId: ensureWorkspace() sets both workspaceId and
  // workspace state directly, so re-running loadWorkspace after creation would
  // race against the addStudy POST and overwrite the workspace with an empty list.
  useEffect(() => {
    if (workspaceId) loadWorkspace(workspaceId);
    else apiFetch('/merge-workspaces?user_id=default').then(r => r.ok ? r.json() : []).then(setExistingWs);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-run validation whenever study list changes
  useEffect(() => {
    if (workspaceId && workspace?.studies?.length) runValidation(workspaceId);
  }, [workspace?.studies?.length, workspaceId]);

  async function ensureWorkspace() {
    if (workspaceId) return workspaceId;
    const res = await apiPost('/merge-workspaces', { user_id: 'default', name: wsNameInput });
    if (!res.ok) { setError('Failed to create workspace'); return null; }
    const ws = await res.json();
    setWorkspaceId(ws.workspace_id);
    setWorkspace(ws);
    return ws.workspace_id;
  }

  async function loadWorkspace(wsId) {
    const res = await apiFetch(`/merge-workspaces/${wsId}?user_id=default`);
    if (res.ok) setWorkspace(await res.json());
  }

  async function selectExisting(wsId) {
    setWorkspaceId(wsId);
    await loadWorkspace(wsId);
  }

  async function runValidation(wsId) {
    setValidating(true);
    const res = await apiFetch(`/merge-workspaces/${wsId}/validate?user_id=default`);
    if (res.ok) setValidation(await res.json());
    setValidating(false);
  }

  async function handleAddStudy(study) {
    setError('');
    try {
      const wsId = await ensureWorkspace();
      if (!wsId) return;
      const res = await apiPost(`/merge-workspaces/${wsId}/studies`, {
        study_id: study.study_id,
        study_title: study.study_title,
        data_types: study.data_types,
        num_samples: study.num_samples,
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(d.error || 'Failed to add study');
        return;
      }
      const data = await res.json();
      setWorkspace(prev => ({ ...prev, studies: data.studies }));
    } catch (e) {
      setError('Network error: ' + e.message);
    }
  }

  async function handleRemoveStudy(studyId) {
    if (!workspaceId) return;
    const res = await apiDel(`/merge-workspaces/${workspaceId}/studies/${studyId}`);
    if (res.ok) {
      const data = await res.json();
      setWorkspace(prev => ({ ...prev, studies: data.studies }));
    }
  }

  async function handleToggleArtifact(studyId, artifactId) {
    if (!workspaceId) return;
    const cur = workspace?.studies?.find(s => s.study_id === studyId)?.chosen_artifact_ids || [];
    const next = cur.includes(artifactId) ? cur.filter(id => id !== artifactId) : [...cur, artifactId];
    const res = await apiFetch(`/merge-workspaces/${workspaceId}/studies/${studyId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chosen_artifact_ids: next }),
    });
    if (res.ok) { const d = await res.json(); setWorkspace(p => ({ ...p, studies: d.studies })); }
  }

  async function handleMerge() {
    if (!workspaceId || merging) return;
    setMerging(true); setError('');
    const res = await apiPost(`/merge-workspaces/${workspaceId}/jobs`, { user_id: 'default' });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError(d.error || 'Failed to start merge');
      setMerging(false);
      return;
    }
    const job = await res.json();
    setJobId(job.job_id);
    setMerging(false);
  }

  async function handleDeleteWorkspace() {
    if (!workspaceId) return;
    await apiDel(`/merge-workspaces/${workspaceId}?user_id=default`);
    setWorkspaceId(null); setWorkspace(null); setValidation(null); setJobId(null);
  }

  function handleBack() {
    setWorkspaceId(null); setWorkspace(null); setValidation(null); setJobId(null); setError('');
    if (!existingWs) apiFetch('/merge-workspaces?user_id=default').then(r => r.ok ? r.json() : []).then(setExistingWs);
  }

  const studies = workspace?.studies || [];

  return (
    <div className="merge-panel">
      <div className="merge-panel-header">
        {workspaceId && <button className="merge-btn-ghost" onClick={handleBack} title="Back to list">←</button>}
        <span className="merge-panel-title">⊕ Merge Workspace</span>
        <span style={{flex:1}} />
        {workspaceId && <button className="merge-btn-ghost" onClick={handleDeleteWorkspace} title="Delete workspace">🗑</button>}
        <button className="merge-btn-ghost" onClick={onClose}>✕</button>
      </div>

      {!workspaceId && (
        <div className="merge-panel-create">
          {existingWs && existingWs.length > 0 && (
            <>
              <div className="merge-picker-label">Open existing</div>
              <div className="merge-ws-picker">
                {existingWs.map(ws => (
                  <div key={ws.workspace_id} className="merge-ws-picker-item"
                       onClick={() => selectExisting(ws.workspace_id)}>{ws.name}</div>
                ))}
              </div>
              <div className="merge-picker-label" style={{marginTop:'10px'}}>New workspace</div>
            </>
          )}
          <input className="merge-name-input" value={wsNameInput}
            onChange={e => setWsNameInput(e.target.value)} placeholder="Workspace name" />
          <p className="merge-hint">Click "+ Merge" on any study card to begin.</p>
        </div>
      )}

      {workspaceId && workspace && (
        <MergePanelNameField
          name={workspace.name}
          workspaceId={workspaceId}
          onRename={name => setWorkspace(w => ({ ...w, name }))}
        />
      )}

      {error && <div className="merge-error">{error}</div>}

      {/* Global BIOM selector */}
      {workspaceId && (
        <GlobalBiomSelector workspace={workspace} studyDetails={studyDetails} onApply={handleGlobalApply} />
      )}

      {/* Study slots */}
      <div className="merge-slots">
        {studies.map(slot => (
          <MergeStudySlot
            key={slot.study_id}
            slot={slot}
            validationStudy={validation?.studies?.find(s => s.study_id === slot.study_id)}
            onRemove={() => handleRemoveStudy(slot.study_id)}
            onToggleArtifact={(aId) => handleToggleArtifact(slot.study_id, aId)}
            onDetailLoaded={handleDetailLoaded}
          />
        ))}
        {studies.length === 0 && workspaceId && (
          <p className="merge-empty">Click "+ Merge" on a study card to add it here.</p>
        )}
        {studies.length < 5 && !workspaceId && (
          <p className="merge-empty">No studies added yet.</p>
        )}
      </div>

      {/* Validation */}
      {workspaceId && studies.length > 0 && (
        <MergeValidationPanel validation={validation} validating={validating} />
      )}

      {/* Merge button */}
      {workspaceId && studies.length >= 2 && (
        <div className="merge-actions">
          <button
            className="merge-btn-primary"
            disabled={merging || !validation?.compatible}
            onClick={handleMerge}
          >
            {merging ? 'Submitting…' : 'Merge'}
          </button>
          {!validation?.compatible && validation && (
            <span className="merge-btn-hint">Fix errors above to enable</span>
          )}
        </div>
      )}

      {/* Job status */}
      {jobId && (
        <MergeJobStatus jobId={jobId} onReset={() => setJobId(null)} />
      )}

      {/* Past jobs */}
      {workspaceId && !jobId && (
        <MergeJobHistory workspaceId={workspaceId} />
      )}
    </div>
  );
}


function MergeStudySlot({ slot, validationStudy, onRemove, onToggleArtifact, onDetailLoaded }) {
  const [detail, setDetail]     = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [open, setOpen]         = useState(false);
  const [prepFilter, setPrepFilter] = useState('');

  async function loadDetail() {
    if (detail) return;
    setDetailLoading(true);
    const res = await apiFetch(`/studies/${slot.study_id}/detail`);
    if (res.ok) {
      const d = await res.json();
      setDetail(d);
      if (onDetailLoaded) onDetailLoaded(slot.study_id, d);
    }
    setDetailLoading(false);
  }

  function handleToggle() {
    if (!open) loadDetail();
    setOpen(p => !p);
  }

  const autoId = validationStudy?.auto_artifact?.artifact_id;
  const chosenIds = slot.chosen_artifact_ids?.length ? slot.chosen_artifact_ids : (autoId ? [autoId] : []);
  const graph = detail?.artifact_graph || [];
  const hasOrphans = graph.length > 0 && graph.some(n => !prepReachableSet(graph).has(n.node_id));

  return (
    <div className="merge-study-slot">
      <div className="merge-slot-header">
        <span className="merge-slot-id">ID {slot.study_id}</span>
        <span className="merge-slot-title">{slot.study_title || 'Untitled'}</span>
        <button className="merge-btn-ghost merge-detail-toggle" onClick={handleToggle}>
          {open ? '▲ Hide' : '▼ Detail'}
        </button>
        <button className="merge-btn-ghost merge-slot-remove" onClick={onRemove} title="Remove">✕</button>
      </div>

      {slot.data_types && (
        <div className="merge-slot-types">
          {slot.data_types.split(',').map(t => t.trim()).filter(Boolean).map(t => (
            <span key={t} className="dtype-chip">{t}</span>
          ))}
        </div>
      )}

      {open && (
        <div className="merge-slot-detail">
          <div className="merge-slot-section">
            <div className="merge-slot-section-title">Preps</div>
            <PrepsTable detail={detail} loading={detailLoading} />
          </div>
          <div className="merge-slot-section">
            <div className="merge-slot-section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              Artifacts
              {detail && (detail.preps || []).length > 0 && (
                <select
                  value={prepFilter}
                  onChange={e => {
                    const v = e.target.value;
                    setPrepFilter(v === '' ? '' : v === 'other' ? 'other' : +v);
                  }}
                  className="merge-dt-select"
                >
                  <option value="">All preps</option>
                  {(detail.preps || []).map(p => (
                    <option key={p.prep_template_id} value={p.prep_template_id}>
                      Prep {p.prep_template_id} · {p.data_type || '?'}
                    </option>
                  ))}
                  {hasOrphans && <option value="other">Other</option>}
                </select>
              )}
            </div>
            <ArtifactGraphView detail={detail} loading={detailLoading}
              chosenIds={chosenIds} onToggleArtifact={onToggleArtifact} prepFilter={prepFilter} />
          </div>
        </div>
      )}
    </div>
  );
}

function MergeValidationPanel({ validation, validating }) {
  if (validating) return <div className="merge-validation validating">Validating…</div>;
  if (!validation) return null;

  const { compatible, warnings = [], errors = [] } = validation;
  return (
    <div className={`merge-validation ${compatible ? 'ok' : 'fail'}`}>
      <div className="merge-val-status">
        {compatible ? '✓ Compatible' : '✗ Incompatible'}
      </div>
      {errors.map((e, i) => <div key={i} className="merge-val-error">⊘ {e}</div>)}
      {warnings.map((w, i) => <div key={i} className="merge-val-warn">⚠ {w}</div>)}
    </div>
  );
}

function MergeJobStatus({ jobId, onReset }) {
  const [job, setJob] = useState(null);

  useEffect(() => {
    let timer;
    async function poll() {
      const res = await apiFetch(`/merge-jobs/${jobId}`);
      if (!res.ok) return;
      const j = await res.json();
      setJob(j);
      if (j.status !== 'done' && j.status !== 'failed') {
        timer = setTimeout(poll, 3000);
      }
    }
    poll();
    return () => clearTimeout(timer);
  }, [jobId]);

  if (!job) return <div className="merge-job-status">Queued…</div>;

  return (
    <div className={`merge-job-status ${job.status}`}>
      {job.status === 'pending'  && <><div className="spinner" style={{display:'inline-block',marginRight:6}}/> Waiting…</>}
      {job.status === 'running'  && <><div className="spinner" style={{display:'inline-block',marginRight:6}}/> Merging…</>}
      {job.status === 'done'     && (
        <div>
          <span>✓ Done — </span>
          <a href={`${API}/merge-jobs/${jobId}/download`} className="merge-download-link">
            Download bundle
          </a>
          <button className="merge-btn-ghost" style={{marginLeft:8}} onClick={onReset}>↺ New job</button>
        </div>
      )}
      {job.status === 'failed'   && (
        <div>
          <span className="merge-val-error">✗ Failed: {job.error_message}</span>
          <button className="merge-btn-ghost" style={{marginLeft:8}} onClick={onReset}>↺ Retry</button>
        </div>
      )}
    </div>
  );
}

function MergeJobHistory({ workspaceId }) {
  const [jobs, setJobs] = useState([]);

  useEffect(() => {
    apiFetch(`/merge-workspaces/${workspaceId}/jobs`)
      .then(r => r.ok ? r.json() : [])
      .then(setJobs);
  }, [workspaceId]);

  const doneJobs = jobs.filter(j => j.status === 'done');
  if (!doneJobs.length) return null;

  return (
    <div className="merge-job-history">
      <div className="merge-slot-label">Past merges</div>
      {doneJobs.slice(0, 3).map(j => (
        <div key={j.job_id} className="merge-job-row">
          <span className="merge-job-date">{(j.created_at || '').slice(0, 10)}</span>
          <a href={`${API}/merge-jobs/${j.job_id}/download`} className="merge-download-link">
            Download
          </a>
        </div>
      ))}
    </div>
  );
}

function MergesTab({ onOpenWorkspace, activeWorkspaceId }) {
  const [workspaces, setWorkspaces] = useState(null);
  const [editingId,  setEditingId]  = useState(null);
  const [editVal,    setEditVal]    = useState('');

  useEffect(() => {
    apiFetch('/merge-workspaces?user_id=default')
      .then(r => r.ok ? r.json() : [])
      .then(async (list) => {
        const detailed = await Promise.all(
          list.map(ws =>
            apiFetch(`/merge-workspaces/${ws.workspace_id}?user_id=default`)
              .then(r => r.ok ? r.json() : ws)
          )
        );
        setWorkspaces(detailed);
      });
  }, []);

  async function saveRename(wsId) {
    const t = editVal.trim();
    setEditingId(null);
    if (!t) return;
    const res = await apiPatch(`/merge-workspaces/${wsId}`, { name: t });
    if (res.ok) setWorkspaces(list => list.map(w => w.workspace_id === wsId ? { ...w, name: t } : w));
  }

  if (workspaces === null) return <div className="merges-loading">Loading…</div>;

  if (workspaces.length === 0) return (
    <div className="merges-empty">
      <p>No merge workspaces yet.</p>
      <p>Click <strong>⊕ Merge</strong> on any study card to create one.</p>
    </div>
  );

  return (
    <div className="merges-tab">
      {workspaces.map(ws => (
        <div key={ws.workspace_id} className={`merge-ws-card${ws.workspace_id === activeWorkspaceId ? ' active' : ''}`} onClick={() => onOpenWorkspace(ws.workspace_id)}>
          <div className="merge-ws-card-header">
            {editingId === ws.workspace_id
              ? <input className="merge-ws-name-input" value={editVal} autoFocus
                  onChange={e => setEditVal(e.target.value)}
                  onBlur={() => saveRename(ws.workspace_id)}
                  onKeyDown={e => { if (e.key === 'Enter') saveRename(ws.workspace_id); if (e.key === 'Escape') setEditingId(null); }}
                  onClick={e => e.stopPropagation()} />
              : <span className="merge-ws-name" onClick={e => { e.stopPropagation(); setEditingId(ws.workspace_id); setEditVal(ws.name); }}>
                  {ws.name} <span className="rename-hint">✎</span>
                </span>
            }
            <span className="merge-btn-ghost">Open ↗</span>
          </div>
          <div className="merge-ws-studies">
            {(ws.studies || []).length === 0
              ? <span className="merge-ws-no-studies">No studies added</span>
              : (ws.studies || []).map(s => (
                  <div key={s.study_id} className="merge-ws-study-row">
                    <span className="merge-ws-study-id">#{s.study_id}</span>
                    <span className="merge-ws-study-title">{s.study_title || 'Untitled'}</span>
                    {s.data_types && <span className="dtype-chip">{s.data_types}</span>}
                  </div>
                ))
            }
          </div>
        </div>
      ))}
    </div>
  );
}
