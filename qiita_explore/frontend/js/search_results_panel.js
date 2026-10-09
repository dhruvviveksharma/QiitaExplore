// Right-hand drawer listing a search_studies result set in relevance order.
// Deep searches can carry hundreds of matches, so rows render in windows of
// _PANEL_PAGE with a show-more button — no virtualization machinery needed.
// Drag its left edge (or focus the edge and use ← / →) to resize it. The cards
// stay centered and grow with it by at most 25% before another column fits
// (search_results.css). The width is remembered in this browser, and the
// page makes room for it through --drawer-space (style.css: .main.merge-open and
// the study modal's .with-drawer offset).
const _PANEL_PAGE = 100;
const _PANEL_W_KEY = 'qe-results-panel-width';
const _PANEL_DEFAULT_W = 400;
const _PANEL_MIN_W = 320;
const _MAIN_MIN_W = 360;    // what the chat keeps beside the drawer
const _DRAWER_GAP = 30;     // the drawer's 12px inset + breathing room

function _panelMaxW() {
  const sidebar = document.querySelector('.sidebar')?.getBoundingClientRect().width || 0;
  return Math.max(_PANEL_MIN_W, window.innerWidth - sidebar - _MAIN_MIN_W - _DRAWER_GAP);
}

function _savedPanelW() {
  try {
    const w = Number(localStorage.getItem(_PANEL_W_KEY));
    return w >= _PANEL_MIN_W ? w : _PANEL_DEFAULT_W;
  } catch (_) { return _PANEL_DEFAULT_W; }
}

function SearchResultsPanel({ studies, title, detail, closing, onClose, onExited, onPin, onMerge, onOpen, isPinned }) {
  const list = studies || [];
  const [shown, setShown] = useState(_PANEL_PAGE);
  useEffect(() => { setShown(_PANEL_PAGE); }, [studies]);
  const visible = list.slice(0, shown);
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

  const [width, setWidth] = useState(() => Math.min(_savedPanelW(), _panelMaxW()));
  const save = (w) => { try { localStorage.setItem(_PANEL_W_KEY, String(Math.round(w))); } catch (_) {} };
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--drawer-space', `${width + _DRAWER_GAP}px`);
    return () => root.style.removeProperty('--drawer-space');
  }, [width]);
  useEffect(() => {   // a smaller window never squeezes the chat below _MAIN_MIN_W
    const fit = () => setWidth(w => Math.min(w, _panelMaxW()));
    fit();             // again once mounted: the sidebar is measurable now
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, []);

  const startResize = (e) => {
    e.preventDefault();
    const startX = e.clientX, startW = width, max = _panelMaxW();
    const root = document.documentElement;
    let last = startW;
    root.classList.add('drawer-resizing');
    const move = (ev) => { last = Math.min(max, Math.max(_PANEL_MIN_W, startW + startX - ev.clientX)); setWidth(last); };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      root.classList.remove('drawer-resizing');
      save(last);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  const keyResize = (e) => {
    const step = e.key === 'ArrowLeft' ? 40 : e.key === 'ArrowRight' ? -40 : 0;
    if (!step) return;
    e.preventDefault();
    const w = Math.min(_panelMaxW(), Math.max(_PANEL_MIN_W, width + step));
    setWidth(w);
    save(w);
  };

  return (
    <div
      className={`search-results-panel${closing ? ' closing' : ''}`}
      style={{ width }}
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
      <div className="search-results-resize" role="separator" aria-orientation="vertical"
        aria-label="Resize search results" aria-valuenow={Math.round(width)} tabIndex={0}
        title="Drag to resize" onPointerDown={startResize} onKeyDown={keyResize} />
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
            {visible.map((s, i) => (
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
            {shown < list.length && (
              <button className="show-more-btn" onClick={() => setShown(n => n + _PANEL_PAGE)}>
                Show {Math.min(_PANEL_PAGE, list.length - shown)} more ({list.length - shown} remaining)
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
