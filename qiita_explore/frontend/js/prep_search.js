// Prep search for the study view's Prep Templates and Prep tree sections
// (study_detail.js): type digits to list the preps whose ID starts with them
// (every prep when the box is empty). ↓ / ↑ move the highlight; Tab or Enter
// completes the highlighted prep — the first match when none is highlighted —
// and selects it; Escape closes. The list is a useDropdown panel (position:
// fixed from the input, per CLAUDE.md "Dropdown UI"); rows pick on mousedown so
// the input keeps focus. `extra` adds one non-prep entry (the Prep tree's "Other").
// Globals in scope: React, useState, useEffect, useRef (utils.js), useDropdown (hooks/useDropdown.js)

const _PREP_SEARCH_MAX = 50;   // rows listed at once (AGP has 308 preps)
const _PREP_SEARCH_W = 280;    // .prep-search-menu width
const _prepSearchPos = r => ({
  top: r.bottom + 4, left: Math.max(8, Math.min(r.left, window.innerWidth - _PREP_SEARCH_W - 8)),
});

function PrepSearch({ preps, value, onPick, extra }) {
  const [q, setQ] = useState('');
  const [hi, setHi] = useState(-1);          // -1: nothing highlighted yet
  const dd = useDropdown(_prepSearchPos, { hoverClose: false });
  const listRef = useRef(null);

  const items = (preps || []).map(p => ({
    id: p.prep_template_id, label: `Prep ${p.prep_template_id}`,
    sub: [p.data_type, p.investigation_type].filter(Boolean).join(' · '),
  }));
  if (extra) items.push(extra);
  const query = q.trim();
  const all = items.filter(it => !query || String(it.id === 'other' ? 'other' : it.id)
    .startsWith(it.id === 'other' ? query.toLowerCase() : query));
  const matches = all.slice(0, _PREP_SEARCH_MAX);

  useEffect(() => { setHi(-1); }, [q]);
  useEffect(() => {   // keep the highlighted row in view while arrowing through a long list
    listRef.current?.querySelector('.prep-search-row.on')?.scrollIntoView?.({ block: 'nearest' });
  }, [hi]);

  const openMenu = () => { if (!dd.open) dd.toggle(); };
  const pick = (it) => {
    setQ(it.id === 'other' ? 'Other' : String(it.id));
    setHi(-1);
    dd.setOpen(false);
    onPick(it.id);
  };
  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault(); openMenu(); setHi(i => Math.min(i + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault(); setHi(i => Math.max(i - 1, 0));
    } else if ((e.key === 'Tab' || e.key === 'Enter') && matches.length && (dd.open || query)) {
      e.preventDefault(); pick(matches[hi >= 0 ? hi : 0]);
    } else if (e.key === 'Escape') {
      dd.setOpen(false);
    }
  };

  return (
    <div className="prep-search" ref={dd.rootRef}>
      <input ref={dd.btnRef} className="prep-search-input" value={q} inputMode="numeric"
        placeholder={value != null && value !== 'other' ? `Prep ${value} · search prep ID…` : 'Search prep ID…'}
        onFocus={openMenu} onChange={e => { setQ(e.target.value); openMenu(); }} onKeyDown={onKeyDown}
        aria-label="Search prep ID" />
      {dd.open && dd.pos && (
        <div ref={listRef} className={`${dd.menuClass} prep-search-menu`} style={{ top: dd.pos.top, left: dd.pos.left }}>
          {matches.length === 0
            ? <div className="prep-search-empty">No prep ID starts with “{query}”</div>
            : matches.map((it, i) => (
              <button key={it.id} type="button"
                className={`cr-menu-item prep-search-row${i === hi ? ' on' : ''}${it.id === value ? ' cur' : ''}`}
                onMouseDown={e => { e.preventDefault(); pick(it); }} onMouseEnter={() => setHi(i)}>
                <span className="prep-search-id">{it.label}</span>
                {it.sub && <span className="prep-search-sub">{it.sub}</span>}
              </button>
            ))}
          {all.length > matches.length && (
            <div className="prep-search-empty">+{all.length - matches.length} more · keep typing</div>
          )}
        </div>
      )}
    </div>
  );
}
