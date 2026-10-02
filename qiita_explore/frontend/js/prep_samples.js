// Study card "Group by prep": one collapsible header per prep template of the study
// (GET /studies/<id>/sample-preps), whose samples load when the group is first opened
// (GET /studies/<id>/preps/<pid>/samples, first 500). A sample in several preps shows
// under each. Rows are bare (toolbar-less) SamplesBrowsers; the clicked sample's
// metadata (the caller's fetchFields) stays in a pane to the right while the
// groups scroll — one selection across all groups, the last click wins.
// Globals in scope: React, useState, useEffect, useRef (utils.js), apiJson (utils.js),
//   SamplesBrowser, SampleFieldsCard (components.js)

function PrepGroupedSamples({ studyId, fetchFields }) {
  const [groups, setGroups] = useState(null);
  const [open,   setOpen]   = useState({});   // key -> bool; key is prep_id, or 'none' for samples in no prep
  const [loaded, setLoaded] = useState({});   // key -> response, or 'loading'
  const [err,    setErr]    = useState('');
  const [pick,   setPick]   = useState(null); // {id, fields, loading}: the sample shown on the right
  const pickReq = useRef(null);               // id of the latest click; an older reply is dropped

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

  const onPick = async (s) => {
    if (pick?.id === s.sample_id) { pickReq.current = null; setPick(null); return; }
    pickReq.current = s.sample_id;
    setPick({ id: s.sample_id, fields: null, loading: true });
    let fields = null;
    try { fields = await fetchFields(s.sample_id); } catch (_) {}
    if (pickReq.current === s.sample_id) setPick({ id: s.sample_id, fields, loading: false });
  };

  if (err && !groups) return <div className="browse-error">{err}</div>;
  if (!groups) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>Loading preps…</p>;
  if (groups.length === 0) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>No samples found.</p>;
  return (
    <div className="psg-split">
      <div className="psg-groups">
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
                        <SamplesBrowser samples={d.samples} layout="stacked" bare activeId={pick?.id} onPick={onPick} />
                      </>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="psg-side">
        {pick && (pick.loading || pick.fields)
          ? <SampleFieldsCard sampleId={pick.id} fields={pick.fields} loading={pick.loading}
              onClose={() => { pickReq.current = null; setPick(null); }} />
          : <p className="psg-hint">{pick ? `No metadata found for ${pick.id}.` : 'Click a sample to see its metadata.'}</p>}
      </div>
    </div>
  );
}
