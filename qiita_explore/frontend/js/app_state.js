// Single toggle for all merge-feature entry points (nav tab, floating
// toggle, per-study "+ Merge", in-chat "Merge →"). Flip to true to restore.
const SHOW_MERGES = false;

// Shared title-truncation for auto-generated chat titles.
const truncateTitle = (msg) => (msg || '').slice(0, 60);

function useAppState() {
  const [projects,    setProjects]    = useState([]);
  const [projLoading, setProjLoading] = useState(true);
  const [openProjId,  setOpenProjId]  = useState(null);
  const [openProject, setOpenProject] = useState(null);
  const [view,        setView]        = useState(() => parseHash(window.location.hash).view);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [chatCache,   setChatCache]   = useState({});
  const [globalChats, setGlobalChats] = useState([]);
  const [projInnerTab, setProjInnerTab] = useState('chats');
  const [query,        setQuery]        = useState('');
  const [results,      setResults]      = useState([]);
  const [firstStudies, setFirstStudies] = useState([]);
  const [searching,    setSearching]    = useState(false);
  const [searched,     setSearched]     = useState(false);
  const [sqlQuery,     setSqlQuery]     = useState(null);
  const [appliedFilters, setAppliedFilters] = useState(null);
  const [showSql,      setShowSql]      = useState(false);
  const [ctxStudies,   setCtxStudies]   = useState([]);
  const [showNewProj,  setShowNewProj]  = useState(false);
  const [newProjName,  setNewProjName]  = useState('');
  // Which sidebar chat row (project or global) is being renamed inline, if any.
  const [editingChatId, setEditingChatId] = useState(null);
  const [editChatVal,   setEditChatVal]   = useState('');
  // Archived-chats "show archived" toggle, per list. archivedProjChats resets
  // whenever openProjId changes (see the openProjId effect below) — it's
  // meaningless across a project switch.
  const [showArchivedProj,   setShowArchivedProj]   = useState(false);
  const [archivedProjChats,  setArchivedProjChats]  = useState(null);
  const [showArchivedGlobal, setShowArchivedGlobal] = useState(false);
  const [archivedGlobalChats, setArchivedGlobalChats] = useState(null);
  const [mergeWorkspaceId, setMergeWorkspaceId] = useState(null);
  const [showMergePanel,   setShowMergePanel]   = useState(false);
  const [pendingMergeStudy, setPendingMergeStudy] = useState(null);
  // { studies, title, detail } | null — right drawer for search_studies "View all"
  const [searchResultsPanel, setSearchResultsPanel] = useState(null);
  const [searchResultsClosing, setSearchResultsClosing] = useState(false);
  const openSearchResultsPanel = (payload) => {
    setShowMergePanel(false);
    setSearchResultsClosing(false);
    setSearchResultsPanel(payload || null);
  };
  const closeSearchResultsPanel = () => {
    if (!searchResultsPanel || searchResultsClosing) return;
    setSearchResultsClosing(true);
  };
  const finishCloseSearchResultsPanel = () => {
    setSearchResultsPanel(null);
    setSearchResultsClosing(false);
  };
  const openMergePanel = (open = true) => {
    if (open) {
      setSearchResultsPanel(null);
      setSearchResultsClosing(false);
    }
    setShowMergePanel(!!open);
  };
  const [input,   setInput]   = useState('');
  const [sending, setSending] = useState(false);
  const [compErr,        setCompErr]        = useState('');
  const [addStudyErr,    setAddStudyErr]    = useState('');
  const [slashIndex,     setSlashIndex]     = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const { selectedModel, setSelectedModel, showModelPicker, setShowModelPicker } = useModelSelection(view.chatId);
  const scrollCollapse = useScrollCollapse(view.chatId);
  const [showPlusMenu,    setShowPlusMenu]    = useState(false);
  const [anthropicKeySet, setAnthropicKeySet] = useState(false);
  const [theme, setThemeState] = useState(() => {
    try { return localStorage.getItem('ui:theme') || 'light'; } catch (_) { return 'light'; }
  });
  const setTheme = (t) => {
    setThemeState(t);
    try { localStorage.setItem('ui:theme', t); } catch (_) {}
  };
  const [modalStudy,         setModalStudy]         = useState(() => {
    const { studyId } = parseHash(window.location.hash);
    return studyId != null ? { study_id: studyId } : null;
  });
  const [modalDetail,        setModalDetail]        = useState(null);
  const [modalDetailLoading, setModalDetailLoading] = useState(false);
  const [projDetailLoading,  setProjDetailLoading]  = useState(false);
  const [chatLoading,        setChatLoading]        = useState(false);

  const abortRef      = useRef(null);
  const taRef         = useRef(null);
  const bottomRef     = useRef(null);
  const modalAbortRef = useRef(null);

  // Derived — must be computed before effects that reference them
  const activeMsgs  = view.chatId ? (chatCache[view.chatId]?.messages || []) : [];
  const lastContent = activeMsgs[activeMsgs.length - 1]?.content;

  useEffect(() => {
    if (!taRef.current) return;
    taRef.current.style.height = '0';
    taRef.current.style.height = Math.min(200, taRef.current.scrollHeight) + 'px';
  }, [input]);

  useEffect(() => { setSlashIndex(0); setSlashDismissed(false); }, [input]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activeMsgs.length, lastContent]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    loadProjects(); loadGlobalChats(); loadFirstStudies();
    apiFetch('/settings').then(r => r.json()).then(d => setAnthropicKeySet(d.anthropic_key_set)).catch(() => {});
  }, []);

  useEffect(() => {
    setShowArchivedProj(false);
    setArchivedProjChats(null);
    if (!openProjId) { setOpenProject(null); return; }
    fetchProjectDetail(openProjId);
  }, [openProjId]);

  // ─── loaders ─────────────────────────────────────────────────────────────────
  const loadProjects = async () => {
    setProjLoading(true);
    try {
      const res = await apiFetch(`/projects`);
      if (res.ok) { const d = await res.json(); setProjects(d.projects || []); }
    } finally { setProjLoading(false); }
  };

  const fetchProjectDetail = async (pid) => {
    setProjDetailLoading(true);
    try {
      const res = await apiFetch(`/projects/${pid}`);
      if (res.ok) setOpenProject(await res.json());
      apiPost(`/projects/${pid}/preload`, {}).catch(() => {});
    } finally {
      setProjDetailLoading(false);
    }
  };

  const loadGlobalChats = async () => {
    const res = await apiFetch(`/global-chats`);
    if (res.ok) { const d = await res.json(); setGlobalChats(d.chats || []); }
  };

  const loadFirstStudies = async () => {
    const res = await apiFetch('/studies/first?limit=20');
    if (res.ok) { const d = await res.json(); setFirstStudies(d.results || []); }
  };

  // Shared project-chat vs global-chat URL prefix, used for hydration, stream,
  // and pin/unpin requests alike. Returns null for a project-chat view with no
  // projId (guards against firing a request to a malformed URL).
  const chatScopeUrl = (view, chatId) =>
    view.type === 'project-chat' && view.projId ? `/projects/${view.projId}/chats/${chatId}`
    : view.type === 'global-chat' ? `/global-chats/${chatId}`
    : null;

  const hydrateChatCache = async (view, chatId) => {
    if (chatCache[chatId]) return;
    const res = await apiFetch(chatScopeUrl(view, chatId));
    if (!res.ok) {
      // A 401 already routed to the sign-in screen via apiFetch; anything else
      // must say so rather than render as a chat with no messages.
      if (res.status !== 401) setCompErr(`Couldn't load this chat (${res.status})`);
      return;
    }
    const d = await res.json();
    const messages = (d.messages || []).map(m => ({
      ...m,
      ...(m.ui_payload && { ui: m.ui_payload }),
      segments: null,
    }));
    setChatCache(prev => ({
      ...prev,
      [chatId]: {
        messages,
        title: d.title,
        pinnedStudyMeta: d.pinned_study_meta || [],
        totalStudiesInProject: d.total_studies_in_project,
      },
    }));
  };

  // ─── project actions ──────────────────────────────────────────────────────────
  const createProject = async () => {
    const name = newProjName.trim() || 'Untitled';
    const res  = await apiPost('/projects', { name });
    if (!res.ok) return;
    const proj = await res.json();
    setNewProjName(''); setShowNewProj(false);
    await loadProjects();
    setOpenProjId(proj.project_id);
    setProjInnerTab('chats');
  };

  const deleteProject = async (pid) => {
    if (!confirm('Delete this project and all its chats?')) return;
    await apiDel(`/projects/${pid}`);
    if (openProjId === pid) { setOpenProjId(null); setOpenProject(null); }
    if (view.projId === pid) setView({ type: 'browse' });
    loadProjects();
  };

  const addStudyToProject = async (study) => {
    if (!openProjId) return;
    setAddStudyErr('');
    const res = await apiPost(`/projects/${openProjId}/studies`, { study });
    if (res.ok) { const updated = await res.json(); setOpenProject(updated); }
    else { const body = await res.json().catch(() => ({})); setAddStudyErr(body.error || 'Could not add study'); }
  };

  const removeStudy = async (studyId) => {
    if (!openProjId) return;
    const res = await apiDel(`/projects/${openProjId}/studies/${studyId}`);
    if (res.ok) { const updated = await res.json(); setOpenProject(updated); }
  };

  // ─── chat navigation ──────────────────────────────────────────────────────────
  const openChat = async (view) => {
    setView(view);
    setCompErr('');
    if (!chatCache[view.chatId]) {
      setChatLoading(true);
      try { await hydrateChatCache(view, view.chatId); }
      finally { setChatLoading(false); }
    }
  };

  const openProjChat = (projId, chatId) => openChat({ type: 'project-chat', projId, chatId });
  const openGlobChat = (chatId) => openChat({ type: 'global-chat', chatId });

  // Evict a chat's cache entry — shared by both delete handlers below.
  const dropChat = (chatId) =>
    setChatCache(prev => { const n = { ...prev }; delete n[chatId]; return n; });

  const newProjChat = async (projId) => {
    const chat = await createProjChatAndSeed(projId, 'New chat').catch(() => null);
    if (!chat) return;
    // sortChatsForDisplay keeps pinned chats above the new (unpinned) one
    setOpenProject(prev => prev ? { ...prev, chats: sortChatsForDisplay([{ ...chat, messages: [] }, ...(prev.chats || [])]) } : prev);
    setView({ type: 'project-chat', projId, chatId: chat.chat_id });
    setCompErr('');
  };

  const deleteProjChat = async (projId, chatId) => {
    await apiDel(`/projects/${projId}/chats/${chatId}`);
    dropChat(chatId);
    if (view.chatId === chatId) setView({ type: 'project-chat', projId, chatId: null });
    setOpenProject(prev => prev ? { ...prev, chats: (prev.chats || []).filter(c => c.chat_id !== chatId) } : prev);
  };

  const newGlobChat = async () => {
    const chat = await createGlobalChatAndSeed('New chat').catch(() => null);
    if (!chat) return;
    setView({ type: 'global-chat', chatId: chat.chat_id });
    setCompErr('');
  };

  const deleteGlobChat = async (chatId) => {
    await apiDel(`/global-chats/${chatId}`);
    dropChat(chatId);
    if (view.chatId === chatId) setView({ type: 'global-chat', chatId: null });
    setGlobalChats(prev => prev.filter(c => c.chat_id !== chatId));
  };

  // ─── pin / archive / move (sidebar "..." menu) ────────────────────────────────
  // Pinned chats float to the top; mirrors the backend's ORDER BY without
  // needing exact timestamp comparison client-side (Array.sort is stable, so
  // ties keep their existing relative order).
  const sortChatsForDisplay = (chats) =>
    [...chats].sort((a, b) => (b.is_pinned ? 1 : 0) - (a.is_pinned ? 1 : 0));

  const patchProjChatFlags = (projId, chatId, patch) =>
    apiJson(`/projects/${projId}/chats/${chatId}`, { method: 'PATCH', body: JSON.stringify(patch) });
  const patchGlobChatFlags = (chatId, patch) =>
    apiJson(`/global-chats/${chatId}`, { method: 'PATCH', body: JSON.stringify(patch) });

  const setProjChatPinned = async (projId, chatId, pinned) => {
    try {
      const d = await patchProjChatFlags(projId, chatId, { pinned });
      setOpenProject(prev => prev && {
        ...prev,
        chats: sortChatsForDisplay((prev.chats || []).map(c =>
          c.chat_id === chatId ? { ...c, is_pinned: d.is_pinned } : c)),
      });
    } catch (e) { setCompErr(e.message || 'Could not update chat'); }
  };

  const setGlobChatPinned = async (chatId, pinned) => {
    try {
      const d = await patchGlobChatFlags(chatId, { pinned });
      setGlobalChats(prev => sortChatsForDisplay(prev.map(c =>
        c.chat_id === chatId ? { ...c, is_pinned: d.is_pinned } : c)));
    } catch (e) { setCompErr(e.message || 'Could not update chat'); }
  };

  // Archiving always drops the chat out of the current (default, non-archived)
  // list view — the "Show archived" view (separate state) is what un-archive
  // acts on instead.
  const setProjChatArchived = async (projId, chatId, archived) => {
    try {
      await patchProjChatFlags(projId, chatId, { archived });
      setOpenProject(prev => prev && { ...prev, chats: (prev.chats || []).filter(c => c.chat_id !== chatId) });
    } catch (e) { setCompErr(e.message || 'Could not update chat'); }
  };

  const setGlobChatArchived = async (chatId, archived) => {
    try {
      await patchGlobChatFlags(chatId, { archived });
      setGlobalChats(prev => prev.filter(c => c.chat_id !== chatId));
    } catch (e) { setCompErr(e.message || 'Could not update chat'); }
  };

  // Moving/removing always evicts the chatCache entry too — its messages
  // belong to a different scope now, same as delete.
  const moveProjChatToProject = async (fromProjId, chatId, targetProjectId) => {
    try {
      await apiJson(`/projects/${fromProjId}/chats/${chatId}/move-to-project`,
        { method: 'POST', body: JSON.stringify({ project_id: targetProjectId }) });
      dropChat(chatId);
      setOpenProject(prev => prev && { ...prev, chats: (prev.chats || []).filter(c => c.chat_id !== chatId) });
      if (view.chatId === chatId) setView({ type: 'project-chat', projId: fromProjId, chatId: null });
    } catch (e) { setCompErr(e.message || 'Could not move chat'); }
  };

  const moveGlobalChatToProject = async (chatId, targetProjectId) => {
    try {
      await apiJson(`/global-chats/${chatId}/move-to-project`,
        { method: 'POST', body: JSON.stringify({ project_id: targetProjectId }) });
      dropChat(chatId);
      setGlobalChats(prev => prev.filter(c => c.chat_id !== chatId));
      if (view.chatId === chatId) setView({ type: 'global-chat', chatId: null });
    } catch (e) { setCompErr(e.message || 'Could not move chat'); }
  };

  const removeChatFromProject = async (projId, chatId) => {
    try {
      await apiJson(`/projects/${projId}/chats/${chatId}/remove-from-project`,
        { method: 'POST', body: '{}' });
      dropChat(chatId);
      setOpenProject(prev => prev && { ...prev, chats: (prev.chats || []).filter(c => c.chat_id !== chatId) });
      if (view.chatId === chatId) setView({ type: 'project-chat', projId, chatId: null });
    } catch (e) { setCompErr(e.message || 'Could not remove chat from project'); }
  };

  // "+ New project" inside the Move-to-project submenu — create, then
  // immediately move the chat into it.
  const createProjectAndMoveChat = async (name, fromProjId, chatId) => {
    const res = await apiPost('/projects', { name: (name || '').trim() || 'Untitled' });
    if (!res.ok) { setCompErr('Failed to create project'); return; }
    const proj = await res.json();
    await loadProjects();
    if (fromProjId) await moveProjChatToProject(fromProjId, chatId, proj.project_id);
    else await moveGlobalChatToProject(chatId, proj.project_id);
  };

  // ─── "Show archived" toggle ───────────────────────────────────────────────────
  // include_archived=1 returns EVERY chat (archived and not) — filtering to
  // just the archived ones client-side avoids a second backend query mode.
  const toggleShowArchivedProj = async (projId) => {
    if (!showArchivedProj && archivedProjChats === null) {
      const res = await apiFetch(`/projects/${projId}?include_archived=1`);
      if (res.ok) {
        const d = await res.json();
        setArchivedProjChats((d.chats || []).filter(c => c.is_archived));
      }
    }
    setShowArchivedProj(v => !v);
  };

  const toggleShowArchivedGlobal = async () => {
    if (!showArchivedGlobal && archivedGlobalChats === null) {
      const res = await apiFetch('/global-chats?include_archived=1');
      if (res.ok) {
        const d = await res.json();
        setArchivedGlobalChats((d.chats || []).filter(c => c.is_archived));
      }
    }
    setShowArchivedGlobal(v => !v);
  };

  // Unarchive moves the chat back into the default list's local state
  // (rather than just vanishing from the archived list) — global chats in
  // particular only ever load once, so without this the chat would stay
  // invisible in both lists until a full page reload.
  const unarchiveProjChat = async (projId, chatId) => {
    try {
      const d = await patchProjChatFlags(projId, chatId, { archived: false });
      const restored = (archivedProjChats || []).find(c => c.chat_id === chatId);
      setArchivedProjChats(prev => (prev || []).filter(c => c.chat_id !== chatId));
      if (restored) {
        setOpenProject(prev => prev && {
          ...prev,
          chats: sortChatsForDisplay([...(prev.chats || []), { ...restored, is_archived: d.is_archived, archived_at: null }]),
        });
      }
    } catch (e) { setCompErr(e.message || 'Could not update chat'); }
  };

  const unarchiveGlobalChat = async (chatId) => {
    try {
      const d = await patchGlobChatFlags(chatId, { archived: false });
      const restored = (archivedGlobalChats || []).find(c => c.chat_id === chatId);
      setArchivedGlobalChats(prev => (prev || []).filter(c => c.chat_id !== chatId));
      if (restored) {
        setGlobalChats(prev => sortChatsForDisplay([...prev, { ...restored, is_archived: d.is_archived, archived_at: null }]));
      }
    } catch (e) { setCompErr(e.message || 'Could not update chat'); }
  };

  // Shared rename primitive — PATCHes the chat, updates chatCache, then
  // hands the new title to `patchList` to mirror it into whichever sidebar
  // list (project chats or global chats) holds this chat's row.
  const renameChatCore = async (url, chatId, newTitle, patchList) => {
    const t = (newTitle || '').trim();
    if (!t) return;
    try {
      const d = await apiJson(url, { method: 'PATCH', body: JSON.stringify({ title: t }) });
      const title = d.title;
      setChatCache(prev => {
        const cur = prev[chatId];
        return cur ? { ...prev, [chatId]: { ...cur, title } } : prev;
      });
      patchList(title);
    } catch (e) {
      setCompErr(e.message || 'Could not rename chat');
    }
  };

  // Explicit-chatId renamers — used by sidebar rows, which may not be the
  // currently open chat (unlike the top-bar ChatTitleEditor below).
  const renameProjChat = (projId, chatId, newTitle) =>
    renameChatCore(`/projects/${projId}/chats/${chatId}`, chatId, newTitle, title =>
      setOpenProject(prev => prev && {
        ...prev,
        chats: (prev.chats || []).map(c => c.chat_id === chatId ? { ...c, title } : c),
      }));

  const renameGlobChat = (chatId, newTitle) =>
    renameChatCore(`/global-chats/${chatId}`, chatId, newTitle, title =>
      setGlobalChats(prev => prev.map(c => c.chat_id === chatId ? { ...c, title } : c)));

  // Top-bar rename (ChatTitleEditor) — always targets the currently open chat.
  const renameChat = (newTitle) => {
    const chatId = view.chatId;
    if (!chatId) return;
    if (view.type === 'global-chat') return renameGlobChat(chatId, newTitle);
    if (view.type === 'project-chat' && view.projId) return renameProjChat(view.projId, chatId, newTitle);
  };

  // ─── streaming helpers ────────────────────────────────────────────────────────
  const patchLast = (chatId, fn) =>
    setChatCache(prev => {
      const c = prev[chatId];
      if (!c) return prev;
      const msgs = [...c.messages];
      msgs[msgs.length - 1] = fn(msgs[msgs.length - 1]);
      return { ...prev, [chatId]: { ...c, messages: msgs } };
    });

  // Patch a whole chatCache entry (not just its last message). Bails if the
  // chat isn't cached; `fn(cur)` may return null/the same object to skip the
  // update (e.g. a no-op like "already pinned").
  const patchChat = (chatId, fn) =>
    setChatCache(prev => {
      const cur = prev[chatId];
      if (!cur) return prev;
      const patch = fn(cur);
      return !patch || patch === cur ? prev : { ...prev, [chatId]: { ...cur, ...patch } };
    });

  const optimisticAppend = (chatId, userMsg) =>
    setChatCache(prev => {
      const c = prev[chatId] || { messages: [], title: truncateTitle(userMsg) };
      return {
        ...prev,
        [chatId]: {
          ...c,
          messages: [
            ...c.messages,
            { role: 'user',      content: userMsg },
            { role: 'assistant', content: '', isStreaming: true, steps: [], pendingStep: null, segments: null },
          ],
        },
      };
    });

  // Shared by normal completion (applyStreamDone) and an early Stop: ends
  // the message's streaming state while keeping whatever content/segments
  // already arrived, rather than requiring a `done` event.
  const _finalizeStreamedMessage = (m) => {
    const next = { ...m, isStreaming: false, pendingStep: null };
    if (m.segments !== null) {
      const frozen = (m.segments || []).map(s => s.type === 'text' ? { ...s, done: true } : s);
      next.segments = frozen;
      next.ui = { kind: 'agent_segments', segments: frozen };
    }
    return next;
  };

  // The server is the source of truth for the title: `done` always carries
  // the chat's current stored value — the provisional first-60-chars title,
  // an LLM-generated replacement, or a rename that landed mid-turn — so
  // applying it here can never clobber a rename.
  const applyStreamDone = (chatId, payload) => {
    patchLast(chatId, _finalizeStreamedMessage);
    setChatCache(prev => {
      const cur = prev[chatId] || {};
      const nextMeta = payload?.pinned_study_meta != null ? payload.pinned_study_meta : (cur.pinnedStudyMeta || []);
      const nextTitle = payload?.title ?? cur.title;
      return { ...prev, [chatId]: { ...cur, title: nextTitle, pinnedStudyMeta: nextMeta } };
    });
  };

  // Shared fetch+guard+parseSSE for a chat stream. `handlers.onToken`/`onDone`
  // (and any agent-only extras) are supplied per call site;
  // the step/ui/error handlers are identical across project and global chat.
  const streamChat = async (url, body, chatId, signal, handlers) => {
    const res = await apiFetch(url, { method: 'POST', body: JSON.stringify(body), signal });
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Stream failed');
    }
    await parseSSE(res, {
      onUi:        (payload) => patchLast(chatId, m => ({ ...m, ui: payload, content: '' })),
      onStepStart: ({ name, label }) => patchLast(chatId, m => ({ ...m, pendingStep: { name, label } })),
      onStepDone:  ({ name, label, detail }) => patchLast(chatId, m => ({
        ...m, pendingStep: null, steps: [...(m.steps || []), { name, label, detail }],
      })),
      onError: ({ error }) => {
        setCompErr(error || 'Error');
        patchLast(chatId, m => ({
          ...m,
          isStreaming: false,
          pendingStep: null,
          content: m.content || `⚠️ ${error || 'Something went wrong.'}`,
        }));
      },
      ...handlers,
    }, signal);
  };

  // ─── agent/segments helpers ──────────────────────────────────────────────────
  const onAgentStart = (chatId) => () =>
    patchLast(chatId, m => ({ ...m, segments: [] }));

  const onSegmentToolCall = (chatId) => ({ name, label, args }) =>
    patchLast(chatId, m => {
      const segs = [...(m.segments || [])];
      if (segs.length && segs[segs.length - 1].type === 'text')
        segs[segs.length - 1] = { ...segs[segs.length - 1], done: true };
      segs.push({ type: 'tool', name, label, args, done: false, result: null });
      return { ...m, segments: segs };
    });

  const onSegmentToolResult = (chatId) => ({ name, label, detail, ui_payload }) =>
    patchLast(chatId, m => {
      const segs = (m.segments || []).map(s =>
        s.type === 'tool' && s.name === name && !s.done
          ? { ...s, done: true, result: { label, detail, ui_payload } }
          : s
      );
      return { ...m, segments: segs };
    });

  const onTokenAgent = (chatId) => ({ token }) =>
    patchLast(chatId, m => {
      if (m.segments === null) return { ...m, content: (m.content || '') + (token || '') };
      const segs = [...(m.segments || [])];
      const last = segs[segs.length - 1];
      if (last && last.type === 'text' && !last.done)
        segs[segs.length - 1] = { ...last, content: (last.content || '') + token };
      else
        segs.push({ type: 'text', content: token, done: false });
      return { ...m, segments: segs };
    });

  // The server is the source of truth for pins: `pinnedStudyMeta` is the whole
  // list, and ids are derived where needed. Each mutation patches optimistically
  // for responsiveness, then adopts the server's list or rolls back and says why
  // — a pin that only ever existed in local state looks identical to a persisted
  // one until the next reload.
  const applyPinResult = (chatId, data) =>
    patchChat(chatId, () => ({ pinnedStudyMeta: data.pinned_study_meta || [] }));

  const mutatePin = async (chatId, optimistic, request, failMsg) => {
    const before = chatCache[chatId]?.pinnedStudyMeta;
    patchChat(chatId, optimistic);
    const base = chatScopeUrl(view, chatId);
    if (!base) return;
    try {
      applyPinResult(chatId, await request(base));
    } catch (e) {
      patchChat(chatId, () => ({ pinnedStudyMeta: before || [] }));
      setCompErr(e.message || failMsg);
    }
  };

  const unpinStudy = (chatId, studyId) => mutatePin(
    chatId,
    cur => ({ pinnedStudyMeta: (cur.pinnedStudyMeta || []).filter(p => p.study_id !== studyId) }),
    base => apiJson(`${base}/pinned/${studyId}`, { method: 'DELETE' }),
    'Could not unpin that study',
  );

  const pinStudy = (chatId, study) => mutatePin(
    chatId,
    cur => (cur.pinnedStudyMeta || []).some(p => p.study_id === study.study_id)
      ? null
      : { pinnedStudyMeta: [...(cur.pinnedStudyMeta || []),
                            { study_id: study.study_id, study_title: study.study_title }] },
    base => apiJson(`${base}/pinned/${study.study_id}`, { method: 'POST', body: '{}' }),
    'Could not pin that study',
  );

  // Browse "+ Pin" stages studies before any chat exists; once the chat is
  // created they become real pins. Sequential on purpose — the 10-pin cap is
  // enforced per request, so parallel POSTs would race it.
  const pinStagedStudies = async (chatId, studies) => {
    const rejected = [];
    let last = null;
    for (const s of studies) {
      try {
        last = await apiJson(`/global-chats/${chatId}/pinned/${s.study_id}`,
                             { method: 'POST', body: '{}' });
      } catch (e) {
        rejected.push(e.message || `Study ${s.study_id} could not be pinned`);
      }
    }
    if (last) applyPinResult(chatId, last);
    if (rejected.length) {
      setCompErr(rejected.length === 1 ? rejected[0]
        : `${rejected.length} of ${studies.length} studies could not be pinned. ${rejected[0]}`);
    }
  };

  const completeSlash = (item) => {
    // Commands that don't need extra args should run immediately — inserting
    // `/systems` and waiting for a second Enter is what made the prompt look broken.
    if (item.cmd === '/systems' || item.cmd === '/model') {
      setInput('');
      sendMessage(item.cmd);
      return;
    }
    setInput(item.insert);
    setTimeout(() => taRef.current?.focus(), 0);
  };

  // Create a chat and seed its chatCache entry — used by sendMessage whenever
  // the active view has no chatId yet (first message in a fresh chat).
  const createProjChatAndSeed = async (projId, title) => {
    const res = await apiPost(`/projects/${projId}/chats`, {});
    if (!res.ok) throw new Error('Failed to create chat');
    const chat = await res.json();
    setChatCache(prev => ({
      ...prev,
      [chat.chat_id]: {
        messages: [],
        title,
        pinnedStudyMeta: chat.pinned_study_meta || [],
        totalStudiesInProject: chat.total_studies_in_project,
      },
    }));
    return chat;
  };

  const createGlobalChatAndSeed = async (title) => {
    const res = await apiPost('/global-chats', {});
    if (!res.ok) throw new Error('Failed to create chat');
    const chat = await res.json();
    setChatCache(prev => ({
      ...prev,
      [chat.chat_id]: {
        messages: [],
        title,
        pinnedStudyMeta: chat.pinned_study_meta || [],
      },
    }));
    setGlobalChats(prev => sortChatsForDisplay([chat, ...prev]));
    return chat;
  };

  // Resolve workView's chatId, creating+seeding a chat first if it has none yet.
  const ensureChatId = async (workView, title) => {
    if (workView.chatId) return workView.chatId;
    const chat = workView.type === 'project-chat'
      ? await createProjChatAndSeed(workView.projId, title)
      : await createGlobalChatAndSeed(title);
    setView(v => ({ ...v, chatId: chat.chat_id }));
    return chat.chat_id;
  };

  // ─── send ─────────────────────────────────────────────────────────────────────
  const sendMessage = async (msgOverride) => {
    const msg = (typeof msgOverride === 'string' ? msgOverride : input).trim();
    if (!msg || sending) return;
    setSending(true); setCompErr('');
    if (typeof msgOverride !== 'string') setInput('');

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    if (/^\/model\s*$/i.test(msg)) {
      setSending(false);
      setShowModelPicker(true);
      return;
    }

    const reportMatch   = /^\/report\s+(\d+)\s*$/i.exec(msg);
    const reportStudyId = reportMatch ? parseInt(reportMatch[1], 10) : null;
    const pinMatch      = /^\/pin\s+([\d\s]+?)\s*$/i.exec(msg);
    const pinStudyIds   = pinMatch ? pinMatch[1].trim().split(/\s+/).map(Number).filter(n => Number.isInteger(n) && !isNaN(n)) : null;
    const deepMatch = /^\/deepsearch\s+(.+)/is.exec(msg);
    const sendMsg   = deepMatch ? deepMatch[1].trim() : msg;
    const displayMsg    = reportStudyId != null ? `/report ${reportStudyId} - Full study report`
                        : pinStudyIds   != null ? `/pin ${pinStudyIds.join(' ')} - Pinning studies`
                        : msg;

    // Visible in the catch block so a Stop (AbortError) can finalize the
    // in-flight message for whichever chat this send was targeting.
    let chatId = null;

    try {
      // ── Normalize browse → new global chat ──────────────────────────────────
      let workView = view;

      if (view.type === 'browse') {
        const staged = [...ctxStudies];
        const chat   = await createGlobalChatAndSeed(truncateTitle(displayMsg));
        workView     = { type: 'global-chat', chatId: chat.chat_id };
        setView(workView);
        setCtxStudies([]);
        if (staged.length) await pinStagedStudies(chat.chat_id, staged);
      }

      // ── /systems ────────────────────────────────────────────────────────────
      if (/^\/systems\b/i.test(msg)) {
        chatId = (workView.type === 'project-chat' || workView.type === 'global-chat')
          ? await ensureChatId(workView, '/systems')
          : null;
        if (!chatId) return;
        optimisticAppend(chatId, '/systems — Model status');
        patchLast(chatId, m => ({ ...m, pendingStep: { name: 'probe', label: 'Probing all models…' } }));
        const res = await apiFetch('/systems');
        if (!res.ok) throw new Error('Systems check failed');
        const models = await res.json();
        patchLast(chatId, m => ({ ...m, ui: { kind: 'systems_status', models }, pendingStep: null, isStreaming: false }));
        return;
      }

      // ── /report + regular messages ──────────────────────────────────────────
      if (workView.type === 'project-chat') {
        chatId = await ensureChatId(workView, truncateTitle(displayMsg));
        optimisticAppend(chatId, displayMsg);
        await streamChat(
          `${chatScopeUrl(workView, chatId)}/message/stream`,
          {
            message: msg,
            model: selectedModel,
            ...(reportStudyId != null && { report_study_id: reportStudyId }),
            ...(pinStudyIds   != null && { pin_study_ids: pinStudyIds }),
          },
          chatId, ctrl.signal,
          {
            onToken:             onTokenAgent(chatId),
            onAgentStart:        onAgentStart(chatId),
            onSegmentToolCall:   onSegmentToolCall(chatId),
            onSegmentToolResult: onSegmentToolResult(chatId),
            onDone: (payload) => {
              applyStreamDone(chatId, payload);
              if (payload?.title) setOpenProject(prev => prev ? {
                ...prev,
                chats: (prev.chats || []).map(c =>
                  c.chat_id === chatId ? { ...c, title: payload.title } : c),
              } : prev);
            },
          },
        );

      } else if (workView.type === 'global-chat') {
        chatId = await ensureChatId(workView, truncateTitle(displayMsg));
        optimisticAppend(chatId, displayMsg);
        await streamChat(
          `${chatScopeUrl(workView, chatId)}/message/stream`,
          {
            message: sendMsg,
            model: selectedModel,
            ...(reportStudyId != null && { report_study_id: reportStudyId }),
            ...(pinStudyIds   != null && { pin_study_ids: pinStudyIds }),
            deep_search: true,
          },
          chatId, ctrl.signal,
          {
            onToken:             onTokenAgent(chatId),
            onAgentStart:        onAgentStart(chatId),
            onSegmentToolCall:   onSegmentToolCall(chatId),
            onSegmentToolResult: onSegmentToolResult(chatId),
            onDone: (payload) => {
              applyStreamDone(chatId, payload);
              if (payload?.title) setGlobalChats(prev => prev.map(c =>
                c.chat_id === chatId ? { ...c, title: payload.title } : c));
            },
          },
        );
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        // User clicked Stop: end the message's streaming state but keep
        // whatever content/segments already arrived (no `done` event fires).
        if (chatId) patchLast(chatId, _finalizeStreamedMessage);
      } else {
        setCompErr(e.message || 'Failed to send');
      }
    } finally {
      setSending(false);
    }
  };

  const stopGenerating = () => abortRef.current?.abort();

  // ─── modal ────────────────────────────────────────────────────────────────────
  const openStudyModal = async (study) => {
    modalAbortRef.current?.abort();
    const ctrl = new AbortController();
    modalAbortRef.current = ctrl;
    setModalStudy(study); setModalDetail(null); setModalDetailLoading(true);
    // Some callers (e.g. InlineStudyCard, fed by the agent's search_studies
    // tool) pass a trimmed study object with no study_abstract/PI fields at
    // all — fetch the full header alongside detail in that case, same as
    // openStudyById does for a bare-id restore.
    const needsHeader = !('study_abstract' in study);
    try {
      const [header, d] = await Promise.all([
        needsHeader ? apiFetch(`/studies/${study.study_id}`).then(r => r.ok ? r.json() : null) : null,
        fetchStudyDetail(study.study_id),
      ]);
      if (!ctrl.signal.aborted) {
        if (header) setModalStudy(prev => ({ ...prev, ...header }));
        setModalDetail(d);
      }
    } catch (_) {}
    finally {
      if (!ctrl.signal.aborted) setModalDetailLoading(false);
    }
  };

  const closeModal = () => {
    modalAbortRef.current?.abort();
    setModalStudy(null); setModalDetail(null);
  };

  // URL-driven modal restore, where only a bare id is available — not even a
  // title to show while loading (openStudyModal's callers at least have
  // that, even when thin). Kept separate so the common click path doesn't
  // pay for a header round trip when its study object already has one.
  const openStudyById = async (studyId) => {
    modalAbortRef.current?.abort();
    const ctrl = new AbortController();
    modalAbortRef.current = ctrl;
    setModalStudy({ study_id: studyId }); setModalDetail(null); setModalDetailLoading(true);
    try {
      const [study, detail] = await Promise.all([
        apiFetch(`/studies/${studyId}`).then(r => r.ok ? r.json() : { study_id: studyId }),
        fetchStudyDetail(studyId),
      ]);
      if (!ctrl.signal.aborted) { setModalStudy(study); setModalDetail(detail); }
    } catch (_) {}
    finally {
      if (!ctrl.signal.aborted) setModalDetailLoading(false);
    }
  };

  // ─── misc ─────────────────────────────────────────────────────────────────────
  const enrichAllStudies = async (projId) => {
    const res = await apiPost(`/projects/${projId}/studies/enrich-all`, {});
    if (res.ok) { const d = await res.json(); if (d.project) setOpenProject(d.project); }
  };

  const doSearch = async (override) => {
    const q = (override ?? query).trim();
    if (!q) return;
    if (override) setQuery(override);
    setSearching(true); setSearched(false);
    const res = await apiPost('/search', { query: q, deep_search: true });
    if (res.ok) {
      const d = await res.json();
      setResults(d.results || []);
      setSqlQuery(d.sql_query || null);
      setAppliedFilters(d.applied_filters || null);
    }
    else setResults([]);
    setSearched(true); setSearching(false);
  };

  // ─── derived ──────────────────────────────────────────────────────────────────
  const projStudyIds   = useMemo(() => (openProject?.studies || []).map(s => s.study_id), [openProject]);
  const ctxStudyIds    = useMemo(() => ctxStudies.map(s => s.study_id), [ctxStudies]);
  const displayStudies = searched ? results : firstStudies;
  const isChat         = view.type === 'project-chat' || view.type === 'global-chat';
  const canSend        = (isChat || view.type === 'browse') && input.trim().length > 0 && !sending;
  const slashMatches   = /^\/\S*$/.test(input)
    ? SLASH_COMMANDS.filter(c => c.cmd.startsWith(input.toLowerCase()))
    : [];

  const topTitle = useMemo(() => {
    if (view.type === 'project-chat') {
      const proj = projects.find(p => p.project_id === view.projId);
      return chatCache[view.chatId]?.title || proj?.name || 'Project Chat';
    }
    if (view.type === 'global-chat') return chatCache[view.chatId]?.title || 'Global Chat';
    return 'Browse Studies';
  }, [view, chatCache, projects]);

  // Placed last so it postdates every const (openChat, openStudyById,
  // closeModal) it closes over, regardless of their declaration order above.
  useUrlSync({ view, setView, setOpenProjId, openChat, modalStudy, openStudyById, closeModal });

  return {
    // state setters needed in render
    setView, setOpenProjId, setProjInnerTab, setShowNewProj, setNewProjName,
    setQuery, setResults, setSearched, setSqlQuery, setAppliedFilters, setShowSql,
    setCtxStudies, setInput, setSelectedModel, setTheme,
    setSlashIndex, setSlashDismissed,
    setMergeWorkspaceId, setPendingMergeStudy,
    setShowModelPicker, setShowPlusMenu, setAnthropicKeySet, setSidebarCollapsed,
    setEditingChatId, setEditChatVal,
    openSearchResultsPanel, closeSearchResultsPanel, finishCloseSearchResultsPanel, openMergePanel,
    // state values
    projects, projLoading, openProjId, openProject, view,
    chatCache, globalChats, projInnerTab,
    query, results, searching, searched, sqlQuery, appliedFilters, showSql,
    ctxStudies, showNewProj, newProjName, mergeWorkspaceId, showMergePanel, pendingMergeStudy, sidebarCollapsed,
    editingChatId, editChatVal,
    showArchivedProj, archivedProjChats, showArchivedGlobal, archivedGlobalChats,
    searchResultsPanel, searchResultsClosing,
    input, sending, compErr, addStudyErr, selectedModel, theme,
    slashIndex, slashDismissed, showModelPicker, showPlusMenu, anthropicKeySet,
    modalStudy, modalDetail, modalDetailLoading,
    projDetailLoading, chatLoading,
    // refs
    taRef, bottomRef,
    // handlers
    createProject, deleteProject, addStudyToProject, removeStudy,
    openProjChat, openGlobChat, newProjChat, deleteProjChat, newGlobChat, deleteGlobChat,
    unpinStudy, pinStudy, sendMessage, stopGenerating, openStudyModal, closeModal, enrichAllStudies, doSearch,
    completeSlash, renameChat, renameProjChat, renameGlobChat,
    setProjChatPinned, setGlobChatPinned, setProjChatArchived, setGlobChatArchived,
    moveProjChatToProject, moveGlobalChatToProject, removeChatFromProject, createProjectAndMoveChat,
    toggleShowArchivedProj, toggleShowArchivedGlobal, unarchiveProjChat, unarchiveGlobalChat,
    // derived
    projStudyIds, ctxStudyIds, displayStudies, isChat, canSend, topTitle, scrollCollapse,
    activeMsgs, slashMatches,
  };
}
