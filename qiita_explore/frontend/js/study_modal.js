// Study detail modal — extracted from app_render.js (TKT-011)
// Globals in scope: React, useState, useEffect, useRef (utils.js),
//   apiFetch, apiPost (utils.js), PrepsTable, SamplesBrowser (components.js),
//   ArtifactOutputsView, prepReachableSet (merge_artifacts.js),
//   ProvenanceForest (merge_tree.js)

// ── Add to project ────────────────────────────────────────────────────────────

// Single-select dropdown built on the shared useDropdown convention (see
// CLAUDE.md "Established Patterns: Dropdown UI") instead of a native
// <select> — same trigger/panel/dismiss shape as ChatRowMenu's "...", just
// with select-style (left-aligned, below the trigger) positioning, which is
// useDropdown's default.
function ProjectPickerDropdown({ projects, selectedId, onSelect }) {
  const dd = useDropdown();
  const selectedProj = (projects || []).find(p => p.project_id === selectedId);
  const label = selectedId === '' ? '+ New project…' : (selectedProj?.name || 'Select a project');

  return (
    <div className="dd-root" ref={dd.rootRef}>
      <button type="button" ref={dd.btnRef} className="dd-trigger" onClick={dd.toggle}>
        <span className="dd-trigger-label">{label}</span>
        <ChevronIcon dir={dd.open ? 'up' : 'down'} size={11} />
      </button>
      {dd.open && dd.pos && (
        <div className={dd.menuClass} style={{ top: dd.pos.top, left: dd.pos.left }} onClick={e => e.stopPropagation()}>
          {(projects || []).map(p => (
            <button key={p.project_id} className="cr-menu-item"
              onClick={() => { onSelect(p.project_id); dd.setOpen(false); }}>
              {p.name}
            </button>
          ))}
          <div className="cr-menu-sep" />
          <button className="cr-menu-item" onClick={() => { onSelect(''); dd.setOpen(false); }}>
            + New project…
          </button>
        </div>
      )}
    </div>
  );
}

function AddToProjectBar({ study }) {
  const [projects,   setProjects]   = useState(null);
  const [selected,   setSelected]   = useState('');
  const [newName,    setNewName]    = useState('');
  const [adding,     setAdding]     = useState(false);
  const [msg,        setMsg]        = useState('');
  const rowRef = useRef(null);
  const lastRealSelectedRef = useRef('');

  useEffect(() => {
    apiFetch('/projects')
      .then(r => r.ok ? r.json() : { projects: [] })
      .then(d => {
        const list = d.projects || [];
        setProjects(list);
        if (list.length > 0) {
          setSelected(list[0].project_id);
          lastRealSelectedRef.current = list[0].project_id;
        }
      });
  }, []);

  const isNew = selected === '';

  // Clicking (or Escape-ing) away from the "+ New project…" flow should
  // cancel it, not leave the name input stuck open forever — revert to the
  // last real project, mirroring useDropdown.js's own outside-click/Escape
  // idiom.
  useEffect(() => {
    if (!isNew) return;
    const cancel = () => setSelected(lastRealSelectedRef.current || (projects || [])[0]?.project_id || '');
    const onDown = e => { if (rowRef.current && !rowRef.current.contains(e.target)) cancel(); };
    const onKey  = e => { if (e.key === 'Escape') cancel(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [isNew, projects]);

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
  return (
    <div className="modal-ws-row" ref={rowRef}>
      <span className="modal-ws-label">Add to project</span>
      <ProjectPickerDropdown projects={projects} selectedId={selected}
        onSelect={id => {
          setSelected(id);
          if (id) lastRealSelectedRef.current = id;
          setMsg('');
        }} />
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
      {SHOW_MERGES && <AddToMergeBar study={study} />}
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

function StudyModal({ study, detail, loading, onClose, shareUrl, drawerOpen }) {
  const [fullscreen,   setFullscreen]   = useState(false);
  // Tracks whether the CURRENT fullscreen=true came from auto-scroll-expand
  // rather than an explicit click — only an auto-expand auto-collapses.
  const [autoExpanded, setAutoExpanded] = useState(false);
  const cardRef = useRef(null);

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Scrolling down in the compact view auto-expands to fullscreen for more
  // room; scrolling back up reverses it — but only when the expansion was
  // itself automatic. An explicit click on the expand button sticks until
  // the user explicitly restores it, even after scrolling up.
  const handleCardScroll = () => {
    const top = cardRef.current?.scrollTop ?? 0;
    if (!fullscreen && top > 0) {
      setFullscreen(true);
      setAutoExpanded(true);
    }
  };

  // Collapse is driven by wheel direction rather than scrollTop position:
  // when the card's content is short (e.g. Samples/Outputs collapsed), the
  // fullscreen card has no scrollable range at all — scrollTop is pinned at
  // 0 — so scroll position alone can't tell "user scrolled back up" from
  // "there was never anything to scroll," which used to make the card
  // expand and immediately collapse again in a flicker.
  const handleCardWheel = e => {
    const top = cardRef.current?.scrollTop ?? 0;
    if (fullscreen && autoExpanded && top <= 0 && e.deltaY < 0) {
      setFullscreen(false);
      setAutoExpanded(false);
    }
  };

  const toggleFullscreen = () => {
    setAutoExpanded(false); // any explicit click clears the "auto" flag
    setFullscreen(p => !p);
  };

  const showStats = !(!loading && detail?.isPrivate) &&
    (study.data_types || study.num_samples != null || study.num_preps != null);

  return (
    <div className={`modal-overlay${drawerOpen ? ' with-drawer' : ''}`} onClick={onClose}>
      <div ref={cardRef} className={`modal-card${fullscreen ? ' modal-fullscreen' : ''}`}
        onClick={e => e.stopPropagation()} onScroll={handleCardScroll} onWheel={handleCardWheel}>
        <div className="modal-header-bar">
          <div className="modal-header-top">
            <div className="modal-header-left">
              <span className="modal-id">Study ID {study.study_id}</span>
              {shareUrl && (
                <div className="modal-copy-link">
                  <CopyResponseButton title="Copy study link" text={shareUrl} />
                </div>
              )}
            </div>
            <div className="modal-header-right">
              <button className="modal-expand" title={fullscreen ? 'Restore' : 'Fullscreen'}
                onClick={toggleFullscreen}>
                <ExpandCompressIcon expanded={fullscreen} />
              </button>
              <button className="modal-close" onClick={onClose}>×</button>
            </div>
          </div>
          <div className="modal-title">{study.study_title || 'Untitled study'}</div>
          {showStats && (
            <div className="modal-stats">
              {splitTypes(study.data_types).map(t => (
                <span key={t} className="dtype-chip">{t}</span>
              ))}
              {study.num_samples != null && <span className="modal-stat">{study.num_samples} samples</span>}
              {study.num_preps   != null && <span className="modal-stat">{study.num_preps} preps</span>}
            </div>
          )}
        </div>

        {!loading && detail?.isPrivate ? (
          <div style={{display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'3rem 1rem', gap:'1rem', color:'#aaa'}}>
            <span style={{fontSize:'4rem', lineHeight:1}}>✕</span>
            <span style={{fontSize:'1rem', fontStyle:'italic', textAlign:'center'}}>This is a private study and its data is not publicly available.</span>
          </div>
        ) : (
          <>
            {study.study_abstract && (
              <CollapsibleSection id="study-modal-abstract" title="Abstract" defaultOpen>
                <p>{study.study_abstract}</p>
              </CollapsibleSection>
            )}
            {study.pi_name && (
              <CollapsibleSection id="study-modal-pi" title="Principal Investigator" defaultOpen>
                <p>{study.pi_name}{study.pi_affiliation ? ` — ${study.pi_affiliation}` : ''}</p>
              </CollapsibleSection>
            )}
            {study.pi_email && (
              <CollapsibleSection id="study-modal-contact" title="Contact" defaultOpen>
                <p>{study.pi_email}</p>
              </CollapsibleSection>
            )}

            <CollapsibleSection id="study-modal-preps" title="Prep Templates"
              subtitle={detail ? `${(detail.preps || []).length}` : undefined} defaultOpen>
              <PrepsTable detail={detail} loading={loading} />
            </CollapsibleSection>

            {!loading && detail && (
              <CollapsibleSection id="study-modal-samples" title="Samples"
                subtitle={detail.total_samples != null
                  ? `${detail.total_samples} total${detail.total_samples > 200 ? ', showing first 200' : ''}`
                  : undefined}
                defaultOpen>
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
              </CollapsibleSection>
            )}

            <CollapsibleSection id="study-modal-outputs" title="Outputs" defaultOpen>
              <StudyModalOutputs study={study} detail={detail} loading={loading} />
            </CollapsibleSection>
          </>
        )}
      </div>
    </div>
  );
}
