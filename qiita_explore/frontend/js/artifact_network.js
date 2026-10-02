// Study modal "Outputs": one prep's processing network drawn left to right, like
// Qiita's own chart — artifacts as triangles labelled "name (type)", processing
// jobs as circles labelled with the command, edges parent → child. Only the
// chart shows until a node is clicked; then that node's details and files (or a
// job's parameters) appear below it. This is also where intermediate artifacts
// such as Demultiplexed become visible and downloadable.
//
// Archived artifacts are hidden, as Qiita hides them: Qiita archives old outputs
// and drops their parent links, so they would float unconnected. A note under the
// chart counts them and can list them.
//
// The graph (helpers/artifact_graph.py) is a forest — one parent per node — so a
// tidy-tree layout is enough and no graph library is needed: x is the depth,
// leaves take successive rows, a parent sits midway between its first and last
// child.
// Globals in scope: React, useState, useEffect (utils.js), apiPost (utils.js),
//   FileLink, FlagList (merge_artifacts.js), jobLabels, jobVariants (merge_tree.js),
//   SamplePeek (merge_detail.js)

const _AN_COL = 230;      // px between depth levels
const _AN_ROW = 76;       // px between leaf rows
const _AN_PAD_X = 110;    // room for the first / last column's centred labels
const _AN_PAD_TOP = 46;   // room for the top row's two label lines
const _AN_R = 13;         // node radius
const _AN_ZOOMS = [0.5, 0.65, 0.8, 1, 1.2, 1.45];
const _AN_LABEL_MAX = 30;

const _anArchived = (n) => n.kind === 'artifact' && n.visibility === 'archived';
const _anClip = (s) => (s && s.length > _AN_LABEL_MAX ? s.slice(0, _AN_LABEL_MAX - 1) + '…' : s || '');

// {pos: {node_id: {x: depth, y: row}}, cols, rows, labels, variants} for the forest.
function _anLayout(nodes) {
  const byId = {};
  for (const n of nodes) byId[n.node_id] = n;
  const kids = {}, roots = [];
  for (const n of nodes) {
    if (n.parent_node_id && byId[n.parent_node_id]) (kids[n.parent_node_id] = kids[n.parent_node_id] || []).push(n);
    else roots.push(n);
  }
  const labels = {}, variants = {};
  // Jobs by their label, then artifacts by id: stable, and branches like
  // Trimming 90 / 100 / 150 come out in a predictable order.
  const ordered = (list) => {
    Object.assign(labels, jobLabels(list));
    Object.assign(variants, jobVariants(list));
    const key = (n) => n.kind === 'job' ? `0 ${labels[n.node_id] || n.command_name || ''}`
      : `1 ${String(n.artifact_id || '').padStart(12, '0')}`;
    return [...list].sort((a, b) => key(a).localeCompare(key(b), undefined, { numeric: true }));
  };
  const pos = {};
  let row = 0, cols = 0;
  const place = (n, depth) => {
    cols = Math.max(cols, depth + 1);
    const cs = ordered(kids[n.node_id] || []);
    if (!cs.length) {
      pos[n.node_id] = { x: depth, y: row++ };
      return;
    }
    cs.forEach(c => place(c, depth + 1));
    pos[n.node_id] = { x: depth, y: (pos[cs[0].node_id].y + pos[cs[cs.length - 1].node_id].y) / 2 };
  };
  ordered(roots).forEach(r => place(r, 0));
  return { pos, cols, rows: row, labels, variants };
}

// Label lines drawn above a node: an artifact's name and "(type)"; a job's
// command, plus the parameter value that tells it apart from same-command siblings.
function _anNodeLines(n, variant) {
  if (n.kind === 'job') return variant != null ? [n.command_name || 'job', variant] : [n.command_name || 'job'];
  return [n.name || n.artifact_type || `artifact ${n.artifact_id}`, `(${n.artifact_type || '?'})`];
}

function ArtifactNetwork({ graph, studyId }) {
  const [selected, setSelected] = useState(null);
  const [zoom, setZoom] = useState(3);              // index into _AN_ZOOMS (1×)
  const [showArchived, setShowArchived] = useState(false);
  const [counts, setCounts] = useState({});         // {artifact_id: samples}, fetched on click

  const archived = graph.filter(_anArchived);
  const live = graph.filter(n => !_anArchived(n));
  const { pos, cols, rows, labels, variants } = _anLayout(live);
  const byId = {};
  for (const n of graph) byId[n.node_id] = n;
  const sel = selected ? byId[selected] : null;

  // A BIOM's sample count needs the file parsed (cached server-side), so it is
  // asked for when that BIOM is clicked, not for every BIOM of the study up front.
  useEffect(() => {
    if (!sel || sel.artifact_type !== 'BIOM' || sel.artifact_id in counts) return;
    apiPost('/artifacts/sample-counts', { study_id: studyId, artifact_ids: [sel.artifact_id] })
      .then(r => (r.ok ? r.json() : {}))
      .then(c => setCounts(prev => ({ ...prev, [sel.artifact_id]: c[sel.artifact_id] ?? null })))
      .catch(() => {});
  }, [selected]);

  const W = _AN_PAD_X * 2 + Math.max(0, cols - 1) * _AN_COL;
  const H = _AN_PAD_TOP + 12 + Math.max(0, rows - 1) * _AN_ROW + _AN_R + 22;
  const at = (id) => ({ cx: _AN_PAD_X + pos[id].x * _AN_COL, cy: _AN_PAD_TOP + 12 + pos[id].y * _AN_ROW });
  const z = _AN_ZOOMS[zoom];

  return (
    <div className="an-root">
      {live.length > 0 ? (
        <div className="an-chart">
          <div className="an-zoom">
            <button type="button" title="Zoom out" disabled={zoom === 0} onClick={() => setZoom(i => i - 1)}>−</button>
            <button type="button" title="Zoom in" disabled={zoom === _AN_ZOOMS.length - 1} onClick={() => setZoom(i => i + 1)}>+</button>
            <button type="button" title="Actual size" onClick={() => setZoom(3)}>1:1</button>
          </div>
          <div className="an-scroll">
            <svg className="an-svg" width={W * z} height={H * z} viewBox={`0 0 ${W} ${H}`}>
              <defs>
                <marker id="an-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                  <path d="M 0 0 L 10 5 L 0 10 z" className="an-arrowhead" />
                </marker>
              </defs>
              {/* Curved edges leave and enter nodes horizontally, so a fan-out
                  doesn't cut through the labels drawn above the nodes. */}
              {live.filter(n => n.parent_node_id && pos[n.parent_node_id]).map(n => {
                const a = at(n.parent_node_id), b = at(n.node_id);
                const x1 = a.cx + _AN_R + 2, x2 = b.cx - _AN_R - 4, mx = (x1 + x2) / 2;
                return <path key={`e-${n.node_id}`} className="an-edge" markerEnd="url(#an-arrow)"
                  d={`M ${x1} ${a.cy} C ${mx} ${a.cy}, ${mx} ${b.cy}, ${x2} ${b.cy}`} />;
              })}
              {live.map(n => {
                const { cx, cy } = at(n.node_id);
                const lines = _anNodeLines(n, variants[n.node_id]);
                const on = selected === n.node_id;
                return (
                  <g key={n.node_id} className={`an-node an-${n.kind}${on ? ' an-on' : ''}`}
                    role="button" tabIndex={0} onClick={() => setSelected(n.node_id)}
                    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(n.node_id); } }}>
                    <title>{lines.join(' ')}</title>
                    {lines.map((t, i) => (
                      <text key={i} className="an-label" x={cx} y={cy - _AN_R - 8 - (lines.length - 1 - i) * 14}>{_anClip(t)}</text>
                    ))}
                    {n.kind === 'job'
                      ? <circle className="an-shape" cx={cx} cy={cy} r={_AN_R} />
                      : <polygon className="an-shape"
                          points={`${cx},${cy - _AN_R - 1} ${cx - _AN_R - 1},${cy + _AN_R - 3} ${cx + _AN_R + 1},${cy + _AN_R - 3}`} />}
                  </g>
                );
              })}
            </svg>
          </div>
        </div>
      ) : (
        <p className="an-empty">No processing graph for this prep.</p>
      )}

      {archived.length > 0 && (
        <div className="an-archived">
          {archived.length} archived {archived.length === 1 ? 'artifact' : 'artifacts'} hidden, as on Qiita
          {' · '}
          <button type="button" className="an-link" onClick={() => setShowArchived(v => !v)}>
            {showArchived ? 'Hide' : 'Show'}
          </button>
          {showArchived && (
            <div className="an-archived-list">
              {archived.map(n => (
                <button type="button" key={n.node_id} className={`an-chip${selected === n.node_id ? ' an-on' : ''}`}
                  onClick={() => setSelected(n.node_id)}>
                  {n.name || n.artifact_type} <span className="an-chip-id">{n.artifact_id}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {sel ? (
        <ArtifactNetworkDetail key={sel.node_id} node={sel} label={labels[sel.node_id]}
          samples={counts[sel.artifact_id]} studyId={studyId} />
      ) : live.length > 0 && (
        <p className="an-hint">Click a node to see its files.</p>
      )}
    </div>
  );
}

// The clicked node: an artifact's id, type, visibility and files (a BIOM also
// its sample count and sample list), or a job's command and parameters.
function ArtifactNetworkDetail({ node, label, samples, studyId }) {
  const [peek, setPeek] = useState(false);
  if (node.kind === 'job') {
    const hasParams = Object.keys(node.command_params || {}).length > 0;
    return (
      <div className="an-detail">
        <div className="an-detail-title">{label || node.command_name}</div>
        <div className="an-detail-sub">Processing step{node.data_type ? ` · ${node.data_type}` : ''}</div>
        {hasParams ? <FlagList params={node.command_params} /> : <div className="an-detail-sub">No parameters recorded.</div>}
      </div>
    );
  }
  const files = node.filepaths || [];
  const isBiom = node.artifact_type === 'BIOM';
  const facts = [node.artifact_type, node.data_type, node.visibility && `Visibility: ${node.visibility}`,
    isBiom && samples != null && `${samples.toLocaleString()} samples`].filter(Boolean);
  return (
    <div className="an-detail">
      <div className="an-detail-title">
        {node.name || node.artifact_type} <span className="an-detail-id">(ID: {node.artifact_id})</span>
      </div>
      <div className="an-detail-sub">{facts.join(' · ')}</div>
      <div className="ao-files-row">
        {files.map(fp => <FileLink key={fp.filepath_id} fp={fp} artifactId={node.artifact_id} studyId={studyId} />)}
        {files.length === 0 && <span className="an-detail-sub">No files.</span>}
        {isBiom && (
          <button className="ao-samples-btn" onClick={() => setPeek(p => !p)}>
            {peek ? 'Hide samples' : 'View samples'}
          </button>
        )}
      </div>
      {peek && <SamplePeek artifactId={node.artifact_id} studyId={studyId} onClose={() => setPeek(false)} />}
    </div>
  );
}
