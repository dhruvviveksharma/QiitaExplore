// Right-hand drawer listing search_studies result_studies in relevance order.
function SearchResultsPanel({ studies, title, detail, closing, onClose, onExited, onPin, onMerge, onOpen, isPinned }) {
  const list = studies || [];
  const exited = useRef(false);
  const finish = () => {
    if (exited.current) return;
    exited.current = true;
    onExited?.();
  };
  useEffect(() => {
    if (!closing) return;
    const t = setTimeout(finish, 280);
    return () => clearTimeout(t);
  }, [closing]);
  return (
    <div
      className={`search-results-panel${closing ? ' closing' : ''}`}
      role="dialog"
      aria-label="Search results"
      onAnimationEnd={e => {
        if (e.target !== e.currentTarget) return;
        if (closing && e.animationName === 'search-results-out') finish();
      }}
      onTransitionEnd={e => {
        if (e.target !== e.currentTarget) return;
        if (closing && e.propertyName === 'transform') finish();
      }}
    >
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
