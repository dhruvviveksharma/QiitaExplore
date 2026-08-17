// Right-hand drawer listing search_studies result_studies in relevance order.
function SearchResultsPanel({ studies, title, detail, onClose, onPin, onMerge, onOpen, isPinned }) {
  const list = studies || [];
  return (
    <div className="search-results-panel" role="dialog" aria-label="Search results">
      <div className="search-results-panel-header">
        <div className="search-results-panel-heading">
          <span className="search-results-panel-title">{title || 'Search results'}</span>
          {detail && <span className="search-results-panel-detail">{detail}</span>}
          <span className="search-results-panel-count">
            {list.length} {list.length === 1 ? 'study' : 'studies'} · by relevance
          </span>
        </div>
        <button type="button" className="search-results-panel-close" onClick={onClose} title="Close">×</button>
      </div>
      <div className="search-results-panel-body">
        {list.length === 0 ? (
          <p className="search-results-empty">No studies in this result set.</p>
        ) : (
          <div className="search-results-list">
            {list.map((s, i) => (
              <div key={s.study_id} className="search-results-row">
                <span className="search-results-rank" aria-label={`Rank ${i + 1}`}>#{i + 1}</span>
                <div className="search-results-card-wrap">
                  <InlineStudyCard
                    study={s}
                    isPinned={isPinned?.(s.study_id)}
                    onPin={onPin}
                    onMerge={onMerge}
                    onOpen={onOpen}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
