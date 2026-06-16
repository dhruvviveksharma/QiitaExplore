// Merge detail UI: per-study summary card, merge preview, sample peek, job status/history
// useState, useEffect, etc. are available globally from utils.js

// ── Study summary card ────────────────────────────────────────────────────────

function StudySummaryCard({ autoArtifact, chosenIds, showGraph, onChangeSelection, studyId }) {
  const [showPeek, setShowPeek] = useState(false);

  if (!autoArtifact) {
    return <div className="study-summary-card study-summary-pending">Validating selection…</div>;
  }

  const fname = (autoArtifact.full_path || '').split('/').pop();
  const displayName = autoArtifact.prep_name || autoArtifact.name || fname
    || `Artifact ${autoArtifact.artifact_id}`;
  const isOverridden = chosenIds?.length > 0 &&
    !chosenIds.includes(autoArtifact.artifact_id);

  return (
    <div className="study-summary-card">
      <div className="study-summary-main">
        <span className="study-summary-check">{isOverridden ? '○' : '✓'}</span>
        <span className="study-summary-name" title={fname}>{displayName}</span>
        {autoArtifact.data_type && (
          <span className="dtype-chip">{autoArtifact.data_type}</span>
        )}
        {autoArtifact.num_samples != null && (
          <span className="study-summary-samples">
            {autoArtifact.num_samples.toLocaleString()} samples
          </span>
        )}
      </div>
      <div className="study-summary-footer">
        {autoArtifact.reason && (
          <span className="study-summary-reason">{autoArtifact.reason}</span>
        )}
        <div className="study-summary-actions">
          {autoArtifact.artifact_id && (
            <button className="merge-btn-ghost study-summary-peek"
              onClick={() => setShowPeek(p => !p)}>
              {showPeek ? 'Hide samples' : 'Peek samples'}
            </button>
          )}
          <button className="merge-btn-ghost study-summary-change"
            onClick={onChangeSelection}>
            {showGraph ? '▲ Hide selection' : '▼ Change selection'}
          </button>
        </div>
      </div>
      {showPeek && autoArtifact.artifact_id && (
        <SamplePeek artifactId={autoArtifact.artifact_id} studyId={studyId}
          onClose={() => setShowPeek(false)} />
      )}
    </div>
  );
}

// ── Sample peek ───────────────────────────────────────────────────────────────

function SamplePeek({ artifactId, studyId, onClose }) {
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiFetch(`/artifacts/${artifactId}/samples?study_id=${studyId}&limit=50`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setRows(d); setLoading(false); })
      .catch(() => { setErr('Could not load samples.'); setLoading(false); });
  }, [artifactId]);

  const cols = rows?.length ? Object.keys(rows[0].fields || {}) : [];

  return (
    <div className="sample-peek">
      <div className="sample-peek-header">
        <span>First {rows?.length ?? '…'} sample IDs</span>
        <button className="merge-btn-ghost" onClick={onClose}>✕</button>
      </div>
      {loading && <div className="sample-peek-msg">Loading…</div>}
      {err   && <div className="sample-peek-msg sample-peek-err">{err}</div>}
      {rows  && (
        <table className="prep-table sample-peek-table">
          <thead>
            <tr>
              <th>Sample ID</th>
              {cols.map(c => <th key={c}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.sample_id}>
                <td>{r.sample_id}</td>
                {cols.map(c => <td key={c}>{r.fields?.[c] ?? ''}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Sample browser (collapsible, inside Compatible banner) ────────────────────

const SAMPLE_COLS = ['sex', 'age', 'age_unit', 'obesitycat', 'country'];

function MergeSampleBrowser({ workspaceId, total }) {
  const [open, setOpen]       = useState(false);
  const [rows, setRows]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr]         = useState('');
  const [filter, setFilter]   = useState('');

  function toggle() {
    setOpen(o => !o);
    if (!rows && !loading) {
      setLoading(true);
      apiFetch(`/merge-workspaces/${workspaceId}/samples`)
        .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
        .then(d => { setRows(d.rows); setLoading(false); })
        .catch(e => { setErr(String(e)); setLoading(false); });
    }
  }

  const visible = rows
    ? (filter.trim()
        ? rows.filter(r => {
            const q = filter.toLowerCase();
            return r.sample_id.toLowerCase().includes(q)
              || String(r.study_id).includes(q)
              || SAMPLE_COLS.some(c => String(r.fields[c] ?? '').toLowerCase().includes(q));
          })
        : rows)
    : [];

  return (
    <div className="merge-sample-browser">
      <button className="merge-sample-toggle" onClick={toggle}>
        {open ? '▲ Hide samples' : `▼ Browse ${total.toLocaleString()} samples`}
      </button>
      {open && (
        <>
          <input
            className="merge-sample-filter"
            placeholder="Filter by sample ID or metadata…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
          {loading && <div className="sample-peek-msg">Loading…</div>}
          {err && <div className="sample-peek-msg sample-peek-err">Error: {err}</div>}
          {!loading && rows && (
            <div className="merge-sample-table-wrap">
              <table className="prep-table sample-peek-table">
                <thead>
                  <tr>
                    <th>Sample ID</th>
                    <th>Study</th>
                    {SAMPLE_COLS.map(c => <th key={c}>{c}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {visible.map(r => (
                    <tr key={r.sample_id}>
                      <td>{r.sample_id}</td>
                      <td><span className="data-type-badge">#{r.study_id}</span></td>
                      {SAMPLE_COLS.map(c => <td key={c}>{r.fields[c] ?? ''}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
              {filter && <div className="sample-peek-msg">{visible.length} / {rows.length} shown</div>}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Merge preview + validation panel ──────────────────────────────────────────

function MergePreviewPanel({ validation, validating, workspaceId }) {
  if (validating) return <div className="merge-validation validating">Validating…</div>;
  if (!validation) return null;

  const { compatible, warnings = [], errors = [], preview } = validation;
  return (
    <div className={`merge-validation ${compatible ? 'ok' : 'fail'}`}>
      <div className="merge-val-status">
        {compatible ? '✓ Compatible' : '✗ Incompatible'}
      </div>
      {errors.map((e, i) => <div key={i} className="merge-val-error">⊘ {e}</div>)}
      {warnings.map((w, i) => <div key={i} className="merge-val-warn">⚠ {w}</div>)}
      {compatible && preview && (
        <div className="merge-preview">
          <span className="merge-preview-total">
            Output: <strong>{preview.total_unique.toLocaleString()}</strong> unique samples
          </span>
          {preview.overlap_count > 0 && (
            <span className="merge-preview-overlap">
              &nbsp;({preview.overlap_count.toLocaleString()} shared IDs collapsed)
            </span>
          )}
          {preview.per_study?.length > 0 && (
            <div className="merge-preview-studies">
              {preview.per_study.map(s => (
                <span key={s.study_id} className="merge-preview-chip">
                  #{s.study_id}: {s.num_samples.toLocaleString()}
                </span>
              ))}
            </div>
          )}
          {workspaceId && (
            <MergeSampleBrowser workspaceId={workspaceId} total={preview.total_unique} />
          )}
        </div>
      )}
    </div>
  );
}

// ── Job status and history (moved from merge_workspace.js) ────────────────────

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
      {job.status === 'pending' && <><div className="spinner" style={{display:'inline-block',marginRight:6}}/> Waiting…</>}
      {job.status === 'running' && <><div className="spinner" style={{display:'inline-block',marginRight:6}}/> Merging…</>}
      {job.status === 'done' && (
        <div>
          <span>✓ Done — </span>
          <a href={`${API}/merge-jobs/${jobId}/download`} className="merge-download-link">
            Download bundle
          </a>
          <button className="merge-btn-ghost" style={{marginLeft:8}} onClick={onReset}>↺ New job</button>
        </div>
      )}
      {job.status === 'failed' && (
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

// ── Merge cart — bottom tray of all selected BIOMs + Merge button ─────────────

function MergeCart({ workspace, validation, merging, onMerge }) {
  const studies = workspace?.studies || [];
  if (!studies.length) return null;

  const canMerge = !merging && validation?.compatible && studies.length >= 2;

  const items = studies.map(slot => {
    const vs = validation?.studies?.find(s => s.study_id === slot.study_id);
    const art = vs?.auto_artifact;
    return { study_id: slot.study_id, study_title: slot.study_title, art };
  });

  return (
    <div className="merge-cart">
      <div className="merge-cart-title">Merge Queue</div>
      <div className="merge-cart-items">
        {items.map(({ study_id, study_title, art }) => (
          <div key={study_id} className="merge-cart-item">
            <span className="merge-cart-study">#{study_id}</span>
            <span className="merge-cart-name">
              {art ? (art.prep_name || art.name || `Artifact ${art.artifact_id}`) : (study_title || '…')}
            </span>
            {art?.data_type && <span className="dtype-chip">{art.data_type}</span>}
            {art?.num_samples != null && (
              <span className="merge-cart-count">{art.num_samples.toLocaleString()} samples</span>
            )}
          </div>
        ))}
      </div>
      {validation?.preview && (
        <div className="merge-cart-preview">
          <strong>{validation.preview.total_unique.toLocaleString()}</strong> unique samples
          {validation.preview.overlap_count > 0 && (
            <span className="merge-cart-overlap">&nbsp;({validation.preview.overlap_count.toLocaleString()} shared)</span>
          )}
        </div>
      )}
      <button className="merge-btn-primary merge-cart-btn"
        disabled={!canMerge} onClick={onMerge}>
        {merging ? 'Submitting…' : 'Merge'}
      </button>
      {studies.length < 2 && (
        <span className="merge-btn-hint">Add at least 2 studies to merge</span>
      )}
      {studies.length >= 2 && !validation?.compatible && validation && (
        <span className="merge-btn-hint">Fix errors above to enable</span>
      )}
    </div>
  );
}
