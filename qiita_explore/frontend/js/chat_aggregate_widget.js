// The chat's "add to sample aggregation" confirm card (propose_aggregation_add,
// backend helpers/study_tools.py). The tool only proposes: scope, counts, a
// suggested target. Nothing changes until Add, which goes through the app's
// one useAggregations instance (ctx.agg), so the Sample Aggregation tab shows
// the study without a refresh. Which aggregations already hold the study is
// read from that live list, so the card is right after a reload too.
// Globals in scope: React, useState (utils.js), apiJson (utils.js), useDropdown (hooks/useDropdown.js),
//   ChevronIcon (icons.js), _aggStudyBody (aggregations.js), WidgetHead, ChatStudyCard (chat_study_widgets.js)

const _AGG_STUDY_CAP = 50;   // AGGREGATION_STUDIES_CAP, store/aggregation_crud.py
const _n = x => (x ?? 0).toLocaleString();

function AggregationProposalCard({ p, ctx }) {
  const agg = ctx.agg;
  const list = agg?.aggregations || [];
  const has = a => (a.studies || []).some(s => +s.study_id === +p.study_id);
  const usable = a => !has(a) && (a.studies || []).length < _AGG_STUDY_CAP;
  const [target, setTarget] = useState(null);       // null = the default below
  const [newName, setNewName] = useState(p.suggest && !p.suggest.aggregation_id ? p.suggest.name : '');
  const [status, setStatus] = useState({ state: 'idle', msg: '' });
  const dd = useDropdown();

  const fallback = () => {
    const sug = p.suggest;
    if (sug?.aggregation_id && list.some(a => a.aggregation_id === sug.aggregation_id && usable(a))) return sug.aggregation_id;
    if (sug && !sug.aggregation_id) return 'new';
    return list.find(usable)?.aggregation_id || 'new';
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
      let aid = chosen, name = chosenAgg?.name;
      if (chosen === 'new') {
        const a = await agg.create(newName.trim() || 'Untitled');
        aid = a.aggregation_id; name = a.name;
      }
      await agg.addStudy(aid, _aggStudyBody(header), p.file_filter || undefined);
      setStatus({ state: 'done', msg: name });
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
        ) : (
          <div className="cw-agg-row">
            <span className="cw-agg-label">Aggregation</span>
            <div className="dd-root" ref={dd.rootRef}>
              <button type="button" ref={dd.btnRef} className="dd-trigger" onClick={dd.toggle} disabled={!agg?.aggregations}>
                <span className="dd-trigger-label">
                  {!agg?.aggregations ? 'Loading…' : chosen === 'new' ? '+ New aggregation' : chosenAgg?.name || 'Choose…'}
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
                  {list.length > 0 && <div className="cr-menu-sep" />}
                  <button className="cr-menu-item" onClick={() => { setTarget('new'); dd.setOpen(false); }}>+ New aggregation…</button>
                </div>
              )}
            </div>
            {chosen === 'new' && (
              <input className="cw-agg-name" placeholder="New aggregation name" value={newName}
                onChange={e => setNewName(e.target.value)} />
            )}
            <button className="btn-card-add" onClick={add}
              disabled={!agg?.aggregations || status.state === 'adding' || (chosen !== 'new' && !chosenAgg)}>
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
