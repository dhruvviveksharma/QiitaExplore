// The chat's workspace card (backend helpers/workspace_tools.py: add_to_workspace /
// create_workspace). The tool already wrote; on mount the card syncs that workspace
// into the sidebar once (ctx.workspaces, app_state.js — patched from the response,
// never a reload), and never repeats the add, so a reloaded chat is safe.
// Undo removes the studies this call added, and the workspace itself when this
// call created it and nothing else is in it.
// Globals in scope: React, useState, useEffect, useRef (utils.js), apiJson (utils.js),
//   WidgetHead (chat_study_widgets.js)

function WorkspaceUpdateCard({ p, ctx }) {
  const ws = ctx.workspaces;
  const live = (ws?.list || []).find(w => w.project_id === p.project_id);
  const [gone, setGone] = useState(false);
  const [undo, setUndo] = useState({ state: 'idle', msg: '' });
  const synced = useRef(false);

  useEffect(() => {
    if (synced.current || !ws) return;
    synced.current = true;
    if (live && (!p.updated_at || (live.updated_at || '') >= p.updated_at)) return;
    apiJson(`/projects/${p.project_id}`).then(ws.apply).catch(() => setGone(true));
  }, []);

  const doUndo = async () => {
    setUndo({ state: 'busy', msg: '' });
    try {
      let proj = null;
      for (const a of p.added || []) {
        proj = await apiJson(`/projects/${p.project_id}/studies/${a.study_id}`, { method: 'DELETE' });
      }
      proj = proj || await apiJson(`/projects/${p.project_id}`);
      if (p.created && !(proj.studies || []).length && !(proj.chats || []).length) {
        await apiJson(`/projects/${p.project_id}`, { method: 'DELETE' });
        ws?.forget(p.project_id);
      } else {
        ws?.apply(proj);
      }
      setUndo({ state: 'done', msg: '' });
    } catch (e) {
      setUndo({ state: 'error', msg: e.message || 'Could not undo' });
    }
  };

  const added = p.added || [], skipped = p.skipped || [];
  const title = p.created ? `Created workspace "${p.name}"` : `Workspace "${p.name}"`;
  return (
    <>
      <WidgetHead title={title}
        sub={added.length ? `+${added.length} stud${added.length === 1 ? 'y' : 'ies'} · ${p.study_count} in all` : null}
        ctx={ctx} />
      <div className="cw-ws">
        {added.map(a => (
          <div key={a.study_id} className="cw-ws-study">
            <span className="study-id-badge">ID {a.study_id}</span>
            <span className="cw-ws-title">{a.study_title || 'Untitled study'}</span>
          </div>
        ))}
        {skipped.map(s => (
          <div key={s.study_id} className="cw-ws-study cw-ws-skipped">
            <span className="study-id-badge">ID {s.study_id}</span>
            <span className="cw-ws-title">not added: {s.reason}</span>
          </div>
        ))}
        {gone ? <div className="cw-note">This workspace has since been deleted.</div> : (
          <div className="cw-agg-row">
            {undo.state === 'done' ? <span className="cw-note">Undone.</span>
              : (added.length > 0 || p.created) && (
                <button className="btn-card-ctx" disabled={undo.state === 'busy'} onClick={doUndo}>
                  {undo.state === 'busy' ? 'Undoing…' : 'Undo'}</button>
              )}
            {undo.state !== 'done' && (
              <button type="button" className="an-link" onClick={() => ws?.open(p.project_id)}>
                Open workspace →</button>
            )}
          </div>
        )}
        {undo.state === 'error' && <div className="browse-error">{undo.msg}</div>}
      </div>
    </>
  );
}
