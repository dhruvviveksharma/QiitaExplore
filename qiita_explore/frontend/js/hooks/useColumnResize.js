// Drag-to-resize table columns (the Sample Aggregation sample table). `defaults`
// maps a column key to its starting width in px; a key it doesn't list gets 140.
// widthOf(key) feeds a <colgroup>; startResize(key, event) goes on a header
// handle's onMouseDown. Widths live in component state only (not persisted).
const _MIN_COL_WIDTH = 40;

function useColumnResize(defaults) {
  const [widths, setWidths] = useState({});
  const widthOf = (key) => widths[key] ?? defaults[key] ?? 140;

  const startResize = (key, e) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX, startW = widthOf(key);
    const move = (ev) => setWidths(w => ({ ...w, [key]: Math.max(_MIN_COL_WIDTH, startW + ev.clientX - startX) }));
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  return { widthOf, startResize };
}
