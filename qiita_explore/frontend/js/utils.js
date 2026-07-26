const { useState, useEffect, useRef, useMemo, useCallback } = React;

// TODO: revert fallback to port 5001 before committing to master
// 127.0.0.1, not localhost: the session cookie is host-only, so this must
// match whichever host the browser used to reach the page.
const API = document.querySelector('meta[name="api-base"]')?.content
          || 'http://127.0.0.1:5002/api';

const formatDate = (dateStr) =>
  new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

const splitTypes = (dataTypes) =>
  (dataTypes || '').split(',').map(t => t.trim()).filter(Boolean);

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

const apiFetch = (path, opts = {}) => {
  const method  = (opts.method || 'GET').toUpperCase();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (_csrfToken && _STATE_CHANGING_METHODS.has(method)) {
    headers['X-CSRF-Token'] = _csrfToken;
  }
  return fetch(`${API}${path}`, { ...opts, headers, credentials: 'include' });
};
const apiPost  = (path, body) => apiFetch(path, { method: 'POST',   body: JSON.stringify(body) });
const apiPatch = (path, body) => apiFetch(path, { method: 'PATCH',  body: JSON.stringify(body) });
const apiDel   = (path)       => apiFetch(path, { method: 'DELETE' });

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
