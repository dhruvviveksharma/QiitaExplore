// Artifact processing graph for the Merge panel

const COL_W = 175;
const ROW_H = 70;
const NODE_W = 148;
const NODE_H = 42;
const JOB_RX  = 46;
const JOB_RY  = 19;

const ARTIFACT_COLORS = {
  BIOM:               '#3b82f6',
  Demultiplexed:      '#0d9488',
  FASTQ:              '#6b7280',
  per_sample_FASTQ:   '#6b7280',
  SFF:                '#8b5cf6',
  FASTA:              '#f59e0b',
  default:            '#9ca3af',
};

function _nodeColor(n) {
  if (n.kind === 'job') return '#d97706';
  return ARTIFACT_COLORS[n.artifact_type] || ARTIFACT_COLORS.default;
}

function buildGraphLayout(nodes) {
  const byId = {};
  nodes.forEach(n => { byId[n.node_id] = n; });

  // Roots: no parent, or parent not in this filtered set
  const roots = nodes.filter(n => !n.parent_node_id || !byId[n.parent_node_id]);

  // BFS to assign rank (column index)
  const rank = {};
  const q = roots.map(n => ({ id: n.node_id, r: 0 }));
  while (q.length) {
    const { id, r } = q.shift();
    if (rank[id] !== undefined) continue;
    rank[id] = r;
    nodes.filter(n => n.parent_node_id === id).forEach(n => q.push({ id: n.node_id, r: r + 1 }));
  }

  // Assign vertical position within each rank
  const rankPos = {};
  const perRank = {};
  nodes.forEach(n => {
    const r = rank[n.node_id] ?? 0;
    perRank[r] = perRank[r] || 0;
    rankPos[n.node_id] = perRank[r]++;
  });

  const edges = nodes
    .filter(n => n.parent_node_id && byId[n.parent_node_id])
    .map(n => ({ from: n.parent_node_id, to: n.node_id }));

  const maxRank = Math.max(0, ...nodes.map(n => rank[n.node_id] ?? 0));
  const maxPos  = Math.max(0, ...nodes.map(n => rankPos[n.node_id] ?? 0));

  return {
    nodes: nodes.map(n => ({ ...n, rank: rank[n.node_id] ?? 0, rankPos: rankPos[n.node_id] ?? 0 })),
    edges,
    maxRank,
    maxPos,
  };
}

function _cx(rank) { return rank * COL_W + COL_W / 2; }
function _cy(rp)   { return rp * ROW_H + ROW_H / 2 + 10; }

function ArtifactGraphSvg({ graphData, chosenId, onPickArtifact }) {
  const { nodes, edges, maxRank, maxPos } = graphData;
  const W = (maxRank + 1) * COL_W + 20;
  const H = (maxPos + 1) * ROW_H + 30;

  const byId = {};
  nodes.forEach(n => { byId[n.node_id] = n; });

  return (
    <svg width={W} height={H} style={{ display: 'block' }}>
      {/* Edges behind nodes */}
      {edges.map((e, i) => {
        const fn = byId[e.from];
        const tn = byId[e.to];
        if (!fn || !tn) return null;
        const fx = _cx(fn.rank) + (fn.kind === 'job' ? JOB_RX : NODE_W / 2);
        const fy = _cy(fn.rankPos);
        const tx = _cx(tn.rank) - (tn.kind === 'job' ? JOB_RX : NODE_W / 2);
        const ty = _cy(tn.rankPos);
        const mx = (fx + tx) / 2;
        return (
          <path key={i} d={`M${fx},${fy} C${mx},${fy} ${mx},${ty} ${tx},${ty}`}
            fill="none" stroke="#cbd5e1" strokeWidth="1.5" />
        );
      })}

      {/* Nodes */}
      {nodes.map(n => {
        const cx = _cx(n.rank);
        const cy = _cy(n.rankPos);
        const color = _nodeColor(n);

        if (n.kind === 'job') {
          const lbl = (n.command_name || '').length > 16
            ? n.command_name.slice(0, 14) + '…'
            : n.command_name;
          return (
            <g key={n.node_id}>
              <ellipse cx={cx} cy={cy} rx={JOB_RX} ry={JOB_RY}
                fill="#fef3c7" stroke={color} strokeWidth="1.5" />
              <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle"
                fontSize="9" fill="#92400e" style={{ pointerEvents: 'none' }}>
                {lbl}
              </text>
            </g>
          );
        }

        const isBiom   = n.artifact_type === 'BIOM';
        const isChosen = n.artifact_id === chosenId;
        const fname    = n.full_path ? n.full_path.split('/').pop() : null;
        const label    = n.name || (n.artifact_type + ' ' + n.artifact_id);
        const trunc    = (s, max) => s && s.length > max ? s.slice(0, max - 1) + '…' : (s || '');

        return (
          <g key={n.node_id}
            style={{ cursor: isBiom ? 'pointer' : 'default' }}
            onClick={isBiom ? () => onPickArtifact(isChosen ? null : n.artifact_id) : undefined}>
            <rect
              x={cx - NODE_W / 2} y={cy - NODE_H / 2}
              width={NODE_W} height={NODE_H} rx="4" ry="4"
              fill={isBiom && isChosen ? '#eff6ff' : '#f8fafc'}
              stroke={isBiom ? (isChosen ? '#3b82f6' : color) : '#e2e8f0'}
              strokeWidth={isBiom && isChosen ? 2.5 : 1}
            />
            {/* Type label (top-left) */}
            <text x={cx - NODE_W / 2 + 5} y={cy - 7}
              fontSize="8.5" fill={color} style={{ pointerEvents: 'none' }}>
              {n.artifact_type || '?'}
            </text>
            {/* Name */}
            <text x={cx} y={cy + 5}
              textAnchor="middle" fontSize="9.5" fill="#374151"
              style={{ pointerEvents: 'none' }}>
              {trunc(label, 20)}
            </text>
            {/* Filename for BIOMs */}
            {isBiom && fname && (
              <text x={cx} y={cy + NODE_H / 2 - 4}
                textAnchor="middle" fontSize="8" fill="#6b7280"
                style={{ pointerEvents: 'none' }}>
                {trunc(fname, 22)}
              </text>
            )}
            {/* Selection checkmark */}
            {isChosen && (
              <text x={cx + NODE_W / 2 - 11} y={cy - NODE_H / 2 + 13}
                fontSize="12" fill="#3b82f6" style={{ pointerEvents: 'none' }}>
                ✓
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function ArtifactGraphView({ detail, loading, chosenId, onPickArtifact, dtFilter }) {
  if (loading && !detail) return <div className="modal-detail-loading">Loading…</div>;
  if (!detail) return null;

  const graph = detail.artifact_graph;

  // Fallback to flat table if no graph data (stale cache)
  if (!graph || graph.length === 0) {
    const arts = detail.artifacts || [];
    if (!arts.length) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>No artifacts found.</p>;
    return (
      <table className="prep-table">
        <thead><tr><th>Use</th><th>ID</th><th>Type</th><th>Data Type</th><th>File</th></tr></thead>
        <tbody>
          {arts.slice(0, 20).map(a => (
            <tr key={a.artifact_id}>
              <td>{a.artifact_type === 'BIOM' && (a.full_path || '').endsWith('.biom') && (
                <input type="checkbox" checked={a.artifact_id === chosenId}
                  onChange={() => onPickArtifact(a.artifact_id === chosenId ? null : a.artifact_id)} />
              )}</td>
              <td>{a.artifact_id}</td><td>{a.artifact_type}</td><td>{a.data_type}</td>
              <td>{(a.full_path || '').split('/').pop()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  const filtered = dtFilter
    ? graph.filter(n => (n.data_type || '').includes(dtFilter))
    : graph;

  if (!filtered.length) return <p style={{ color: 'var(--text-3)', fontSize: '0.85rem' }}>No artifacts for selected data type.</p>;

  const graphData = buildGraphLayout(filtered);
  return (
    <div className="artifact-graph-scroll">
      <ArtifactGraphSvg graphData={graphData} chosenId={chosenId} onPickArtifact={onPickArtifact} />
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
