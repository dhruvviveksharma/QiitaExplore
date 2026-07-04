// ─── Shared inline SVG icons ─────────────────────────────────────────────────
// Replaces unicode glyphs (☽ ☀ ⊕ ⚡ ▸ ▾ ▶ ◀) with a consistent stroke-icon set.

function ChevronIcon({ open, dir, size = 12, style = {} }) {
  let rotation = 0;
  if (dir === 'left') rotation = 180;
  else if (dir === 'up') rotation = -90;
  else if (dir === 'down') rotation = 90;
  else if (open != null) rotation = open ? 90 : 0;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, transition: 'transform 140ms ease', transform: `rotate(${rotation}deg)`, ...style }}>
      <polyline points="9 6 15 12 9 18" />
    </svg>
  );
}

function MergeIcon({ size = 13, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, ...style }}>
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
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, ...style }}>
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 2v2.5M12 19.5V22M4.2 4.2l1.8 1.8M18 18l1.8 1.8M2 12h2.5M19.5 12H22M4.2 19.8l1.8-1.8M18 6l1.8-1.8" />
    </svg>
  );
}

function MoonIcon({ size = 14, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, ...style }}>
      <path d="M20 14.3A8.3 8.3 0 1 1 9.7 4 6.6 6.6 0 0 0 20 14.3z" />
    </svg>
  );
}

function BoltIcon({ size = 13, style = {} }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0, ...style }}>
      <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" />
    </svg>
  );
}
