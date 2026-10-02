// Close a panel that is always open while mounted (the chat's model picker)
// when the user presses the mouse anywhere outside it. Returns the ref to put
// on the panel's root. `ignoreSelector` names an outside element that toggles
// the panel itself (its own click handler would otherwise reopen it).
//
// The listener is attached on the next tick: the panel can be opened by a
// mousedown (the "+" menu's /model row, the slash menu) that is still bubbling
// to the document when the panel mounts, and must not close it again.
// Globals in scope: useRef, useEffect (utils.js)

function useOutsideClose(onClose, ignoreSelector) {
  const ref = useRef(null);
  const cb  = useRef(onClose);
  cb.current = onClose;
  useEffect(() => {
    const onDown = e => {
      if (ref.current?.contains(e.target)) return;
      if (ignoreSelector && e.target.closest?.(ignoreSelector)) return;
      cb.current();
    };
    const t = setTimeout(() => document.addEventListener('mousedown', onDown), 0);
    return () => { clearTimeout(t); document.removeEventListener('mousedown', onDown); };
  }, []);
  return ref;
}
