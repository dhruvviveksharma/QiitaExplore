// ─── Shared inline SVG icons ─────────────────────────────────────────────────
// Replaces unicode glyphs (☽ ☀ ⊕ ⚡ ▸ ▾ ▶ ◀) with a consistent stroke-icon set.

const _ICON_STYLE = { display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 };

function ChevronIcon({ open, dir, size = 12, style = {} }) {
  let rotation = 0;
  if (dir === 'left') rotation = 180;
  else if (dir === 'up') rotation = -90;
  else if (dir === 'down') rotation = 90;
  else if (open != null) rotation = open ? 90 : 0;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ ..._ICON_STYLE, transition: 'transform 140ms ease', transform: `rotate(${rotation}deg)`, ...style }}>
      <polyline points="9 6 15 12 9 18" />
    </svg>
  );
}

function MergeIcon({ size = 13, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      style={{ ..._ICON_STYLE, ...style }}>
      <circle cx="7" cy="6" r="2.5" />
      <circle cx="7" cy="18" r="2.5" />
      <circle cx="17" cy="12" r="2.5" />
      <path d="M7 8.5v7M9.3 6.9 14.7 10.1M9.3 17.1 14.7 13.9" />
    </svg>
  );
}

function SunIcon({ size = 14, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round"
      style={{ ..._ICON_STYLE, ...style }}>
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 2v2.5M12 19.5V22M4.2 4.2l1.8 1.8M18 18l1.8 1.8M2 12h2.5M19.5 12H22M4.2 19.8l1.8-1.8M18 6l1.8-1.8" />
    </svg>
  );
}

function MoonIcon({ size = 14, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      style={{ ..._ICON_STYLE, ...style }}>
      <path d="M20 14.3A8.3 8.3 0 1 1 9.7 4 6.6 6.6 0 0 0 20 14.3z" />
    </svg>
  );
}

function BoltIcon({ size = 13, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
      style={{ ..._ICON_STYLE, ...style }}>
      <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" />
    </svg>
  );
}

function CopyIcon({ size = 13, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      style={{ ..._ICON_STYLE, ...style }}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon({ size = 13, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ ..._ICON_STYLE, ...style }}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
