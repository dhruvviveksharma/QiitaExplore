// One study card, shared by the Browse grid (app_render.js) and the Sample
// Aggregation tab (aggregation_detail.js) so the two never drift. It knows
// nothing about projects, pins or aggregations: the caller passes whatever
// buttons belong in the action row as `actions`; that row stops propagation,
// so buttons never trigger the card's onClick.
// Globals in scope: React (utils.js), splitTypes (utils.js)

function StudyCard({ study, onClick, actions, className }) {
  const dataTypeList = splitTypes(study.data_types);
  const metaParts = [
    study.num_samples != null ? `${study.num_samples} samples` : null,
    study.num_preps    != null ? `${study.num_preps} preps`    : null,
  ].filter(Boolean);
  return (
    <div className={`study-card${className ? ' ' + className : ''}`} onClick={onClick}>
      <div className="study-card-top">
        <div style={{display:'flex',gap:'6px',alignItems:'center'}}>
          <span className="study-id-badge">ID {study.study_id}</span>
          {study.year != null && <span className="study-year-badge" title="Year added to Qiita">{study.year}</span>}
          {study.is_gold && <span className="gold-badge">GOLD</span>}
        </div>
        {actions && <div className="study-card-actions" onClick={e => e.stopPropagation()}>{actions}</div>}
      </div>
      <div className="study-card-title">{study.study_title || 'Untitled study'}</div>
      <div className="study-card-abstract">{study.study_abstract || 'No abstract available.'}</div>
      {dataTypeList.length > 0 && (
        <div className="study-card-types">
          {dataTypeList.map(t => <span key={t} className="dtype-chip">{t}</span>)}
        </div>
      )}
      {metaParts.length > 0 && (
        <div className="study-card-meta">{metaParts.join(' · ')}</div>
      )}
      {(study.pi_name || study.pi_affiliation) && (
        <div className="study-card-pi">
          {[study.pi_name, study.pi_affiliation].filter(Boolean).join(' · ')}
        </div>
      )}
    </div>
  );
}
