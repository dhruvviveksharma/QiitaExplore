// The chat's aggregation widgets, all reading the app's one useAggregations
// list (ctx.agg) so they stay right after a reload and update the Sample
// Aggregation tab and the chat's bar (chat_aggregation_bar.js) without a refresh:
//   AggregationProposalCard — propose_aggregation_add: add to a SAVED aggregation
//                             the user named; nothing changes until Add.
//   ChatAggregationUpdate   — add_to_chat_aggregation already added to this chat's
//                             temporary aggregation; Undo reverses that one add.
//   AggregationSavedWidget  — save_chat_aggregation moved it to the tab.
//   AggregationListWidget   — list_aggregations: saved ones and this chat's.
// Globals in scope: React, useState, useEffect, useRef (utils.js), apiJson, splitTypes (utils.js),
//   useDropdown (hooks/useDropdown.js), ChevronIcon (icons.js), _aggStudyBody (aggregations.js),
//   WidgetHead, ChatStudyCard (chat_study_widgets.js), _plural (aggregation_detail.js)

const _AGG_STUDY_CAP = 50;   // AGGREGATION_STUDIES_CAP, store/aggregation_crud.py
const _n = x => (x ?? 0).toLocaleString();

function AggregationProposalCard({ p, ctx }) {
  const agg = ctx.agg;
  const list = (agg?.aggregations || []).filter(a => !a.chat_id);    // saved ones: chats' stay out
  const has = a => (a.studies || []).some(s => +s.study_id === +p.study_id);
  const usable = a => !has(a) && (a.studies || []).length < _AGG_STUDY_CAP;
  const [target, setTarget] = useState(null);       // null = the default below
  const [status, setStatus] = useState({ state: 'idle', msg: '' });
  const dd = useDropdown();

  const fallback = () => {
    const sug = p.suggest;
    if (sug?.aggregation_id && list.some(a => a.aggregation_id === sug.aggregation_id && usable(a))) return sug.aggregation_id;
    return list.find(usable)?.aggregation_id || null;
  };
  const chosen = target ?? (agg?.aggregations ? fallback() : null);
  const chosenAgg = list.find(a => a.aggregation_id === chosen);
  const holding = list.filter(has);
  const { data_types = [], prep_ids = [] } = p.scope || {};
  const scope = [data_types.length && `data type ${data_types.join(', ')}`,
                 prep_ids.length && `prep ${prep_ids.join(', ')}`].filter(Boolean).join(', ') || 'the whole study';
  const c = p.counts || {};

  const add = async () => {
    setStatus({ state: 'adding', msg: '' });
    try {
      const header = await apiJson(`/studies/${p.study_id}`);   // the card snapshot the tab shows
      await agg.addStudy(chosen, _aggStudyBody(header), p.file_filter || undefined);
      setStatus({ state: 'done', msg: chosenAgg.name });
    } catch (e) {
      setStatus({ state: 'error', msg: e.message || 'Could not add the study' });
    }
  };

  return (
    <>
      <ChatStudyCard studyId={p.study_id} seed={{ study_title: p.study_title }} />
      <WidgetHead title="Add to sample aggregation" studyId={p.study_id} ctx={ctx} />
      <div className="cw-agg">
        <div className="cw-agg-scope">
          Scope: {scope} · {_n(c.samples)} samples · {_n(c.rows)} file rows
          {c.rows !== c.study_rows && <span className="cw-files-meta"> (of {_n(c.study_rows)} in the study)</span>}
        </div>
        {(p.warnings || []).map(w => <div key={w} className="cw-note">{w}</div>)}
        {p.blocked ? (
          <div className="cw-note cw-error">Can't add: {p.blocked}.</div>
        ) : status.state === 'done' ? (
          <div className="cw-agg-done">✓ Added to {status.msg} ·{' '}
            <button type="button" className="an-link" onClick={() => ctx.openAggregations?.()}>Open Sample Aggregation →</button>
          </div>
        ) : agg?.aggregations && list.length === 0 ? (
          <div className="cw-note">You have no saved aggregations yet — ask to add it to this chat's aggregation instead.</div>
        ) : (
          <div className="cw-agg-row">
            <span className="cw-agg-label">Aggregation</span>
            <div className="dd-root" ref={dd.rootRef}>
              <button type="button" ref={dd.btnRef} className="dd-trigger" onClick={dd.toggle} disabled={!agg?.aggregations}>
                <span className="dd-trigger-label">
                  {!agg?.aggregations ? 'Loading…' : chosenAgg?.name || 'Choose…'}
                </span>
                <ChevronIcon dir={dd.open ? 'up' : 'down'} size={11} />
              </button>
              {dd.open && dd.pos && (
                <div className={dd.menuClass} style={{ top: dd.pos.top, left: dd.pos.left }} onClick={e => e.stopPropagation()}>
                  {list.map(a => (
                    <button key={a.aggregation_id} className={`cr-menu-item${has(a) ? ' agg-in' : ''}`} disabled={!usable(a)}
                      onClick={() => { setTarget(a.aggregation_id); dd.setOpen(false); }}>
                      <span className="agg-menu-name">{a.name}</span>
                      {has(a) && <span>✓</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button className="btn-card-add" onClick={add}
              disabled={!agg?.aggregations || status.state === 'adding' || !chosenAgg}>
              {status.state === 'adding' ? 'Adding…' : `Add ${_n(c.rows)} rows`}
            </button>
          </div>
        )}
        {status.state !== 'done' && holding.length > 0 && (
          <div className="cw-note">✓ Already in: {holding.map(a => a.name).join(', ')}</div>)}
        {status.state === 'error' && <div className="browse-error">{status.msg}</div>}
      </div>
    </>
  );
}

// Sync a chat-changed aggregation into the live list once, when the list lacks it
// or holds an older copy (the tool wrote server-side; a reload already has it).
function useSyncAggregation(agg, id, updatedAt) {
  const done = useRef(false);
  const live = (agg?.aggregations || []).find(a => a.aggregation_id === id);
  useEffect(() => {
    if (done.current || !agg?.aggregations) return;
    done.current = true;
    if (!live || (updatedAt && (live.updated_at || '') < updatedAt)) agg.sync(id).catch(() => {});
  }, [agg?.aggregations == null]);
  return live;
}

function ChatAggregationUpdate({ p, ctx }) {
  const agg = ctx.agg;
  const live = useSyncAggregation(agg, p.aggregation_id, p.updated_at);
  const [undo, setUndo] = useState({ state: 'idle', msg: '' });
  const doUndo = async () => {
    setUndo({ state: 'busy', msg: '' });
    try { await agg.undoAdd(p.aggregation_id, p.study_id, p.undo); setUndo({ state: 'done', msg: '' }); }
    catch (e) { setUndo({ state: 'error', msg: e.message || 'Could not undo' }); }
  };
  const t = p.totals || {};
  return (
    <>
      <ChatStudyCard studyId={p.study_id} seed={{ study_title: p.study_title }} />
      <WidgetHead title="Added to this chat's aggregation" sub={p.scope} studyId={p.study_id} ctx={ctx} />
      <div className="cw-agg">
        <div className="cw-agg-scope">
          +{_n(p.added_rows)} file rows · the chat's aggregation now holds {_plural(t.studies || 0, 'study', 'studies')},{' '}
          {_n(t.rows)} checked rows
        </div>
        {(p.notes || []).map(x => <div key={x} className="cw-note">{x}</div>)}
        <div className="cw-agg-row">
          {undo.state === 'done' ? <span className="cw-note">Undone.</span> : (
            <button className="btn-card-ctx" disabled={!live || undo.state === 'busy'} onClick={doUndo}>
              {undo.state === 'busy' ? 'Undoing…' : 'Undo'}</button>
          )}
          <button type="button" className="an-link" onClick={() => ctx.openAggregation?.(p.aggregation_id)}>
            View aggregation →</button>
        </div>
        {agg?.aggregations && !live && <div className="cw-note">That aggregation has since been cleared.</div>}
        {undo.state === 'error' && <div className="browse-error">{undo.msg}</div>}
      </div>
    </>
  );
}

function AggregationSavedWidget({ p, ctx }) {
  useSyncAggregation(ctx.agg, p.aggregation_id, p.updated_at);
  return (
    <>
      <WidgetHead title="Saved aggregation" ctx={ctx} />
      <div className="cw-agg-done">
        "{p.name}" is now in the Sample Aggregation tab ·{' '}
        <button type="button" className="an-link" onClick={() => ctx.openAggregation?.(p.aggregation_id)}>Open ↗</button>
      </div>
    </>
  );
}

function _aggFilterText(ff) {
  ff = ff || {};
  return [ff.data_types?.length && `data type ${ff.data_types.join(', ')}`,
          ff.processing?.length && `processing ${ff.processing.join(', ')}`,
          ff.artifacts?.length && `${ff.artifacts.length} artifacts`].filter(Boolean).join('; ');
}

function AggregationListWidget({ p, ctx }) {
  const list = ctx.agg?.aggregations;
  const byId = new Map((list || []).map(a => [a.aggregation_id, a]));
  const temp = p.chat_aggregation_id && byId.get(p.chat_aggregation_id);
  const chatAgg = temp && temp.chat_id ? temp : null;
  const saved = p.name ? (p.aggregation_ids || []).map(id => byId.get(id)).filter(Boolean)
                       : (list || []).filter(a => !a.chat_id);
  const shown = [...(chatAgg ? [chatAgg] : []), ...saved];
  const [open, setOpen] = useState(() => new Set(p.name || shown.length <= 3 ? shown.map(a => a.aggregation_id) : []));
  const toggle = id => setOpen(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  return (
    <>
      <WidgetHead title="Your sample aggregations" sub={list ? String(saved.length) + (chatAgg ? " + this chat's" : '') : null} ctx={ctx} />
      {list == null ? <p className="cw-note">Loading…</p> : shown.length === 0 ? (
        <p className="cw-note">No aggregations yet. Use /aggregate in a chat, or + Aggregate on a study card.</p>
      ) : shown.map(a => {
        const studies = a.studies || [];
        const rows = studies.reduce((n, s) => n + (s.selected_rows || 0), 0);
        const isOpen = open.has(a.aggregation_id);
        return (
          <div key={a.aggregation_id} className="cw-agglist">
            <div className="cw-agglist-head">
              <button type="button" className="cw-agglist-toggle" onClick={() => toggle(a.aggregation_id)}>
                {isOpen ? '▾' : '▸'} <strong>{a.chat_id ? "This chat's aggregation" : a.name}</strong>
                {a.chat_id && <span className="agg-temp-tag">temporary</span>}
              </button>
              <span className="cw-agglist-meta">
                {_plural(studies.length, 'study', 'studies')} · {_n(rows)} rows checked · {(a.updated_at || '').slice(0, 10)}
              </span>
              <span className="cw-spacer" />
              <button type="button" className="an-link" onClick={() => ctx.openAggregation?.(a.aggregation_id)}>Open ↗</button>
            </div>
            {isOpen && studies.map(s => {
              const ft = _aggFilterText(s.file_filter);
              return (
                <div key={s.study_id} className="cw-agglist-study">
                  <span className="study-id-badge">ID {s.study_id}</span>
                  <span className="cw-agglist-title">{s.study_title || 'Untitled study'}</span>
                  {splitTypes(s.data_types).map(t => <span key={t} className="dtype-chip">{t}</span>)}
                  <span className="cw-agglist-meta">
                    {s.rows_v ? `${_n(s.selected_rows)} of ${_n(s.file_rows)} rows checked` : `${_n(s.selected_rows)} samples checked`}
                    {ft ? ` · filter: ${ft}` : ''}
                  </span>
                </div>
              );
            })}
            {isOpen && studies.length === 0 && <div className="cw-note cw-agglist-empty">No studies yet.</div>}
          </div>
        );
      })}
    </>
  );
}
