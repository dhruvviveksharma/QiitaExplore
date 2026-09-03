'use strict';

// Shared helpers (ported from DNA Loaders.html)
function _lRgba(hex, a) {
  const n = parseInt(hex.replace('#', ''), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function _ringCrossings(n, ph, t0, t1) {
  const out = [];
  for (let k = Math.ceil((n * t0 + ph) / Math.PI);
           k <= Math.floor((n * t1 + ph) / Math.PI); k++) {
    const t = (k * Math.PI - ph) / n;
    if (t > t0 + 1e-9 && t < t1 - 1e-9) out.push(t);
  }
  return out;
}

function _makeSegs(crx, lo, hi) {
  const segs = []; let prev = lo;
  for (const c of crx) { segs.push([prev, c]); prev = c; }
  segs.push([prev, hi]);
  return segs;
}

const _LT = '#1ea8a8', _LG = '#40ad6e', _LC = '#e07555';
const _LP = _lRgba('#9ad8d0', 0.28);

function _setupCanvas(canvas, w, h) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width  = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width  = w + 'px';
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  return ctx;
}

function _drawDuplex(ctx, segs, f1topFn, strand) {
  for (const [a, b] of segs) {
    const f1top = f1topFn(a, b);
    ctx.strokeStyle = f1top ? _lRgba(_LG, 0.75) : _lRgba(_LT, 0.75);
    strand(a, b, !f1top, 1.5);
    ctx.strokeStyle = f1top ? _LT : _LG;
    strand(a, b,  f1top, 2.5);
  }
}

// ─── InfinityLoader ───────────────────────────────────────────────────────────
function InfinityLoader({ w = 80, h = 50 }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = _setupCanvas(canvas, w, h);
    const cx = w / 2, cy = h / 2;
    const SX = w * 0.37, SY = SX * 0.54, r = SX * 0.22, n = 5;

    function figPos(t) { return [cx + SX * Math.sin(t), cy + SY * Math.sin(2 * t)]; }
    function figNorm(t) {
      const dx = SX * Math.cos(t), dy = 2 * SY * Math.cos(2 * t);
      const L  = Math.sqrt(dx * dx + dy * dy) || 1;
      return [-dy / L, dx / L];
    }

    let rafId, t0;
    function frame(ts) {
      if (!t0) t0 = ts;
      const ph = 1.19 * (ts - t0) / 1000;
      ctx.clearRect(0, 0, w, h);
      const segs = _makeSegs(_ringCrossings(n, ph, 0, Math.PI * 2), 0, Math.PI * 2);

      const RN = n * 6;
      for (let i = 0; i < RN; i++) {
        const t = (i / RN) * Math.PI * 2, s = Math.sin(n * t + ph);
        const [px, py] = figPos(t), [nx, ny] = figNorm(t);
        const isHL = (i % Math.round(RN / n)) === 0;
        ctx.strokeStyle = isHL ? _lRgba(_LC, 0.85) : _LP;
        ctx.lineWidth   = isHL ? 1.5 : 0.8;
        ctx.beginPath();
        ctx.moveTo(px + r * s * nx, py + r * s * ny);
        ctx.lineTo(px - r * s * nx, py - r * s * ny);
        ctx.stroke();
      }

      function strand(tS, tE, isS1, lw) {
        const N = Math.max(10, Math.round((tE - tS) / (Math.PI * 2) * 400));
        ctx.beginPath();
        for (let i = 0; i <= N; i++) {
          const t = tS + (i / N) * (tE - tS);
          const s = Math.sin(n * t + ph) * (isS1 ? 1 : -1);
          const [px, py] = figPos(t), [nx, ny] = figNorm(t);
          i === 0 ? ctx.moveTo(px + r * s * nx, py + r * s * ny)
                  : ctx.lineTo(px + r * s * nx, py + r * s * ny);
        }
        ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.stroke();
      }

      _drawDuplex(ctx, segs, (tS, tE) => Math.sin(n * (tS + tE) / 2 + ph) > 0, strand);
      rafId = requestAnimationFrame(frame);
    }
    rafId = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafId);
  }, [w, h]);
  return <canvas ref={ref} style={{display:'block'}} />;
}

// ─── WreathLoader ─────────────────────────────────────────────────────────────
// Icon-scale rendering (28-32px): a single bold stroke per segment reads far
// crisper than the demo-scale duplex "woven shadow" trick, which just smears
// at this size, and fewer/higher-contrast ring ticks avoid a hazy look.
function WreathLoader({ size = 32 }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = _setupCanvas(canvas, size, size);
    const cx = size / 2, cy = size / 2;
    const R = size * 0.355, r = size * 0.11, n = 5;
    const pale = _lRgba('#9ad8d0', 0.5);

    let rafId, t0;
    function frame(ts) {
      if (!t0) t0 = ts;
      const ph = 1.54 * (ts - t0) / 1000;
      ctx.clearRect(0, 0, size, size);
      const segs = _makeSegs(_ringCrossings(n, ph, 0, Math.PI * 2), 0, Math.PI * 2);

      const RN = n * 3;
      for (let i = 0; i < RN; i++) {
        const t  = (i / RN) * Math.PI * 2, s = Math.sin(n * t + ph);
        const r1 = R + r * s, r2 = R - r * s;
        const isHL = (i % Math.round(RN / n)) === 0;
        ctx.strokeStyle = isHL ? _LC : pale;
        ctx.lineWidth   = isHL ? 2 : 1;
        ctx.beginPath();
        ctx.moveTo(cx + r1 * Math.cos(t), cy + r1 * Math.sin(t));
        ctx.lineTo(cx + r2 * Math.cos(t), cy + r2 * Math.sin(t));
        ctx.stroke();
      }

      function strand(tS, tE, isS1, lw) {
        const N = Math.max(8, Math.round((tE - tS) / (Math.PI * 2) * 400));
        ctx.beginPath();
        for (let i = 0; i <= N; i++) {
          const t   = tS + (i / N) * (tE - tS);
          const rad = R + r * Math.sin(n * t + ph) * (isS1 ? 1 : -1);
          i === 0 ? ctx.moveTo(cx + rad * Math.cos(t), cy + rad * Math.sin(t))
                  : ctx.lineTo(cx + rad * Math.cos(t), cy + rad * Math.sin(t));
        }
        ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.stroke();
      }

      for (const [tS, tE] of segs) {
        const f1top = Math.sin(n * (tS + tE) / 2 + ph) > 0;
        ctx.strokeStyle = f1top ? _LT : _LG;
        strand(tS, tE, !f1top, 1.8);
        ctx.strokeStyle = f1top ? _LG : _LT;
        strand(tS, tE, f1top, 1.8);
      }
      rafId = requestAnimationFrame(frame);
    }
    rafId = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafId);
  }, [size]);
  return <canvas ref={ref} style={{display:'block'}} />;
}
