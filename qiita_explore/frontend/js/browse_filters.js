// Browse-page facet filters: PI, data type, year added. Loaded after
// utils.js / icons.js / hooks/useDropdown.js (whose globals it uses) and
// before app_state.js (which calls useBrowseFilters).
//
// "Year added" is EXTRACT(YEAR FROM qiita.study.first_contact) — when the
// study was created in Qiita. Qiita stores no publication date.

const EMPTY_BROWSE_FILTERS = { pis: [], dataTypes: [], yearMin: null, yearMax: null };

const hasBrowseFilters = f =>
  !!f && (f.pis.length > 0 || f.dataTypes.length > 0 || f.yearMin != null || f.yearMax != null);

// `filters` object for POST /api/search — empty keys omitted, undefined when
// nothing is set so the body key disappears entirely.
const browseFiltersBody = f => {
  if (!hasBrowseFilters(f)) return undefined;
  const body = {};
  if (f.pis.length)        body.pis        = f.pis;
  if (f.dataTypes.length)  body.data_types = f.dataTypes;
  if (f.yearMin != null)   body.year_min   = f.yearMin;
  if (f.yearMax != null)   body.year_max   = f.yearMax;
  return body;
};

function useBrowseFilters() {
  const [filters, setFilters] = useState(EMPTY_BROWSE_FILTERS);
  // {pis:[{name,count}], years:{min,max}, data_types:[{name,count}]} — null
  // until GET /api/search/facets answers; the pickers are disabled meanwhile.
  const [facets, setFacets] = useState(null);
  useEffect(() => {
    let alive = true;
    apiFetch('/search/facets')
      .then(async res => { if (alive && res.ok) setFacets(await res.json()); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  const clear = () => setFilters(EMPTY_BROWSE_FILTERS);
  return { filters, setFilters, clear, facets };
}

// The app's first multi-select dropdown: stays open after each toggle (the
// two earlier useDropdown consumers close on select) and opts out of the
// hover-away close so typing in the search box with the mouse elsewhere
// doesn't dismiss it.
function FacetMultiSelect({ label, options, selected, onChange, searchable, disabled }) {
  const dd = useDropdown(undefined, { hoverClose: false });
  const [q, setQ] = useState('');
  const ql = q.trim().toLowerCase();
  const all = options || [];
  const shown = (ql ? all.filter(o => o.name.toLowerCase().includes(ql)) : all).slice(0, 200);
  const toggle = name =>
    onChange(selected.includes(name) ? selected.filter(n => n !== name) : [...selected, name]);
  const n = selected.length;
  return (
    <div className="dd-root" ref={dd.rootRef}>
      <button type="button" ref={dd.btnRef} className={`dd-trigger bf-trigger ${n ? 'active' : ''}`}
        onClick={dd.toggle} disabled={disabled}>
        <span className="dd-trigger-label">
          {label}{n > 0 && <span className="bf-trigger-n">{n}</span>}
        </span>
        <ChevronIcon dir={dd.open ? 'up' : 'down'} size={11} />
      </button>
      {dd.open && dd.pos && (
        <div className={`${dd.menuClass} bf-menu`} style={{ top: dd.pos.top, left: dd.pos.left }}
          onClick={e => e.stopPropagation()}>
          {searchable && (
            <input className="bf-search" placeholder={`Search ${label.toLowerCase()}…`} value={q}
              autoFocus onChange={e => setQ(e.target.value)} />
          )}
          {shown.length === 0 && <div className="bf-empty">No matches</div>}
          {shown.map(o => {
            const on = selected.includes(o.name);
            return (
              <button key={o.name} type="button" className={`cr-menu-item bf-item ${on ? 'on' : ''}`}
                onClick={() => toggle(o.name)}>
                <span className="bf-check">{on && <CheckIcon size={12} />}</span>
                <span className="bf-item-name" title={o.name}>{o.name}</span>
                {o.count != null && <span className="bf-count">{o.count}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Dual-thumb range on two stacked native <input type="range">. Lives inline
// in the bar (not in a dropdown: dragging a thumb past a panel edge would
// dismiss it). Commits on release only, so a drag is one request, not one
// per pixel. A bound sitting at the facet edge is sent as null (= no bound).
function YearRangeSlider({ range, value, onCommit, disabled }) {
  const min = range?.min, max = range?.max;
  const [lo, setLo] = useState(value[0] ?? min);
  const [hi, setHi] = useState(value[1] ?? max);
  const cur = useRef({ lo, hi }); cur.current = { lo, hi };
  useEffect(() => { setLo(value[0] ?? min); setHi(value[1] ?? max); }, [value[0], value[1], min, max]);
  if (min == null || max == null || min >= max) return null;

  const pct = v => ((v - min) / (max - min)) * 100;
  const commit = () => {
    const a = Math.min(cur.current.lo, cur.current.hi), b = Math.max(cur.current.lo, cur.current.hi);
    const next = [a === min ? null : a, b === max ? null : b];
    if (next[0] !== value[0] || next[1] !== value[1]) onCommit(next);
  };
  const active = value[0] != null || value[1] != null;
  const a = Math.min(lo, hi), b = Math.max(lo, hi);
  return (
    <div className={`bf-range-wrap ${active ? 'active' : ''}`}>
      <span className="bf-range-label">Year added</span>
      <div className="bf-range">
        <div className="bf-range-track" />
        <div className="bf-range-fill" style={{ left: `${pct(a)}%`, right: `${100 - pct(b)}%` }} />
        {/* Low thumb goes on top only when both sit at the far end, so
            neither thumb can ever be buried under the other. */}
        <input type="range" min={min} max={max} step={1} value={lo} disabled={disabled}
          style={{ zIndex: lo >= max ? 5 : 3 }} aria-label="Year added, from"
          onChange={e => setLo(Math.min(+e.target.value, hi))}
          onMouseUp={commit} onTouchEnd={commit} onKeyUp={commit} />
        <input type="range" min={min} max={max} step={1} value={hi} disabled={disabled}
          style={{ zIndex: 4 }} aria-label="Year added, to"
          onChange={e => setHi(Math.max(+e.target.value, lo))}
          onMouseUp={commit} onTouchEnd={commit} onKeyUp={commit} />
      </div>
      <span className="bf-range-values">{a} – {b}</span>
    </div>
  );
}

function BrowseFilterBar({ facets, filters, onChange }) {
  const set = patch => onChange({ ...filters, ...patch });
  const chips = [
    ...filters.dataTypes.map(t => ({
      key: `dt:${t}`, label: t,
      remove: () => set({ dataTypes: filters.dataTypes.filter(x => x !== t) }),
    })),
    ...filters.pis.map(p => ({
      key: `pi:${p}`, label: p,
      remove: () => set({ pis: filters.pis.filter(x => x !== p) }),
    })),
  ];
  if (filters.yearMin != null || filters.yearMax != null) {
    const lo = filters.yearMin ?? facets?.years?.min ?? '…';
    const hi = filters.yearMax ?? facets?.years?.max ?? '…';
    chips.push({ key: 'year', label: `Added ${lo} – ${hi}`, remove: () => set({ yearMin: null, yearMax: null }) });
  }
  const loading = !facets;
  return (
    <div className="bf-bar">
      <FacetMultiSelect label="Data type" options={facets?.data_types} selected={filters.dataTypes}
        onChange={v => set({ dataTypes: v })} disabled={loading} />
      <FacetMultiSelect label="PI" searchable options={facets?.pis} selected={filters.pis}
        onChange={v => set({ pis: v })} disabled={loading} />
      {facets?.years && (
        <YearRangeSlider range={facets.years} value={[filters.yearMin, filters.yearMax]}
          onCommit={([a, b]) => set({ yearMin: a, yearMax: b })} />
      )}
      {chips.length > 0 && (
        <div className="bf-chips">
          {chips.map(c => (
            <span key={c.key} className="bf-chip">
              {c.label}
              <button type="button" className="bf-chip-x" onClick={c.remove} aria-label={`Remove ${c.label}`}>×</button>
            </span>
          ))}
          <button type="button" className="bf-clear" onClick={() => onChange(EMPTY_BROWSE_FILTERS)}>
            Clear filters
          </button>
        </div>
      )}
    </div>
  );
}
