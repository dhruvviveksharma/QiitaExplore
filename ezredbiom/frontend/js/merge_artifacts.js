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

// Step parameters ("flags"), revealed on demand. Shared by the breadcrumb and map.
function FlagList({ params }) {
  const entries = Object.entries(params || {});
  if (!entries.length) return null;
  return (
    <div className="ao-flags-pop">
      {entries.map(([k, v]) => (
        <div key={k} className="ao-flag-row">
          <span className="ao-flag-k">{k}</span> {String(v)}
        </div>
      ))}
    </div>
  );
}

// Clean command-name chips; click a step to reveal its flags below the row.
function PipelineBreadcrumb({ steps }) {
  const [openIdx, setOpenIdx] = useState(null);
  if (!steps.length) return null;
  const open = openIdx != null ? steps[openIdx] : null;
  return (
    <div className="ao-breadcrumb-wrap">
      <div className="ao-breadcrumb">
        {steps.map((s, i) => {
          const hasFlags = Object.keys(s.command_params || {}).length > 0;
          return (
            <React.Fragment key={i}>
              {i > 0 && <span className="ao-breadcrumb-sep">→</span>}
              <button type="button" disabled={!hasFlags}
                className={`ao-step-chip${openIdx === i ? ' ao-step-chip-open' : ''}`}
                onClick={() => setOpenIdx(openIdx === i ? null : i)}>
                {s.command_name}
                {hasFlags && <span className="ao-step-caret">{openIdx === i ? '▾' : '▸'}</span>}
              </button>
            </React.Fragment>
          );
        })}
      </div>
      {open && <FlagList params={open.command_params} />}
    </div>
  );
}

function WorkflowStep({ step, isLast, filepaths, artifactId, studyId }) {
  const [open, setOpen] = useState(false);
  const hasFlags = Object.keys(step.command_params || {}).length > 0;
  return (
    <div className="ao-workflow-step">
      <div className="ao-workflow-step-left">
        <span className={`ao-workflow-dot${isLast ? ' ao-workflow-dot-last' : ''}`} />
        {!isLast && <span className="ao-workflow-line" />}
      </div>
      <div className="ao-workflow-step-body">
        <button type="button" disabled={!hasFlags} className="ao-workflow-cmd"
          onClick={() => setOpen(p => !p)}>
          {step.command_name}
          {hasFlags && <span className="ao-step-caret">{open ? '▾' : '▸'}</span>}
        </button>
        {open && <FlagList params={step.command_params} />}
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
}

function VerticalWorkflowMap({ steps, filepaths, artifactId, studyId }) {
  if (!steps.length) return null;
  return (
    <div className="ao-workflow-map">
      {steps.map((s, i) => (
        <WorkflowStep key={i} step={s} isLast={i === steps.length - 1}
          filepaths={filepaths} artifactId={artifactId} studyId={studyId} />
      ))}
    </div>
  );
}

function BiomCard({ node, graph, chosenIds, onToggleArtifact, recommendedId, sampleCounts, studyId }) {
  const [expanded, setExpanded] = useState(false);
  const [peekOpen, setPeekOpen] = useState(false);

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
        <button className="ao-samples-btn" onClick={() => setPeekOpen(p => !p)}>
          {peekOpen ? 'Hide samples' : 'View samples'}
        </button>
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
      {peekOpen && (
        <SamplePeek artifactId={node.artifact_id} studyId={studyId}
          onClose={() => setPeekOpen(false)} />
      )}
    </div>
  );
}

// Build the provenance subgraph for BIOMs that have job ancestry.
// Returns { roots, byId, childrenMap } for the CSS org-chart renderer.
function provenanceForest(filtered, rootedBioms) {
  const byId = {};
  for (const n of filtered) byId[n.node_id] = n;

  const relevant = new Set();
  for (const biom of rootedBioms) {
    let cur = biom;
    while (cur) {
      relevant.add(cur.node_id);
      cur = cur.parent_node_id ? byId[cur.parent_node_id] : null;
    }
  }

  const childrenMap = {};
  for (const nid of relevant) {
    const n = byId[nid];
    if (n.parent_node_id && relevant.has(n.parent_node_id)) {
      (childrenMap[n.parent_node_id] = childrenMap[n.parent_node_id] || []).push(n.node_id);
    }
  }

  const roots = [...relevant]
    .map(nid => byId[nid])
    .filter(n => !n.parent_node_id || !relevant.has(n.parent_node_id));

  return { roots, byId, childrenMap };
}

function TreeChildren({ nodes, byId, childrenMap, selProps }) {
  if (!nodes.length) return null;
  const nameCount = {};
  for (const n of nodes) {
    if (n.kind === 'job') nameCount[n.command_name] = (nameCount[n.command_name] || 0) + 1;
  }
  return (
    <div className="ao-tree-children">
      {nodes.map(n => {
        let label = null;
        if (n.kind === 'job' && nameCount[n.command_name] > 1) {
          const numVal = Object.values(n.command_params || {}).find(
            v => typeof v === 'number' || (typeof v === 'string' && /^\d+$/.test(v))
          );
          if (numVal != null) label = `${n.command_name} · ${numVal}`;
        }
        return (
          <TreeNode key={n.node_id} node={n} byId={byId} childrenMap={childrenMap}
            selProps={selProps} label={label} />
        );
      })}
    </div>
  );
}

function TreeNode({ node, byId, childrenMap, selProps, label, isRoot }) {
  const [flagsOpen, setFlagsOpen] = useState(false);
  const [peekOpen, setPeekOpen] = useState(false);
  const children = (childrenMap[node.node_id] || []).map(id => byId[id]);

  if (node.kind === 'job') {
    const hasFlags = Object.keys(node.command_params || {}).length > 0;
    return (
      <div className="ao-tree-node">
        <button type="button" disabled={!hasFlags}
          className={`ao-tree-box${flagsOpen ? ' ao-tree-box-open' : ''}`}
          onClick={() => hasFlags && setFlagsOpen(p => !p)}>
          {label || node.command_name}
          {hasFlags && <span className="ao-step-caret">{flagsOpen ? '▾' : '▸'}</span>}
        </button>
        {flagsOpen && <FlagList params={node.command_params} />}
        <TreeChildren nodes={children} byId={byId} childrenMap={childrenMap} selProps={selProps} />
      </div>
    );
  }

  if (node.artifact_type === 'BIOM') {
    const { chosenIds, onToggleArtifact, recommendedId, sampleCounts, studyId } = selProps;
    const isChosen = (chosenIds || []).includes(node.artifact_id);
    const isRec = node.artifact_id === recommendedId;
    const numSamples = sampleCounts ? sampleCounts[node.artifact_id] : null;
    const filepaths = node.filepaths || [];
    return (
      <div className="ao-tree-node">
        <label className={`ao-tree-box ao-tree-box-leaf${isChosen ? ' ao-tree-box-chosen' : ''}`}>
          <input type="checkbox" checked={isChosen}
            onChange={() => onToggleArtifact(node.artifact_id)} />
          <span className="ao-tree-leaf-name">
            {node.name || (node.artifact_type + ' ' + node.artifact_id)}
          </span>
          {isRec && <span className="ao-rec-badge">recommended</span>}
          {numSamples != null && (
            <span className="ao-sample-count">{numSamples.toLocaleString()} samples</span>
          )}
        </label>
        {filepaths.length > 0 && (
          <div className="ao-workflow-files" style={{ marginTop: '4px' }}>
            {filepaths.map(fp => (
              <FileLink key={fp.filepath_id} fp={fp} artifactId={node.artifact_id} studyId={studyId} />
            ))}
          </div>
        )}
        <button className="ao-samples-btn" style={{ fontSize: '0.72rem', marginTop: '4px' }}
          onClick={() => setPeekOpen(p => !p)}>
          {peekOpen ? 'Hide samples' : 'View samples'}
        </button>
        {peekOpen && (
          <SamplePeek artifactId={node.artifact_id} studyId={studyId}
            onClose={() => setPeekOpen(false)} />
        )}
      </div>
    );
  }

  if (isRoot) {
    return (
      <div className="ao-tree-node">
        <div className="ao-tree-box ao-tree-box-root">
          {node.name || node.artifact_type}
        </div>
        <TreeChildren nodes={children} byId={byId} childrenMap={childrenMap} selProps={selProps} />
      </div>
    );
  }

  // Intermediate artifact: pass-through, render children directly
  return (
    <TreeChildren nodes={children} byId={byId} childrenMap={childrenMap} selProps={selProps} />
  );
}

function ProvenanceForest({ roots, byId, childrenMap, selProps }) {
  return (
    <div className="ao-tree">
      {roots.map(r => (
        <TreeNode key={r.node_id} node={r} byId={byId} childrenMap={childrenMap}
          selProps={selProps} label={null} isRoot />
      ))}
    </div>
  );
}

function ArtifactOutputsView({ detail, loading, chosenIds, onToggleArtifact, prepFilter, recommendedId, sampleCounts, studyId }) {
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

  const parentless = bioms.filter(n => buildPipeline(filtered, n).length === 0);
  const rooted     = bioms.filter(n => buildPipeline(filtered, n).length > 0);
  const selProps   = { chosenIds, onToggleArtifact, recommendedId, sampleCounts, studyId };
  const forest     = rooted.length > 0 ? provenanceForest(filtered, rooted) : null;

  return (
    <div>
      {forest && (
        <ProvenanceForest roots={forest.roots} byId={forest.byId}
          childrenMap={forest.childrenMap} selProps={selProps} />
      )}
      {parentless.length > 0 && (
        <div className={`ao-card-list${forest ? ' ao-card-list-below' : ''}`}>
          {parentless.map(n => (
            <BiomCard key={n.artifact_id} node={n} graph={filtered}
              chosenIds={chosenIds} onToggleArtifact={onToggleArtifact}
              recommendedId={recommendedId} sampleCounts={sampleCounts}
              studyId={studyId} />
          ))}
        </div>
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
