// Hash-based URL sync for view + study modal, extracted from useAppState.
// Hash routing (not pushState) because local dev serves the frontend as
// plain static files with no SPA fallback rewrite — a hash never leaves the
// browser, so it works identically in dev and behind nginx.

// Pure parse: hash path -> view. Defensive against malformed hashes and
// against SHOW_MERGES being off (app_state.js's single toggle for the
// merges feature must not be bypassable via a hand-typed/bookmarked URL).
function hashToView(path) {
  let m;
  if ((m = /^\/projects\/([^/]+)\/chats\/([^/]+)\/?$/.exec(path)))
    return { type: 'project-chat', projId: decodeURIComponent(m[1]), chatId: decodeURIComponent(m[2]) };
  if ((m = /^\/chats\/([^/]+)\/?$/.exec(path)))
    return { type: 'global-chat', chatId: decodeURIComponent(m[1]) };
  if (path === '/merges' && SHOW_MERGES)
    return { type: 'merges' };
  return { type: 'browse' };
}

// Pure serialize: view -> hash path (no '?study=' suffix, no leading '#').
function viewToPath(view) {
  if (view.type === 'project-chat' && view.projId && view.chatId)
    return `/projects/${view.projId}/chats/${view.chatId}`;
  if (view.type === 'global-chat' && view.chatId)
    return `/chats/${view.chatId}`;
  if (view.type === 'merges' && SHOW_MERGES)
    return '/merges';
  return '/';
}

// Full hash -> {view, studyId}. studyId is a global overlay independent of
// the path — a study link works after ANY backdrop, since studies aren't
// owned by a project or chat.
function parseHash(hash) {
  const h = (hash || '').replace(/^#/, '');
  const qIdx = h.indexOf('?');
  const path = qIdx === -1 ? h : h.slice(0, qIdx);
  const raw  = new URLSearchParams(qIdx === -1 ? '' : h.slice(qIdx + 1)).get('study');
  const studyId = raw && /^\d+$/.test(raw) ? parseInt(raw, 10) : null;
  return { view: hashToView(path || '/'), studyId };
}

// {view, studyId} -> full hash (always includes leading '#').
function buildHash(view, studyId) {
  return '#' + viewToPath(view) + (studyId != null ? `?study=${studyId}` : '');
}

function useUrlSync({ view, setView, setOpenProjId, openChat, modalStudy, openStudyById, closeModal }) {
  // Mount: `view` and `modalStudy` were already lazily seeded from the hash
  // in their useState initializers, but nothing has been fetched yet.
  // Unconditional — must NOT reuse the echo-guard below, or the one hydrate
  // that needs to happen here gets skipped.
  useEffect(() => {
    if (view.type === 'project-chat' && view.projId) setOpenProjId(view.projId);
    if (view.type === 'project-chat' || view.type === 'global-chat') openChat(view);
    if (modalStudy?.study_id != null) openStudyById(modalStudy.study_id);
  }, []);

  // Back/forward + manual hash edits. Must ignore hashchange events that are
  // just an echo of the write-effect below (window.location.hash = x fires
  // hashchange asynchronously for ANY change, including our own), or every
  // internal navigation re-triggers a redundant restore a moment later. For
  // chats this isn't just waste: deleting the open chat transitions view to
  // {type:'project-chat', chatId:null, ...}, which collapses to '#/' — an
  // unguarded listener would parse that as {type:'browse'} and bounce the
  // user out of "Select a chat" into full Browse. Compare the HASH STRING
  // (path + study suffix together), not parsed objects — a deleted-chat
  // view's type won't equal 'browse' as an object, but both collapse to the
  // same string, which is what needs to be recognized as "nothing changed."
  useEffect(() => {
    const onHashChange = () => {
      const newHash = window.location.hash || '#/';
      if (buildHash(view, modalStudy?.study_id ?? null) === newHash) return; // echo of our own last write
      const { view: target, studyId } = parseHash(newHash);
      if (target.type === 'project-chat' && target.projId) setOpenProjId(target.projId);
      if (target.type === 'project-chat' || target.type === 'global-chat') openChat(target);
      else setView(target);
      if (studyId != null) openStudyById(studyId);
      else closeModal();
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [view, modalStudy]);

  // Push the URL whenever view or modalStudy changes, regardless of who
  // changed it. Self-guarding (only writes if it actually differs), so
  // firing redundantly is harmless.
  useEffect(() => {
    const next = buildHash(view, modalStudy?.study_id ?? null);
    if (window.location.hash !== next) window.location.hash = next;
  }, [view, modalStudy]);
}
