// Study card "Group by prep": one collapsible header per prep template of the study
// (GET /studies/<id>/sample-preps), whose samples load when the group is first opened
// (GET /studies/<id>/preps/<pid>/samples, first 500). A sample in several preps shows
// under each. Rows are a bare (toolbar-less) stacked SamplesBrowser, so clicking a
// sample still opens its metadata through the caller's fetchFields.
// Globals in scope: React, useState, useEffect (utils.js), apiJson (utils.js), SamplesBrowser (components.js)

function PrepGroupedSamples({ studyId, fetchFields }) {
  const [groups, setGroups] = useState(null);
  const [open,   setOpen]   = useState({});   // key -> bool; key is prep_id, or 'none' for samples in no prep
  const [loaded, setLoaded] = useState({});   // key -> response, or 'loading'
  const [err,    setErr]    = useState('');

  useEffect(() => {
    let live = true;
    apiJson(`/studies/${studyId}/sample-preps`)
      .then(d => { if (live) setGroups(d.groups || []); })
      .catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [studyId]);

  const toggle = async (g) => {
    const k = g.prep_id ?? 'none';
    setOpen(o => ({ ...o, [k]: !o[k] }));
    if (loaded[k]) return;
    setLoaded(l => ({ ...l, [k]: 'loading' }));
    try {
      const d = await apiJson(`/studies/${studyId}/preps/${k}/samples`);
      setLoaded(l => ({ ...l, [k]: d }));
    } catch (e) {
      setLoaded(l => { const n = { ...l }; delete n[k]; return n; });
      setErr(e.message);
    }
  };

  if (err && !groups) return <div className="browse-error">{err}</div>;
  if (!groups) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>Loading preps…</p>;
  if (groups.length === 0) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>No samples found.</p>;
  return (
    <div>
      {err && <div className="browse-error">{err}</div>}
      {groups.map(g => {
        const k = g.prep_id ?? 'none';
        const d = loaded[k];
        return (
          <div key={k}>
            <button className="prep-group-head" onClick={() => toggle(g)}>
              {open[k] ? '▾' : '▸'}
              <strong>{g.prep_id == null ? 'No prep' : `Prep ${g.prep_id}`}</strong>
              {g.data_type && <span className="dtype-chip">{g.data_type}</span>}
              <span style={{ color: 'var(--text-3)' }}>{g.num_samples.toLocaleString()} samples</span>
            </button>
            {open[k] && (
              <div className="prep-group-body">
                {d === 'loading' || !d
                  ? <span className="agg-samples-msg">Loading…</span>
                  : <>
                      {d.total > d.samples.length && (
                        <div className="agg-samples-msg">Showing first {d.samples.length.toLocaleString()} of {d.total.toLocaleString()}</div>
                      )}
                      <SamplesBrowser samples={d.samples} layout="stacked" bare fetchFields={fetchFields} />
                    </>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
