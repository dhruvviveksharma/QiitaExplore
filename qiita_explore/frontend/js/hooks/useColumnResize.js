// Drag-to-resize table columns (the Sample Aggregation sample table). `defaults`
// maps a column key to its starting width in px; a key it doesn't list gets 140.
// widthOf(key) feeds a <colgroup>; startResize(key, event) goes on a header
// handle's onMouseDown. isResizing() is true from mousedown until just after the
// click that follows the releasing mouseup, so a header onClick can ignore the click
// that ends a drag. Widths live in component state only (not persisted).
const _MIN_COL_WIDTH = 40;

function useColumnResize(defaults) {
  const [widths, setWidths] = useState({});
  const resizing = React.useRef(false);
  const widthOf = (key) => widths[key] ?? defaults[key] ?? 140;

  const startResize = (key, e) => {
    e.preventDefault();
    e.stopPropagation();
    resizing.current = true;
    const startX = e.clientX, startW = widthOf(key);
    const move = (ev) => setWidths(w => ({ ...w, [key]: Math.max(_MIN_COL_WIDTH, startW + ev.clientX - startX) }));
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      setTimeout(() => { resizing.current = false; }, 0);   // click fires right after mouseup
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  return { widthOf, startResize, isResizing: () => resizing.current };
}
