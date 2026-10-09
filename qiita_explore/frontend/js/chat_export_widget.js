// The chat's CSV / TSV card (backend helpers/export_tools.py: export_table).
// samples / files / rows exports download from GET /api/chat-exports/<id>.<ext>
// (rebuilt from Qiita at download for samples and files); an aggregation export
// links the aggregation's own export route. The payload carries a five-row
// preview, so a reloaded chat shows the card without a request.
// Globals in scope: API (utils.js), WidgetHead (chat_study_widgets.js)

const _EXPORT_SOURCE_LABEL = {
  samples: 'sample metadata', files: 'file paths', rows: 'table from this chat',
};

function TableExportCard({ p, ctx }) {
  const n = x => (x ?? 0).toLocaleString();
  if (p.source === 'aggregation') {
    const base = `${API}/aggregations/${p.aggregation_id}`;
    return (
      <>
        <WidgetHead title={`Export of ${p.name}`}
          sub={`${n(p.n_studies)} stud${p.n_studies === 1 ? 'y' : 'ies'} · one row per checked sample × file`} ctx={ctx} />
        <div className="cw-agg-row">
          {['csv', 'tsv', 'xlsx'].map(ext => (
            <a key={ext} className="ao-file-btn" href={`${base}/export.${ext}`}>↓ {ext.toUpperCase()}</a>
          ))}
        </div>
      </>
    );
  }
  const href = ext => `${API}/chat-exports/${p.export_id}.${ext}`;
  const formats = p.format === 'tsv' ? ['tsv', 'csv'] : ['csv', 'tsv'];
  const more = (p.n_columns || 0) > (p.columns || []).length;
  return (
    <>
      <WidgetHead title={`${p.name}.${p.format}`}
        sub={`${_EXPORT_SOURCE_LABEL[p.source] || p.source} · ${n(p.n_rows)} rows × ${n(p.n_columns)} columns`}
        studyId={p.study_id || undefined} ctx={ctx} />
      {(p.preview || []).length > 0 && (
        <div className="cw-export-preview">
          <table className="cw-export-table">
            <thead><tr>{(p.columns || []).map((c, i) => <th key={i}>{c}</th>)}{more && <th>…</th>}</tr></thead>
            <tbody>
              {p.preview.map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j} title={v}>{v}</td>)}{more && <td>…</td>}</tr>
              ))}
            </tbody>
          </table>
          {p.n_rows > p.preview.length && <div className="cw-note">First {p.preview.length} of {n(p.n_rows)} rows.</div>}
        </div>
      )}
      <div className="cw-agg-row">
        {formats.map(ext => (
          <a key={ext} className="ao-file-btn" href={href(ext)}>↓ {ext.toUpperCase()}</a>
        ))}
      </div>
    </>
  );
}
