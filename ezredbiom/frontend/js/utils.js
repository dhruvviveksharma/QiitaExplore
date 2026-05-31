const { useState, useEffect, useRef, useMemo, useCallback } = React;

const API     = document.querySelector('meta[name="api-base"]')?.content
              || 'http://localhost:5001/api';
const USER_ID = 'default';

// ─── HTTP helpers ──────────────────────────────────────────────────────────────
const apiFetch = (path, opts = {}) =>
  fetch(`${API}${path}`, { headers: { 'Content-Type': 'application/json' }, ...opts });
const apiPost  = (path, body) => apiFetch(path, { method: 'POST',   body: JSON.stringify(body) });
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

async function parseSSE(response, { onToken, onUi, onDone, onError, onStepStart, onStepDone, onQueryPlan }, signal) {
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
      if (type === 'step_start'  && onStepStart)  onStepStart(payload);
      if (type === 'step_done'   && onStepDone)   onStepDone(payload);
      if (type === 'query_plan'  && onQueryPlan)  onQueryPlan(payload);
    }
  }
}
