// Shared state/positioning/dismiss logic for every custom dropdown in the
// app — see CLAUDE.md "Established Patterns: Dropdown UI" for the
// convention this exists to enforce. Loads before components.js (unlike
// the other hooks/) since ChatRowMenu needs it there.
//
// Renders position:fixed, with coordinates computed from the trigger
// button's own rect — not position:absolute relative to a CSS-positioned
// ancestor. A dropdown inside any scrolling container (the sidebar,
// eventually a scrolling modal body, etc.) would otherwise get clipped by
// that container's overflow for any trigger near its edge.
function useDropdown(computePos) {
  const [open, setOpen] = useState(false);
  const [pos,  setPos]  = useState(null);
  const rootRef = useRef(null);
  const btnRef  = useRef(null);
  const leaveTimerRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = e => { if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false); };
    const onKey  = e => { if (e.key === 'Escape') setOpen(false); };
    // Hover-away dismissal: the dropdown stays open only while the cursor is
    // over the trigger or the panel (both DOM children of rootRef, so
    // mouseenter/mouseleave cover them even though the panel is
    // position:fixed). The grace delay bridges the few-px gap between the
    // trigger and the panel so travelling across it doesn't close the menu.
    const el = rootRef.current;
    const onLeave = () => { leaveTimerRef.current = setTimeout(() => setOpen(false), 300); };
    const onEnter = () => clearTimeout(leaveTimerRef.current);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    el?.addEventListener('mouseleave', onLeave);
    el?.addEventListener('mouseenter', onEnter);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      el?.removeEventListener('mouseleave', onLeave);
      el?.removeEventListener('mouseenter', onEnter);
      clearTimeout(leaveTimerRef.current);
    };
  }, [open]);

  // Default: left-aligned, directly below the trigger (matches a <select>).
  // Pass computePos(rect) to override — e.g. ChatRowMenu right-aligns its
  // "..." menu to a fixed width instead.
  const toggle = e => {
    e && e.stopPropagation();
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setPos(computePos ? computePos(r) : { top: r.bottom + 4, left: r.left });
    }
    setOpen(v => !v);
  };

  return { open, setOpen, pos, rootRef, btnRef, toggle };
}
