// Provenance org-chart renderer — extracted from merge_artifacts.js (TKT-011)
// Globals in scope: React, useState (utils.js), FileLink, FlagList (merge_artifacts.js),
//                   SamplePeek (merge_detail.js)

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
  const [peekOpen, setPeekOpen]   = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
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
    const isChosen   = (chosenIds || []).includes(node.artifact_id);
    const isRec      = node.artifact_id === recommendedId;
    const numSamples = sampleCounts ? sampleCounts[node.artifact_id] : null;
    const filepaths  = node.filepaths || [];
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
          {filepaths.length > 0 && (
            <button type="button" className="ao-tree-files-toggle"
              onClick={e => { e.stopPropagation(); setFilesOpen(p => !p); }}>
              {filesOpen ? '▾ files' : '▸ files'}
            </button>
          )}
        </label>
        {filesOpen && (
          <div className="ao-tree-leaf-detail">
            {filepaths.map(fp => (
              <FileLink key={fp.filepath_id} fp={fp} artifactId={node.artifact_id} studyId={studyId} />
            ))}
            <button className="ao-samples-btn"
              onClick={() => setPeekOpen(p => !p)}>
              {peekOpen ? 'Hide samples' : 'View samples'}
            </button>
          </div>
        )}
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

  // Intermediate artifact: pass-through
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
