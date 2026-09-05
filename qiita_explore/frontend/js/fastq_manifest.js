// Per-sample FASTQ manifest download — a study-modal section listing every
// per_sample_FASTQ artifact with a QIIME2 V2 manifest link.
// Globals in scope: React (utils.js), API (utils.js), CollapsibleSection (components.js)

const _SEQ_FP_TYPE = /^raw_(forward|reverse)_seqs$/;

function FastqManifestSection({ detail, studyId }) {
  const nodes = (detail?.artifact_graph || [])
    .filter(n => n.kind === 'artifact' && n.artifact_type === 'per_sample_FASTQ');
  if (!nodes.length) return null;

  return (
    <CollapsibleSection id="study-modal-fastq" title="Per-sample FASTQ" subtitle={`${nodes.length}`} defaultOpen>
      <table className="prep-table">
        <thead>
          <tr><th>Artifact</th><th>Prep</th><th>Data Type</th><th>Seq files</th><th></th></tr>
        </thead>
        <tbody>
          {nodes.map(n => (
            <tr key={n.artifact_id}>
              <td>{n.artifact_id}{n.name ? ` · ${n.name}` : ''}</td>
              <td>{n.prep_template_id ?? '—'}</td>
              <td>{n.data_type || '—'}</td>
              <td>{(n.filepaths || []).filter(f => _SEQ_FP_TYPE.test(f.filepath_type)).length}</td>
              <td>
                {/* Plain link like FileLink (merge_artifacts.js): the session cookie
                    rides along on top-level navigation; Content-Disposition names the file. */}
                <a className="ao-file-btn" target="_blank" rel="noreferrer"
                  href={`${API}/artifacts/${n.artifact_id}/fastq-manifest?study_id=${studyId}`}>
                  ↓ QIIME2 manifest
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </CollapsibleSection>
  );
}
