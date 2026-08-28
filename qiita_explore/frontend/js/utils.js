const { useState, useEffect, useRef, useMemo, useCallback } = React;

// Relative by default so the same build works same-origin behind any proxy
// (production nginx, a Cloudflare Tunnel, etc.) with no host to keep in
// sync. index.html's <meta name="api-base"> overrides this for local dev
// where the frontend and backend are on different origins.
const API = document.querySelector('meta[name="api-base"]')?.content || '/api';

const formatDate = (dateStr) =>
  new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

const splitTypes = (dataTypes) =>
  (dataTypes || '').split(',').map(t => t.trim()).filter(Boolean);

// One label for every pinned/context chip. Three surfaces render these — the
// topbar sources bar, the composer bar and the browse staging bar — and they had
// drifted to 40/38/32 chars.
const CHIP_TITLE_MAX = 38;
const pinChipLabel = (s) => {
  const t = s.study_title;
  if (!t) return `Study ${s.study_id}`;
  return `${s.study_id} · ${t.length > CHIP_TITLE_MAX ? t.slice(0, CHIP_TITLE_MAX) + '…' : t}`;
};

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* fall through */ }
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

// ─── HTTP helpers ──────────────────────────────────────────────────────────────
// Identity now comes from the session cookie (set by POST /auth/connect), not
// a client-supplied user_id. auth.js calls setCsrfToken() once /auth/me or
// /auth/connect resolves; every state-changing request below picks it up
// automatically — no per-call-site wiring needed.
const _STATE_CHANGING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
let _csrfToken = null;
const setCsrfToken   = (token) => { _csrfToken = token || null; };
const clearCsrfToken = () => { _csrfToken = null; };

// A dead session must not look like empty data. Handled here rather than at each
// call site: every request goes through apiFetch, and the callers that check
// `res.ok` at all mostly do nothing on failure, so a 401 used to render as a
// blank pane. auth.js registers the handler; see useAuth.
let _onUnauthorized = null;
const setUnauthorizedHandler = (fn) => { _onUnauthorized = fn; };

const apiFetch = async (path, opts = {}) => {
  const method  = (opts.method || 'GET').toUpperCase();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (_csrfToken && _STATE_CHANGING_METHODS.has(method)) {
    headers['X-CSRF-Token'] = _csrfToken;
  }
  const res = await fetch(`${API}${path}`, { ...opts, headers, credentials: 'include' });
  // /auth/connect legitimately 401s on a bad token — routing that through the
  // handler would fight the sign-in screen it is trying to render.
  if (res.status === 401 && !path.startsWith('/auth/') && _onUnauthorized) {
    _onUnauthorized();
  }
  return res;
};
// Parsed body or throw. Collapses the res.ok + res.json().catch + message
// extraction dance that every caller otherwise hand-writes — and half of them
// skip, which is how a failed request renders as empty data.
const apiJson = async (path, opts = {}) => {
  const res  = await apiFetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
};

const apiPost  = (path, body) => apiFetch(path, { method: 'POST',   body: JSON.stringify(body) });
const apiPatch = (path, body) => apiFetch(path, { method: 'PATCH',  body: JSON.stringify(body) });
const apiDel   = (path)       => apiFetch(path, { method: 'DELETE' });

// ─── Markdown (marked + DOMPurify) ─────────────────────────────────────────────
let _mdReady = false;
function _ensureMarked() {
  if (_mdReady || typeof marked === 'undefined') return;
  if (typeof marked.use === 'function') {
    marked.use({ gfm: true, breaks: true });
  } else if (typeof marked.setOptions === 'function') {
    marked.setOptions({ gfm: true, breaks: true });
  }
  _mdReady = true;
}
function renderMarkdown(text) {
  _ensureMarked();
  const src = text == null ? '' : String(text);
  const raw = typeof marked !== 'undefined'
    ? (typeof marked.parse === 'function' ? marked.parse(src) : marked(src))
    : src;
  let html = typeof DOMPurify !== 'undefined'
    ? DOMPurify.sanitize(raw, { ADD_ATTR: ['target'] })
    : raw;
  // External links open in a new tab — applied post-sanitize so it works across marked versions.
  html = html.replace(
    /<a\s+([^>]*?)href="(https?:\/\/[^"]+)"([^>]*)>/gi,
    (m, pre, href, post) => {
      if (/\btarget=/i.test(pre + post)) return m;
      return `<a ${pre}href="${href}"${post} target="_blank" rel="noopener noreferrer">`;
    }
  );
  // Isolate wide tables so only they scroll horizontally — not the whole bubble.
  return html.replace(
    /<table\b[\s\S]*?<\/table>/gi,
    (table) => `<div class="md-table-wrap">${table}</div>`
  );
}

// Module-scope coalescing for /studies/<id>/detail. All callers (modal + every
// SamplesReportBubble) share one in-flight promise + one cached result per study,
// so we don't slam the (slow, single-transaction) Qiita DB with parallel duplicates.
const _studyDetailCache   = new Map();
const _studyDetailInflight = new Map();
async function fetchStudyDetail(studyId, { signal } = {}) {
  if (_studyDetailCache.has(studyId)) return _studyDetailCache.get(studyId);
  if (_studyDetailInflight.has(studyId)) return _studyDetailInflight.get(studyId);
  const p = (async () => {
    const res = await apiFetch(`/studies/${studyId}/detail`, signal ? { signal } : {});
    if (res.status === 404) return { isPrivate: true };
    if (!res.ok) throw new Error(`detail ${res.status}`);
    const d = await res.json();
    _studyDetailCache.set(studyId, d);
    return d;
  })().finally(() => { _studyDetailInflight.delete(studyId); });
  _studyDetailInflight.set(studyId, p);
  return p;
}

async function parseSSE(response, { onToken, onUi, onDone, onError, onStepStart, onStepDone,
                                    onAgentStart, onSegmentToolCall, onSegmentToolResult }, signal) {
  const reader = response.body.getReader();
  const dec    = new TextDecoder();
  let buf      = '';
  while (true) {
    if (signal?.aborted) break;
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) !== -1) {
      const raw = buf.slice(0, i); buf = buf.slice(i + 2);
      let type = 'message', data = '{}';
      for (const ln of raw.split('\n')) {
        if (ln.startsWith('event:')) type = ln.slice(6).trim();
        if (ln.startsWith('data:'))  data = ln.slice(5).trim();
      }
      let payload = {};
      try { payload = JSON.parse(data); } catch (_) {}
      if (type === 'token'      && onToken)     onToken(payload);
      if (type === 'ui'         && onUi)        onUi(payload);
      if (type === 'done'       && onDone)      onDone(payload);
      if (type === 'error'      && onError)     onError(payload);
      if (type === 'step_start'          && onStepStart)          onStepStart(payload);
      if (type === 'step_done'           && onStepDone)           onStepDone(payload);
      if (type === 'agent_start'         && onAgentStart)         onAgentStart(payload);
      if (type === 'segment_tool_call'   && onSegmentToolCall)    onSegmentToolCall(payload);
      if (type === 'segment_tool_result' && onSegmentToolResult)  onSegmentToolResult(payload);
    }
  }
}
