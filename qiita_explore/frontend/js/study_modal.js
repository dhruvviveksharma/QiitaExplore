// Study detail modal — extracted from app_render.js (TKT-011). The header and
// sections are shared with the study page (study_detail.js).
// Globals in scope: React, useState, useEffect, useRef (utils.js),
//   apiFetch, apiPost (utils.js), StudyHeader, StudyDetailBody (study_detail.js),
//   ExpandCompressIcon (icons.js)

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

// ── Study modal ────────────────────────────────────────────────────────────────

function StudyModal({ study, detail, loading, onClose, shareUrl, drawerOpen, actions, onOpenPage }) {
  const [fullscreen, setFullscreen] = useState(false);
  const cardRef = useRef(null);

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Scrolling down in the compact view auto-expands to fullscreen for more
  // room; scrolling back up reverses it. (The expand button opens the study
  // page instead, so fullscreen only ever comes from scrolling.)
  const handleCardScroll = () => {
    const top = cardRef.current?.scrollTop ?? 0;
    if (!fullscreen && top > 0) setFullscreen(true);
  };

  // Collapse is driven by wheel direction rather than scrollTop position:
  // when the card's content is short (e.g. Samples/Outputs collapsed), the
  // fullscreen card has no scrollable range at all — scrollTop is pinned at
  // 0 — so scroll position alone can't tell "user scrolled back up" from
  // "there was never anything to scroll," which used to make the card
  // expand and immediately collapse again in a flicker.
  const handleCardWheel = e => {
    const top = cardRef.current?.scrollTop ?? 0;
    if (fullscreen && top <= 0 && e.deltaY < 0) setFullscreen(false);
  };

  return (
    <div className={`modal-overlay${drawerOpen ? ' with-drawer' : ''}`} onClick={onClose}>
      <div ref={cardRef} className={`modal-card${fullscreen ? ' modal-fullscreen' : ''}`}
        onClick={e => e.stopPropagation()} onScroll={handleCardScroll} onWheel={handleCardWheel}>
        <StudyHeader study={study} detail={detail} loading={loading} shareUrl={shareUrl}
          right={<>
            {/* Same row as the Browse card (js/study_actions.js), in both sizes. */}
            {actions && <div className="modal-header-actions">{actions}</div>}
            {onOpenPage && (
              <button className="modal-expand" title="Open as page" onClick={onOpenPage}>
                <ExpandCompressIcon expanded={false} />
              </button>
            )}
            <button className="modal-close" onClick={onClose}>×</button>
          </>} />
        <StudyDetailBody key={study.study_id} study={study} detail={detail} loading={loading} />
      </div>
    </div>
  );
}
