// A study's header and body, shared by the study modal (study_modal.js) and the
// full study page at #/studies/<id> (StudyPage below). Body sections, in order:
// Abstract (with the PI and contact), Samples, Prep Templates (table + the
// selected prep's processing graph), Prep tree. Both prep sections pick a prep
// through PrepSearch (prep_search.js) when the study has more than one.
// The Per-sample FASTQ section (FastqManifestSection, fastq_manifest.js) is
// hidden for now.
// Globals in scope: React, useState, useEffect (utils.js), apiFetch, fetchStudyDetail (utils.js),
//   CollapsibleSection, PrepsTable, SamplesBrowser, CopyResponseButton (components.js),
//   PrepGroupedSamples (prep_samples.js), ArtifactOutputsView, prepReachableSet,
//   filterGraphByPrep (merge_artifacts.js), ArtifactNetwork (artifact_network.js),
//   StudyActionBar (study_modal.js), splitTypes (utils.js), PrepSearch (prep_search.js)

// The sticky bar: ID, copy-link, the caller's buttons on the right, title, stats.
function StudyHeader({ study, detail, loading, shareUrl, leading, right }) {
  const showStats = !(!loading && detail?.isPrivate) &&
    (study.data_types || study.num_samples != null || study.num_preps != null);
  return (
    <div className="modal-header-bar">
      <div className="modal-header-top">
        <div className="modal-header-left">
          {leading}
          <span className="modal-id">Study ID {study.study_id}</span>
          {shareUrl && (
            <div className="modal-copy-link">
              <CopyResponseButton title="Copy study link" text={shareUrl} />
            </div>
          )}
        </div>
        <div className="modal-header-right">{right}</div>
      </div>
      <div className="modal-title">{study.study_title || (loading ? 'Loading…' : 'Untitled study')}</div>
      {showStats && (
        <div className="modal-stats">
          {splitTypes(study.data_types).map(t => (
            <span key={t} className="dtype-chip">{t}</span>
          ))}
          {study.num_samples != null && <span className="modal-stat">{study.num_samples} samples</span>}
          {study.num_preps   != null && <span className="modal-stat">{study.num_preps} preps</span>}
        </div>
      )}
    </div>
  );
}

// One sample's metadata fields (or null), for SamplesBrowser / PrepGroupedSamples.
const sampleFieldsFetcher = (studyId) => async (sampleId) => {
  const res = await apiFetch(`/studies/${studyId}/samples/${encodeURIComponent(sampleId)}`);
  if (!res.ok) return null;
  const d = await res.json();
  return d.fields || null;
};

// Samples: the first 200 with each clicked sample's metadata beside them, or —
// with Group by prep — every prep's samples. Also the chat's samples widget.
function StudySamplesSection({ studyId, samples }) {
  const [groupByPrep, setGroupByPrep] = useState(false);   // cluster under prep headers
  const fetchFields = sampleFieldsFetcher(studyId);
  return (
    <>
      <div className="agg-seg" style={{ marginBottom: 8, width: 'fit-content' }}>
        <button className={groupByPrep ? 'on' : ''} title="Cluster samples under their prep template"
          onClick={() => setGroupByPrep(v => !v)}>Group by prep</button>
      </div>
      {groupByPrep
        ? <PrepGroupedSamples key={studyId} studyId={studyId} fetchFields={fetchFields} />
        : <SamplesBrowser samples={samples || []} layout="two-pane" fetchFields={fetchFields} />}
    </>
  );
}

// Callers key this by study id, so per-study state (Group by prep, the selected
// prep, the Prep tree's prep) starts fresh for each study.
function StudyDetailBody({ study, detail, loading }) {
  if (!loading && detail?.isPrivate) {
    return (
      <div style={{display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'3rem 1rem', gap:'1rem', color:'#aaa'}}>
        <span style={{fontSize:'4rem', lineHeight:1}}>✕</span>
        <span style={{fontSize:'1rem', fontStyle:'italic', textAlign:'center'}}>This is a private study and its data is not publicly available.</span>
      </div>
    );
  }
  return (
    <>
      {(study.study_abstract || study.pi_name || study.pi_email) && (
        <CollapsibleSection id="study-modal-abstract" title="Abstract" defaultOpen>
          {study.study_abstract && <p>{study.study_abstract}</p>}
          {(study.pi_name || study.pi_email) && (
            <div className="study-people">
              {study.pi_name && (
                <div><span className="study-people-k">Principal Investigator</span>
                  {study.pi_name}{study.pi_affiliation ? ` — ${study.pi_affiliation}` : ''}</div>
              )}
              {study.pi_email && <div><span className="study-people-k">Contact</span>{study.pi_email}</div>}
            </div>
          )}
        </CollapsibleSection>
      )}

      {!loading && detail && (
        <CollapsibleSection id="study-modal-samples" title="Samples"
          subtitle={detail.total_samples != null
            ? `${detail.total_samples} total${detail.total_samples > 200 ? ', showing first 200' : ''}`
            : undefined}
          defaultOpen>
          <StudySamplesSection studyId={study.study_id} samples={detail.samples} />
        </CollapsibleSection>
      )}

      <CollapsibleSection id="study-modal-preps" title="Prep Templates"
        subtitle={detail ? `${(detail.preps || []).length}` : undefined} defaultOpen>
        <PrepTemplatesSection study={study} detail={detail} loading={loading} />
      </CollapsibleSection>

      {/* id kept from its "Outputs" days, so saved open/closed states carry over */}
      <CollapsibleSection id="study-modal-outputs" title="Prep tree" defaultOpen>
        <StudyOutputs study={study} detail={detail} loading={loading} />
      </CollapsibleSection>
    </>
  );
}

// The prep table, and beside it (below it when the modal is too narrow for
// both) the selected prep's processing graph. The first prep is selected until
// a row is clicked or a prep is searched. graphProps go to ArtifactNetwork (the
// chat's options).
function PrepTemplatesSection({ study, detail, loading, graphProps }) {
  const [picked, setPicked] = useState(null);
  const preps = detail?.preps || [];
  const graph = detail?.artifact_graph || [];
  const prep  = picked ?? preps[0]?.prep_template_id ?? null;
  const dt    = preps.find(p => p.prep_template_id === prep)?.data_type;
  return (
    <>
    {preps.length > 1 && <PrepSearch preps={preps} value={prep} onPick={setPicked} />}
    <div className="study-prep-split">
      <div className="study-prep-table">
        <PrepsTable detail={detail} loading={loading} selectedId={prep} onSelect={setPicked} />
      </div>
      {prep != null && graph.length > 0 && (
        <div className="study-prep-graph">
          <div className="study-prep-graph-title">Prep {prep}{dt ? ` · ${dt}` : ''}</div>
          <ArtifactNetwork key={prep} graph={filterGraphByPrep(graph, prep)} studyId={study.study_id} {...graphProps} />
        </div>
      )}
    </div>
    </>
  );
}

// The "Prep tree" section: the study's processing network (ArtifactNetwork), one
// prep at a time like Qiita's own chart. PrepSearch picks the prep (default the
// first; AGP has 308) and is hidden for a single-prep study. "Other" holds
// non-archived artifacts that no prep reaches. A study whose cached detail has no
// graph keeps the flat table.
function StudyOutputs({ study, detail, loading }) {
  const [prepFilter, setPrepFilter] = useState('');   // '' = the study's first prep
  const graph = detail?.artifact_graph || [];
  if (!graph.length) {
    return (
      <div>
        <ArtifactOutputsView detail={detail} loading={loading} chosenIds={[]} onToggleArtifact={() => {}}
          prepFilter="" recommendedId={null} sampleCounts={{}} studyId={study.study_id} selectable={false} />
        <StudyActionBar study={study} />
      </div>
    );
  }
  const preps = detail.preps || [];
  const reachable = prepReachableSet(graph);
  const orphans = graph.filter(n => !reachable.has(n.node_id));
  const hasOrphans = orphans.some(n => !(n.kind === 'artifact' && n.visibility === 'archived'));
  const prep = prepFilter === '' ? (preps[0]?.prep_template_id ?? 'other') : prepFilter;
  const shown = prep === 'other' ? orphans : filterGraphByPrep(graph, prep);

  return (
    <div>
      {(preps.length > 1 || (hasOrphans && preps.length > 0)) && (
        <PrepSearch preps={preps} value={prep} onPick={setPrepFilter}
          extra={hasOrphans ? { id: 'other', label: 'Other', sub: 'artifacts no prep reaches' } : null} />
      )}
      <ArtifactNetwork key={String(prep)} graph={shown} studyId={study.study_id} />
      <StudyActionBar study={study} />
    </div>
  );
}

// #/studies/<id>: the study at full width, opened from the modal's expand
// button or a link. Loads its own header and detail (the detail through the
// shared fetchStudyDetail cache, so coming from the modal costs nothing).
function StudyPage({ studyId, shareUrl, renderActions, onBack }) {
  const [study,   setStudy]   = useState({ study_id: studyId });
  const [detail,  setDetail]  = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setStudy({ study_id: studyId }); setDetail(null); setLoading(true);
    Promise.all([
      apiFetch(`/studies/${studyId}`).then(r => (r.ok ? r.json() : { study_id: studyId })),
      fetchStudyDetail(studyId),
    ])
      .then(([s, d]) => { if (live) { setStudy(s); setDetail(d); } })
      .catch(() => {})
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [studyId]);

  return (
    <div className="study-page">
      <div className="modal-card study-page-card">
        <StudyHeader study={study} detail={detail} loading={loading} shareUrl={shareUrl}
          leading={<button className="study-page-back" onClick={onBack}>← Back</button>}
          right={renderActions && <div className="modal-header-actions">{renderActions(study)}</div>} />
        <StudyDetailBody key={studyId} study={study} detail={detail} loading={loading} />
      </div>
    </div>
  );
}
