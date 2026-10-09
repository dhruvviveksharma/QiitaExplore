// The actions a study offers wherever it is shown — Browse cards and the
// study modal's header (compact and fullscreen) — rendered by one component
// so the two can't drift. `ctx` is built once per render in app_render.js
// (studyActionsCtx):
//   { agg, openProjId, projStudyIds, addStudyToProject,
//     pin: { isOn(sid), toggle(study) } | null,   // null → no Pin button
//     onMerge: (study) => … | null }              // null → no Merge button
// With a project open, "+ Add to Project" takes Pin's place (as on the card).
// Globals in scope: AggregateCardButton (aggregations.js)

function StudyActions({ study, ctx }) {
  const { agg, openProjId, projStudyIds, addStudyToProject, pin, onMerge } = ctx;
  const sid = study.study_id;
  const inProj = projStudyIds.includes(sid);
  const pinned = !!pin && pin.isOn(sid);
  return (
    <>
      {openProjId ? (
        <button className="btn-card-add" disabled={inProj} onClick={() => addStudyToProject(study)}>
          {inProj ? '✓ Saved' : '+ Add to Project'}
        </button>
      ) : pin && (
        <button className={`btn-card-ctx ${pinned ? 'on' : ''}`} onClick={() => pin.toggle(study)}>
          {pinned ? '✓ Pinned' : '+ Pin'}
        </button>
      )}
      <AggregateCardButton study={study} agg={agg} />
      {onMerge && <button className="btn-card-merge" onClick={() => onMerge(study)}>+ Merge</button>}
    </>
  );
}
