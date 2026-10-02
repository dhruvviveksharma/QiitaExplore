// This chat's temporary aggregation, shown above the composer next to the
// pinned bar while the chat has one (add_to_chat_aggregation creates it). It is
// an ordinary aggregation with chat_id set, read from the app's single
// useAggregations list, so the chat's adds and Undo update it in place.
// View opens it in the Sample Aggregation tab; Save names it and moves it there.
// Globals in scope: React, useState (utils.js), API (utils.js)

function ChatAggregationBar({ agg, chatId, scope, onOpen }) {
  const [busy, setBusy] = useState(false);
  const a = (agg?.aggregations || []).find(x => x.chat_id === chatId && x.chat_scope === scope);
  if (!a) return null;
  const studies = a.studies || [];
  const rows = studies.reduce((n, s) => n + (s.selected_rows || 0), 0);
  const base = `${API}/aggregations/${a.aggregation_id}`;
  const save = async () => {
    const name = prompt('Save this chat aggregation as:', '');
    if (!name || !name.trim()) return;
    setBusy(true);
    try { await agg.save(a.aggregation_id, name.trim()); } finally { setBusy(false); }
  };
  const clear = async () => {
    if (!confirm("Clear this chat's aggregation? Its studies and checked rows are removed.")) return;
    setBusy(true);
    try { await agg.remove(a.aggregation_id); } finally { setBusy(false); }
  };
  return (
    <div className="composer-pins chat-agg-bar">
      <span className="composer-pins-label">Chat aggregation:</span>
      {studies.map(s => (
        <span key={s.study_id} className="composer-pin-chip chat-agg-chip" title={s.study_title || `Study ${s.study_id}`}>
          {s.study_id} · {(s.selected_rows || 0).toLocaleString()} rows
        </span>
      ))}
      <span className="chat-agg-actions">
        <button className="chat-agg-btn" onClick={() => onOpen(a.aggregation_id)}>View</button>
        {rows > 0 ? (
          <>
            <a className="chat-agg-btn" href={`${base}/export.csv`}>CSV</a>
            <a className="chat-agg-btn" href={`${base}/export.xlsx`}>xlsx</a>
          </>
        ) : <span className="chat-agg-btn disabled" title="No checked rows to export">CSV · xlsx</span>}
        <button className="chat-agg-btn" disabled={busy} onClick={save}>Save as…</button>
        <button className="chat-agg-btn" disabled={busy} onClick={clear}>Clear</button>
      </span>
    </div>
  );
}
