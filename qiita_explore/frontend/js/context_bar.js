// The composer's context bar, just before the model chip: how full this chat's
// last request was, split by what filled it. The server measures every LLM
// call (backend helpers/context_usage.py) and sends a `context_usage` SSE
// event; the turn's last one is saved on the chat, so a reopened chat shows it
// too (app_state.js keeps it as chatCache[chatId].contextUsage). Click for the
// breakdown. After a model switch the same tokens are shown against the new
// model's window until the next reply measures again.
// Globals in scope: useDropdown (hooks/useDropdown.js)

const _CTX_PARTS = [
  ['system',       'System prompt'],
  ['tools',        'Tools'],
  ['studies',      'Studies'],
  ['summary',      'Earlier summary'],
  ['conversation', 'Conversation'],
  ['turn',         'This turn'],
];
const _CTX_PANEL_W = 270;   // .cbar-panel width
const _CTX_PANEL_H = 250;   // about its height: room needed to open upward

function _fmtTokens(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, '')}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(Math.round(n || 0));
}

function ContextBar({ usage, model }) {
  // Opens upward (the composer sits at the bottom; below if there's no room), right edge on the bar's.
  const dd = useDropdown(r => ({
    ...(r.top > _CTX_PANEL_H ? { bottom: document.documentElement.clientHeight - r.top + 6 } : { top: r.bottom + 6 }),
    left: Math.max(8, Math.min(r.right - _CTX_PANEL_W, window.innerWidth - _CTX_PANEL_W - 8)),
  }));
  if (!usage || !usage.total) return null;

  const otherModel = !!model && !!usage.model && model !== usage.model;
  const windowSize = (otherModel && usage.windows?.[model]) || usage.window || 0;
  const pct = n => (windowSize ? (n / windowSize) * 100 : 0);
  const pctText = n => (n > 0 && pct(n) < 1 ? '<1' : String(Math.round(pct(n))));
  const used = Math.min(100, pct(usage.total));
  const parts = usage.parts || {};

  return (
    <div className="cbar-root" ref={dd.rootRef}>
      <button type="button" ref={dd.btnRef} className={`cbar${used >= 90 ? ' cbar-full' : ''}`} onClick={dd.toggle}
              title={`≈${_fmtTokens(usage.total)} of ${_fmtTokens(windowSize)} tokens in context`}>
        <span className="cbar-track">
          {_CTX_PARTS.map(([k]) => parts[k] > 0 && (
            <span key={k} className={`cbar-seg cbar-${k}`} style={{ width: `${Math.min(100, pct(parts[k]))}%` }} />
          ))}
        </span>
        <span className="cbar-pct">{pctText(usage.total)}%</span>
      </button>
      {dd.open && dd.pos && (
        <div className={`${dd.menuClass} cbar-panel`} style={{ top: dd.pos.top, bottom: dd.pos.bottom, left: dd.pos.left }}
             onClick={e => e.stopPropagation()}>
          <div className="cbar-head">
            Context · {model || usage.model}
            <span className="cbar-head-num">{_fmtTokens(usage.total)} / {_fmtTokens(windowSize)} tokens</span>
          </div>
          {_CTX_PARTS.map(([k, label]) => (
            <div key={k} className="cbar-row">
              <span className={`cbar-dot cbar-${k}`} />
              <span className="cbar-label">{label}</span>
              <span className="cbar-num">{_fmtTokens(parts[k] || 0)}</span>
              <span className="cbar-num cbar-num-pct">{pctText(parts[k] || 0)}%</span>
            </div>
          ))}
          <div className="cbar-row">
            <span className="cbar-dot cbar-free" />
            <span className="cbar-label">Free</span>
            <span className="cbar-num">{_fmtTokens(Math.max(0, windowSize - usage.total))}</span>
            <span className="cbar-num cbar-num-pct">{Math.round(100 - used)}%</span>
          </div>
          <div className="cbar-foot">
            {!otherModel && usage.compact_at > 0 && <div>Auto-compacts at ~{Math.round(pct(usage.compact_at))}%</div>}
            <div>{usage.measured ? 'Counted by the model' : 'Estimated from the text'} · last request</div>
            {otherModel && <div>Measured on {usage.model} — updates after your next message</div>}
          </div>
        </div>
      )}
    </div>
  );
}
