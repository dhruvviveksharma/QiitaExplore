// Artifact output browser for the Merge panel

// Returns the Set of node_ids reachable from prep-linked artifact roots (BFS via children).
function prepReachableSet(graph) {
  const children = {};
  for (const n of graph) {
    if (n.parent_node_id) {
      (children[n.parent_node_id] = children[n.parent_node_id] || []).push(n.node_id);
    }
  }
  const reachable = new Set();
  const queue = [];
  for (const n of graph) {
    if (n.kind === 'artifact' && n.prep_template_id != null) {
      reachable.add(n.node_id);
      queue.push(n.node_id);
    }
  }
  while (queue.length) {
    const nid = queue.shift();
    for (const cnid of (children[nid] || [])) {
      if (!reachable.has(cnid)) { reachable.add(cnid); queue.push(cnid); }
    }
  }
  return reachable;
}

function filterGraphByPrep(graph, prepId) {
  if (!prepId) return graph;
  const children = {};
  for (const n of graph) {
    if (n.parent_node_id) {
      (children[n.parent_node_id] = children[n.parent_node_id] || []).push(n.node_id);
    }
  }
  const included = new Set();
  const queue = [];
  for (const n of graph) {
    if (n.kind === 'artifact' && n.prep_template_id === prepId) {
      included.add(n.node_id);
      queue.push(n.node_id);
    }
  }
  while (queue.length) {
    const nid = queue.shift();
    for (const cnid of (children[nid] || [])) {
      if (!included.has(cnid)) { included.add(cnid); queue.push(cnid); }
    }
  }
  return graph.filter(n => included.has(n.node_id));
}

function FileLink({ fp, artifactId, studyId }) {
  return (
    <a className="ao-file-btn"
      href={`${API}/artifacts/${artifactId}/files/${fp.filepath_id}/download?study_id=${studyId}`}
      target="_blank" rel="noreferrer">
      ↓ {fp.filename || fp.filepath_type}
    </a>
  );
}

// Walk parent_node_id from a BIOM node up to the root, collecting job steps in order.
// Returns [{command_name, command_params}] from root → leaf.
function buildPipeline(graph, biomNode) {
  const byId = {};
  for (const n of graph) byId[n.node_id] = n;

  const steps = [];
  let cur = biomNode;
  while (cur && cur.parent_node_id) {
    const parent = byId[cur.parent_node_id];
    if (!parent) break;
    if (parent.kind === 'job') {
      steps.unshift({ command_name: parent.command_name, command_params: parent.command_params || {} });
    }
    cur = parent;
  }
  return steps;
}

// Pick 1-2 distinguishing params to show inline on a step chip.
const KEY_PARAM_PATTERNS = /trim|length|reference|tax|threshold|database|gg|silva|similarity|phred/i;
function pickKeyParams(params) {
  return Object.entries(params || {})
    .filter(([k]) => KEY_PARAM_PATTERNS.test(k))
    .slice(0, 2)
    .map(([k, v]) => `${k}: ${v}`)
    .join(', ');
}

function PipelineBreadcrumb({ steps }) {
  if (!steps.length) return null;
  return (
    <div className="ao-breadcrumb">
      {steps.map((s, i) => {
        const key = pickKeyParams(s.command_params);
        return (
          <React.Fragment key={i}>
            {i > 0 && <span className="ao-breadcrumb-sep">→</span>}
            <span className="ao-step-chip">
              {s.command_name}
              {key && <span className="ao-step-params"> · {key}</span>}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

function VerticalWorkflowMap({ steps, filepaths, artifactId, studyId }) {
  if (!steps.length) return null;
  return (
    <div className="ao-workflow-map">
      {steps.map((s, i) => {
        const isLast = i === steps.length - 1;
        const params = Object.entries(s.command_params || {});
        return (
          <div key={i} className="ao-workflow-step">
            <div className="ao-workflow-step-left">
              <span className={`ao-workflow-dot${isLast ? ' ao-workflow-dot-last' : ''}`} />
              {!isLast && <span className="ao-workflow-line" />}
            </div>
            <div className="ao-workflow-step-body">
              <div className="ao-workflow-cmd">{s.command_name}</div>
              {params.length > 0 && (
                <div className="ao-workflow-params">
                  {params.map(([k, v]) => (
                    <span key={k} className="ao-param-pill">{k}: {String(v)}</span>
                  ))}
                </div>
              )}
              {isLast && filepaths.length > 0 && (
                <div className="ao-workflow-files">
                  {filepaths.map(fp => (
                    <FileLink key={fp.filepath_id} fp={fp} artifactId={artifactId} studyId={studyId} />
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function BiomCard({ node, graph, chosenIds, onToggleArtifact, recommendedId, sampleCounts, studyId, onPeekBiom }) {
  const [expanded, setExpanded] = useState(false);

  const isChosen  = (chosenIds || []).includes(node.artifact_id);
  const isRec     = node.artifact_id === recommendedId;
  const numSamples = sampleCounts ? sampleCounts[node.artifact_id] : null;
  const steps      = buildPipeline(graph, node);
  const filepaths  = node.filepaths || [];

  return (
    <div className={`ao-card${isChosen ? ' ao-card-chosen' : ''}`}>
      <div className="ao-card-header">
        <label className="ao-select-row">
          <input type="checkbox" checked={isChosen}
            onChange={() => onToggleArtifact(node.artifact_id)} />
          <span className="ao-card-name">
            {node.name || (node.artifact_type + ' ' + node.artifact_id)}
          </span>
          {isRec && <span className="ao-rec-badge">recommended</span>}
          {node.data_type && <span className="dtype-chip">{node.data_type}</span>}
          {numSamples != null && (
            <span className="ao-sample-count">{numSamples.toLocaleString()} samples</span>
          )}
        </label>
      </div>

      <PipelineBreadcrumb steps={steps} />

      <div className="ao-files-row">
        {filepaths.map(fp => (
          <FileLink key={fp.filepath_id} fp={fp} artifactId={node.artifact_id} studyId={studyId} />
        ))}
        {onPeekBiom && (
          <button className="ao-samples-btn" onClick={() => onPeekBiom(node.artifact_id)}>
            View samples
          </button>
        )}
        {steps.length > 0 && (
          <button className="ao-expand-btn" onClick={() => setExpanded(p => !p)}>
            {expanded ? 'Hide workflow' : 'Show workflow'}
          </button>
        )}
      </div>

      {expanded && (
        <VerticalWorkflowMap steps={steps} filepaths={filepaths}
          artifactId={node.artifact_id} studyId={studyId} />
      )}
    </div>
  );
}

function ArtifactOutputsView({ detail, loading, chosenIds, onToggleArtifact, prepFilter, recommendedId, sampleCounts, studyId }) {
  const [peekBiomId, setPeekBiomId] = useState(null);

  if (loading && !detail) return <div className="modal-detail-loading">Loading…</div>;
  if (!detail) return null;

  const graph = detail.artifact_graph;

  // Fallback to flat table if no graph data (stale cache)
  if (!graph || graph.length === 0) {
    const arts = detail.artifacts || [];
    if (!arts.length) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>No artifacts found.</p>;
    const chosen = new Set(chosenIds || []);
    return (
      <table className="prep-table">
        <thead><tr><th>Use</th><th>ID</th><th>Type</th><th>Data Type</th><th>File</th></tr></thead>
        <tbody>
          {arts.slice(0, 20).map(a => (
            <tr key={a.artifact_id}>
              <td>{a.artifact_type === 'BIOM' && (a.full_path || '').endsWith('.biom') && (
                <input type="checkbox" checked={chosen.has(a.artifact_id)}
                  onChange={() => onToggleArtifact(a.artifact_id)} />
              )}</td>
              <td>{a.artifact_id}</td><td>{a.artifact_type}</td><td>{a.data_type}</td>
              <td>{(a.full_path || '').split('/').pop()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  const reachable = prepReachableSet(graph);

  let filtered;
  if (prepFilter === 'other') {
    filtered = graph.filter(n => !reachable.has(n.node_id));
  } else if (prepFilter) {
    filtered = filterGraphByPrep(graph, prepFilter);
  } else {
    filtered = reachable.size > 0 ? graph.filter(n => reachable.has(n.node_id)) : graph;
  }

  const bioms = filtered.filter(n => n.kind === 'artifact' && n.artifact_type === 'BIOM');

  if (!bioms.length) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>No BIOM artifacts for selected prep.</p>;

  return (
    <div>
      <div className="ao-card-list">
        {bioms.map(n => (
          <BiomCard key={n.artifact_id} node={n} graph={filtered}
            chosenIds={chosenIds} onToggleArtifact={onToggleArtifact}
            recommendedId={recommendedId} sampleCounts={sampleCounts}
            studyId={studyId} onPeekBiom={setPeekBiomId} />
        ))}
      </div>
      {peekBiomId && (
        <SamplePeek artifactId={peekBiomId} studyId={studyId}
          onClose={() => setPeekBiomId(null)} />
      )}
    </div>
  );
}

function GlobalBiomSelector({ workspace, studyDetails, onApply }) {
  const [dtFilter,    setDtFilter]    = useState('');
  const [nameFilter,  setNameFilter]  = useState('');
  const [applying,    setApplying]    = useState(false);
  const [msg,         setMsg]         = useState('');

  const studies = workspace?.studies || [];
  if (studies.length < 2) return null;

  const allTypes = [...new Set(
    studies.flatMap(s => (s.data_types || '').split(',').map(t => t.trim()).filter(Boolean))
  )].sort();

  async function handleApply() {
    setApplying(true); setMsg('');
    const localDetails = new Map(studyDetails);
    const missing = studies.filter(s => !localDetails.has(s.study_id));
    if (missing.length) {
      const results = await Promise.all(
        missing.map(s => apiFetch(`/studies/${s.study_id}/detail`).then(r => r.ok ? r.json() : null))
      );
      results.filter(Boolean).forEach(d => localDetails.set(d.study_id, d));
    }
    const selections = [];
    let missed = 0;
    studies.forEach(s => {
      const d = localDetails.get(s.study_id);
      if (!d) { missed++; return; }
      const bioms = (d.artifact_graph || []).filter(n =>
        n.kind === 'artifact' && n.artifact_type === 'BIOM' &&
        (!dtFilter   || (n.data_type || '').includes(dtFilter)) &&
        (!nameFilter || (n.name || n.full_path || '').toLowerCase().includes(nameFilter.toLowerCase()))
      );
      if (bioms.length === 1) selections.push({ studyId: s.study_id, artifactId: bioms[0].artifact_id });
      else missed++;
    });
    if (selections.length) onApply(selections);
    setMsg(missed
      ? `${selections.length} selected; ${missed} study(s) had no unique match.`
      : `Selected ${selections.length} BIOM(s).`
    );
    setApplying(false);
  }

  return (
    <div className="global-biom-selector">
      <select value={dtFilter} onChange={e => setDtFilter(e.target.value)} className="merge-dt-select">
        <option value="">All data types</option>
        {allTypes.map(t => <option key={t} value={t}>{t}</option>)}
      </select>
      <input value={nameFilter} onChange={e => setNameFilter(e.target.value)}
        placeholder="filename contains…" className="merge-name-filter" />
      <button onClick={handleApply} disabled={applying} className="merge-btn-primary"
        style={{ padding: '4px 10px', fontSize: '0.8rem' }}>
        {applying ? 'Searching…' : 'Smart Select'}
      </button>
      {msg && <span style={{ fontSize: '0.8rem', color: 'var(--text-3)', marginLeft: 4 }}>{msg}</span>}
    </div>
  );
}
