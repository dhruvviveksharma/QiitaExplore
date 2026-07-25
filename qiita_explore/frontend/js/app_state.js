function useAppState() {
  const [projects,    setProjects]    = useState([]);
  const [projLoading, setProjLoading] = useState(true);
  const [openProjId,  setOpenProjId]  = useState(null);
  const [openProject, setOpenProject] = useState(null);
  const [view,        setView]        = useState({ type: 'browse' });
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
  const [showSql,      setShowSql]      = useState(false);
  const [deepSearch,   setDeepSearch]   = useState(false);
  const [ctxStudies,   setCtxStudies]   = useState([]);
  const [showNewProj,  setShowNewProj]  = useState(false);
  const [newProjName,  setNewProjName]  = useState('');
  const [mergeWorkspaceId, setMergeWorkspaceId] = useState(null);
  const [showMergePanel,   setShowMergePanel]   = useState(false);
  const [pendingMergeStudy, setPendingMergeStudy] = useState(null);
  const [input,   setInput]   = useState('');
  const [sending, setSending] = useState(false);
  const [compErr,        setCompErr]        = useState('');
  const [slashIndex,     setSlashIndex]     = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const { selectedModel, setSelectedModel, showModelPicker, setShowModelPicker } = useModelSelection(view.chatId);
  const [showPlusMenu,    setShowPlusMenu]    = useState(false);
  const [anthropicKeySet, setAnthropicKeySet] = useState(false);
  const [theme, setThemeState] = useState(() => {
    try { return localStorage.getItem('ui:theme') || 'light'; } catch (_) { return 'light'; }
  });
  const setTheme = (t) => {
    setThemeState(t);
    try { localStorage.setItem('ui:theme', t); } catch (_) {}
  };
  const [modalStudy,         setModalStudy]         = useState(null);
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
    if (res.ok) {
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
          pinnedStudies: d.pinned_studies || [],
          totalStudiesInProject: d.total_studies_in_project,
        },
      }));
    }
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
    const res = await apiPost(`/projects/${openProjId}/studies`, { study });
    if (res.ok) { const updated = await res.json(); setOpenProject(updated); }
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
    setOpenProject(prev => prev ? { ...prev, chats: [{ ...chat, messages: [] }, ...(prev.chats || [])] } : prev);
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
      const c = prev[chatId] || { messages: [], title: userMsg.slice(0, 60) };
      return {
        ...prev,
        [chatId]: {
          ...c,
          messages: [
            ...c.messages,
            { role: 'user',      content: userMsg },
            { role: 'assistant', content: '', isStreaming: true, steps: [], pendingStep: null, queryPlan: null, segments: null },
          ],
        },
      };
    });

  const applyStreamDone = (chatId, title, pinnedList) => {
    patchLast(chatId, m => {
      const next = { ...m, isStreaming: false, pendingStep: null };
      if (m.segments !== null) {
        const frozen = (m.segments || []).map(s => s.type === 'text' ? { ...s, done: true } : s);
        next.segments = frozen;
        next.ui = { kind: 'agent_segments', segments: frozen };
      }
      return next;
    });
    setChatCache(prev => {
      const cur = prev[chatId] || {};
      const pins = cur.pinnedStudies || [];
      const nextPins = pinnedList != null ? pinnedList : pins;
      return { ...prev, [chatId]: { ...cur, title, pinnedStudies: nextPins } };
    });
  };

  // Shared fetch+guard+parseSSE for a chat stream. `handlers.onToken`/`onDone`
  // (and any agent-only extras like onQueryPlan) are supplied per call site;
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

  const unpinStudy = async (chatId, studyId) => {
    patchChat(chatId, cur => ({ pinnedStudies: (cur.pinnedStudies || []).filter(id => id !== studyId) }));
    const base = chatScopeUrl(view, chatId);
    if (!base) return;
    try {
      await apiDel(`${base}/pinned/${studyId}`);
    } catch (_) {}
  };

  const pinStudy = async (chatId, study) => {
    const studyId = study.study_id;
    patchChat(chatId, cur => (cur.pinnedStudies || []).includes(studyId)
      ? null
      : { pinnedStudies: [...(cur.pinnedStudies || []), studyId] });
    const base = chatScopeUrl(view, chatId);
    if (!base) return;
    try {
      const res = await apiPost(`${base}/pinned/${studyId}`, {});
      if (res?.ok) {
        const data = await res.json();
        if (data.pinned_studies) {
          setChatCache(prev => ({ ...prev, [chatId]: { ...(prev[chatId] || {}), pinnedStudies: data.pinned_studies } }));
        }
      }
    } catch (_) {}
  };

  const removeCtxStudyFromChat = (chatId, studyId) => {
    patchChat(chatId, cur => ({ ctxStudies: (cur.ctxStudies || []).filter(s => s.study_id !== studyId) }));
  };

  const completeSlash = (item) => {
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
        pinnedStudies: chat.pinned_studies || [],
        totalStudiesInProject: chat.total_studies_in_project,
      },
    }));
    return chat;
  };

  const createGlobalChatAndSeed = async (title, extraCacheFields = {}) => {
    const res = await apiPost('/global-chats', {});
    if (!res.ok) throw new Error('Failed to create chat');
    const chat = await res.json();
    setChatCache(prev => ({ ...prev, [chat.chat_id]: { messages: [], title, ...extraCacheFields } }));
    setGlobalChats(prev => [chat, ...prev]);
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
    const deepMatch    = /^\/deepsearch\s+(.+)/is.exec(msg);
    const isDeepSearch = deepSearch || !!deepMatch;
    const sendMsg      = deepMatch ? deepMatch[1].trim() : msg;
    const displayMsg    = reportStudyId != null ? `/report ${reportStudyId} - Full study report`
                        : pinStudyIds   != null ? `/pin ${pinStudyIds.join(' ')} - Pinning studies`
                        : msg;

    try {
      // ── Normalize browse → new global chat ──────────────────────────────────
      let workView   = view;
      let studiesCtx = null; // null means read from cache; set when coming from browse

      if (view.type === 'browse') {
        const snapCtx = [...ctxStudies];
        const chat = await createGlobalChatAndSeed(displayMsg.slice(0, 60), { ctxStudies: snapCtx });
        workView   = { type: 'global-chat', chatId: chat.chat_id };
        setView(workView);
        setCtxStudies([]);
        studiesCtx = snapCtx;
      }

      // ── /systems ────────────────────────────────────────────────────────────
      if (/^\/systems\s*$/i.test(msg)) {
        const chatId = (workView.type === 'project-chat' || workView.type === 'global-chat')
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
        const chatId = await ensureChatId(workView, displayMsg.slice(0, 60));
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
            // onToken (not onTokenAgent) still covers the legacy plain-stream
            // path. onAgentStart/onSegmentToolCall/onSegmentToolResult are the
            // same handlers global chat already uses — project chat can now
            // emit agent_start/segment_* too (PI_BACKEND_PROJECT), and these
            // were previously unwired here because it never could.
            onToken: ({ token }) => patchLast(chatId, m => ({ ...m, content: (m.content||'') + (token||'') })),
            onAgentStart:        onAgentStart(chatId),
            onSegmentToolCall:   onSegmentToolCall(chatId),
            onSegmentToolResult: onSegmentToolResult(chatId),
            onDone: (payload) => {
              const title = displayMsg.slice(0, 60);
              applyStreamDone(chatId, title, payload?.pinned_studies ?? null);
              setOpenProject(prev => prev ? {
                ...prev, chats: (prev.chats||[]).map(c => c.chat_id === chatId ? { ...c, title } : c)
              } : prev);
            },
          },
        );

      } else if (workView.type === 'global-chat') {
        const chatId = await ensureChatId(workView, displayMsg.slice(0, 60));
        optimisticAppend(chatId, displayMsg);
        const ctxToSend = studiesCtx !== null ? studiesCtx : (chatCache[chatId]?.ctxStudies || []);
        await streamChat(
          `${chatScopeUrl(workView, chatId)}/message/stream`,
          {
            message: sendMsg,
            model: selectedModel,
            selected_studies: ctxToSend,
            ...(reportStudyId != null && { report_study_id: reportStudyId }),
            ...(pinStudyIds   != null && { pin_study_ids: pinStudyIds }),
            ...(isDeepSearch            && { deep_search: true }),
          },
          chatId, ctrl.signal,
          {
            onToken:             onTokenAgent(chatId),
            onQueryPlan:         (payload) => patchLast(chatId, m => ({ ...m, queryPlan: payload })),
            onAgentStart:        onAgentStart(chatId),
            onSegmentToolCall:   onSegmentToolCall(chatId),
            onSegmentToolResult: onSegmentToolResult(chatId),
            onDone: (payload) => {
              const title = displayMsg.slice(0, 60);
              applyStreamDone(chatId, title, payload?.pinned_studies ?? null);
              setGlobalChats(prev => prev.map(c => c.chat_id === chatId ? { ...c, title } : c));
            },
          },
        );
      }
    } catch (e) {
      if (e.name !== 'AbortError') setCompErr(e.message || 'Failed to send');
    } finally {
      setSending(false);
    }
  };

  // ─── modal ────────────────────────────────────────────────────────────────────
  const openStudyModal = async (study) => {
    modalAbortRef.current?.abort();
    const ctrl = new AbortController();
    modalAbortRef.current = ctrl;
    setModalStudy(study); setModalDetail(null); setModalDetailLoading(true);
    try {
      const d = await fetchStudyDetail(study.study_id);
      if (!ctrl.signal.aborted) setModalDetail(d);
    } catch (_) {}
    finally {
      if (!ctrl.signal.aborted) setModalDetailLoading(false);
    }
  };

  const closeModal = () => {
    modalAbortRef.current?.abort();
    setModalStudy(null); setModalDetail(null);
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
    const res = await apiPost('/search', { query: q, deep_search: deepSearch });
    if (res.ok) { const d = await res.json(); setResults(d.results || []); setSqlQuery(d.sql_query || null); }
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

  return {
    // state setters needed in render
    setView, setOpenProjId, setProjInnerTab, setShowNewProj, setNewProjName,
    setQuery, setResults, setSearched, setSqlQuery, setShowSql, setDeepSearch,
    setCtxStudies, setInput, setSelectedModel, setTheme,
    setSlashIndex, setSlashDismissed,
    setMergeWorkspaceId, setShowMergePanel, setPendingMergeStudy,
    setShowModelPicker, setShowPlusMenu, setAnthropicKeySet, setSidebarCollapsed,
    // state values
    projects, projLoading, openProjId, openProject, view,
    chatCache, globalChats, projInnerTab,
    query, results, searching, searched, sqlQuery, showSql, deepSearch,
    ctxStudies, showNewProj, newProjName, mergeWorkspaceId, showMergePanel, pendingMergeStudy, sidebarCollapsed,
    input, sending, compErr, selectedModel, theme,
    slashIndex, slashDismissed, showModelPicker, showPlusMenu, anthropicKeySet,
    modalStudy, modalDetail, modalDetailLoading,
    projDetailLoading, chatLoading,
    // refs
    taRef, bottomRef,
    // handlers
    createProject, deleteProject, addStudyToProject, removeStudy,
    openProjChat, openGlobChat, newProjChat, deleteProjChat, newGlobChat, deleteGlobChat,
    unpinStudy, pinStudy, sendMessage, openStudyModal, closeModal, enrichAllStudies, doSearch,
    removeCtxStudyFromChat, completeSlash,
    // derived
    projStudyIds, ctxStudyIds, displayStudies, isChat, canSend, topTitle,
    activeMsgs, slashMatches,
  };
}
