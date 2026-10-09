// ↑ / ↓ recall in a text box, like a shell: ↑ steps back through what was sent
// from that box, ↓ forward, and ↓ past the newest brings back what was being
// typed. Used by the chat composer and the Browse search bar (app_state.js
// records each send / search; app_render.js passes their keydown here).
//
// Only from the edge of the text: ↑ when the caret is on the first line, ↓ on
// the last, so moving within a multi-line message still works. Modifier keys
// are left alone. The last 50 entries per box are kept in localStorage — a
// per-browser convenience, so a blocked or cleared storage just starts empty.
// Globals in scope: useState, useRef (utils.js)

const _HISTORY_MAX = 50;

function _readHistory(key) {
  try {
    const v = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(v) ? v.filter(x => typeof x === 'string').slice(-_HISTORY_MAX) : [];
  } catch (_) { return []; }
}

function useInputHistory(name) {
  const key = `qe-history:${name}`;
  const [items, setItems] = useState(() => _readHistory(key));   // oldest first
  const pos   = useRef(null);   // index while browsing; null = not browsing
  const draft = useRef('');     // what was typed before the first ↑
  const shown = useRef(null);   // the entry last put in the box

  const record = (text) => {
    const t = (text || '').trim();
    pos.current = null;
    if (!t) return;
    setItems(prev => {
      const next = [...prev.filter(x => x !== t), t].slice(-_HISTORY_MAX);
      try { localStorage.setItem(key, JSON.stringify(next)); } catch (_) {}
      return next;
    });
  };

  // Handles ↑ / ↓ when it should; true when it did (the caller then stops).
  const onKey = (e, value, setValue) => {
    if ((e.key !== 'ArrowUp' && e.key !== 'ArrowDown') || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return false;
    const el = e.target;
    const start = el.selectionStart ?? value.length, end = el.selectionEnd ?? value.length;
    if (e.key === 'ArrowUp' && value.slice(0, start).includes('\n')) return false;
    if (e.key === 'ArrowDown' && value.slice(end).includes('\n')) return false;
    if (pos.current !== null && value !== shown.current) pos.current = null;   // edited since: start over
    if (e.key === 'ArrowUp') {
      if (!items.length) return false;
      if (pos.current === null) { draft.current = value; pos.current = items.length; }
      if (pos.current > 0) pos.current -= 1;
      shown.current = items[pos.current];
    } else {
      if (pos.current === null) return false;
      pos.current += 1;
      shown.current = pos.current < items.length ? items[pos.current] : draft.current;
      if (pos.current >= items.length) pos.current = null;
    }
    e.preventDefault();
    setValue(shown.current);
    return true;
  };

  // Browsing only while the box still shows the recalled entry: an edit (e.g.
  // into a slash command) hands the arrows back to the caller.
  return { items, record, onKey, browsing: (value) => pos.current !== null && value === shown.current };
}
