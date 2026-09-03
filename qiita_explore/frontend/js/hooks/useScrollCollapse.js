// Collapses the chat topbar on a deliberate scroll-down gesture, expands it
// back on scroll-up — driven by wheel direction rather than scrollTop alone,
// same reasoning as StudyModal's handleCardWheel (study_modal.js): a short
// chat has no scrollable range, so position alone can't tell "scrolled back
// up" from "nothing to scroll," which would otherwise flicker.
function useScrollCollapse(resetKey) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => { setCollapsed(false); }, [resetKey]);

  const onWheel = (e) => {
    const el = e.currentTarget;
    if (e.deltaY < 0 || el.scrollTop <= 8) {
      setCollapsed(false);
    } else if (e.deltaY > 0 && el.scrollHeight - el.clientHeight > 8) {
      setCollapsed(true);
    }
  };

  // Reaching the top always expands — including via auto-scroll or keyboard,
  // not just the wheel gesture onWheel already handles.
  const onScroll = (e) => {
    if (e.currentTarget.scrollTop <= 8) setCollapsed(false);
  };

  return { collapsed, onWheel, onScroll, barClass: collapsed ? ' compact' : '' };
}
