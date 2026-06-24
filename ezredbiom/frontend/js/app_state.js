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
  const _VALID_MODELS = new Set([..._NRP_MODELS, ..._CLAUDE_MODELS].map(m => m[0]));
  const [selectedModel, setSelectedModelState] = useState(() => {
    try {
      const saved = localStorage.getItem('llm:model');
      return (saved && _VALID_MODELS.has(saved)) ? saved : 'qwen3';
    } catch (_) { return 'qwen3'; }
  });
  const setSelectedModel = (value) => {
    setSelectedModelState(value);
    const chatId = view.chatId || null;
    try {
      if (chatId) localStorage.setItem(`model:chat:${chatId}`, value);
      localStorage.setItem('llm:model', value);
    } catch (_) {}
  };
  const [showModelPicker, setShowModelPicker] = useState(false);
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
    fetch('/api/settings').then(r => r.json()).then(d => setAnthropicKeySet(d.anthropic_key_set)).catch(() => {});
  }, []);

  useEffect(() => {
    const chatId = view.chatId || null;
    try {
      const perChat = chatId ? localStorage.getItem(`model:chat:${chatId}`) : null;
      const global  = localStorage.getItem('llm:model') || 'qwen3';
      const effective = (perChat && _VALID_MODELS.has(perChat)) ? perChat
                      : (_VALID_MODELS.has(global) ? global : 'qwen3');
      setSelectedModelState(effective);
    } catch (_) {}
  }, [view.chatId]);

  useEffect(() => {
    if (!openProjId) { setOpenProject(null); return; }
    fetchProjectDetail(openProjId);
  }, [openProjId]);

  // ─── loaders ─────────────────────────────────────────────────────────────────
  const loadProjects = async () => {
    setProjLoading(true);
    try {
      const res = await apiFetch(`/projects?user_id=${USER_ID}`);
      if (res.ok) { const d = await res.json(); setProjects(d.projects || []); }
    } finally { setProjLoading(false); }
  };

  const fetchProjectDetail = async (pid) => {
    setProjDetailLoading(true);
    try {
      const res = await apiFetch(`/projects/${pid}?user_id=${USER_ID}`);
      if (res.ok) setOpenProject(await res.json());
      apiPost(`/projects/${pid}/preload`, { user_id: USER_ID }).catch(() => {});
    } finally {
      setProjDetailLoading(false);
    }
  };

  const loadGlobalChats = async () => {
    const res = await apiFetch(`/global-chats?user_id=${USER_ID}`);
    if (res.ok) { const d = await res.json(); setGlobalChats(d.chats || []); }
  };

  const loadFirstStudies = async () => {
    const res = await apiFetch('/studies/first?limit=20');
    if (res.ok) { const d = await res.json(); setFirstStudies(d.results || []); }
  };

  const hydrateChatCache = async (type, projId, chatId) => {
    if (chatCache[chatId]) return;
    const res = type === 'project-chat'
      ? await apiFetch(`/projects/${projId}/chats/${chatId}?user_id=${USER_ID}`)
      : await apiFetch(`/global-chats/${chatId}?user_id=${USER_ID}`);
    if (res.ok) {
      const d = await res.json();
      const messages = (d.messages || []).map(m => m.ui_payload ? { ...m, ui: m.ui_payload } : m);
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
    const res  = await apiPost('/projects', { user_id: USER_ID, name });
    if (!res.ok) return;
    const proj = await res.json();
    setNewProjName(''); setShowNewProj(false);
    await loadProjects();
    setOpenProjId(proj.project_id);
    setProjInnerTab('chats');
  };

  const deleteProject = async (pid) => {
    if (!confirm('Delete this project and all its chats?')) return;
    await apiDel(`/projects/${pid}?user_id=${USER_ID}`);
    if (openProjId === pid) { setOpenProjId(null); setOpenProject(null); }
    if (view.projId === pid) setView({ type: 'browse' });
    loadProjects();
  };

  const addStudyToProject = async (study) => {
    if (!openProjId) return;
    const res = await apiPost(`/projects/${openProjId}/studies`, { user_id: USER_ID, study });
    if (res.ok) { const updated = await res.json(); setOpenProject(updated); }
  };

  const removeStudy = async (studyId) => {
    if (!openProjId) return;
    const res = await apiDel(`/projects/${openProjId}/studies/${studyId}?user_id=${USER_ID}`);
    if (res.ok) { const updated = await res.json(); setOpenProject(updated); }
  };

  // ─── chat navigation ──────────────────────────────────────────────────────────
  const openProjChat = async (projId, chatId) => {
    setView({ type: 'project-chat', projId, chatId });
    setCompErr('');
    if (!chatCache[chatId]) {
      setChatLoading(true);
      try { await hydrateChatCache('project-chat', projId, chatId); }
      finally { setChatLoading(false); }
    }
  };

  const openGlobChat = async (chatId) => {
    setView({ type: 'global-chat', chatId });
    setCompErr('');
    if (!chatCache[chatId]) {
      setChatLoading(true);
      try { await hydrateChatCache('global-chat', null, chatId); }
      finally { setChatLoading(false); }
    }
  };

  const newProjChat = async (projId) => {
    const res = await apiPost(`/projects/${projId}/chats`, { user_id: USER_ID });
    if (!res.ok) return;
    const chat = await res.json();
    setChatCache(prev => ({ ...prev, [chat.chat_id]: { messages: [], title: 'New chat' } }));
    setOpenProject(prev => prev ? { ...prev, chats: [{ ...chat, messages: [] }, ...(prev.chats || [])] } : prev);
    setView({ type: 'project-chat', projId, chatId: chat.chat_id });
    setCompErr('');
  };

  const deleteProjChat = async (projId, chatId) => {
    await apiDel(`/projects/${projId}/chats/${chatId}?user_id=${USER_ID}`);
    setChatCache(prev => { const n = { ...prev }; delete n[chatId]; return n; });
    if (view.chatId === chatId) setView({ type: 'project-chat', projId, chatId: null });
    setOpenProject(prev => prev ? { ...prev, chats: (prev.chats || []).filter(c => c.chat_id !== chatId) } : prev);
  };

  const newGlobChat = async () => {
    const res = await apiPost('/global-chats', { user_id: USER_ID });
    if (!res.ok) return;
    const chat = await res.json();
    setChatCache(prev => ({ ...prev, [chat.chat_id]: { messages: [], title: 'New chat' } }));
    setGlobalChats(prev => [chat, ...prev]);
    setView({ type: 'global-chat', chatId: chat.chat_id });
    setCompErr('');
  };

  const deleteGlobChat = async (chatId) => {
    await apiDel(`/global-chats/${chatId}?user_id=${USER_ID}`);
    setChatCache(prev => { const n = { ...prev }; delete n[chatId]; return n; });
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

  const applyStreamDone = (chatId, title, reportStudyId, pinnedList) => {
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
    setChatCache(prev => {
      const cur = prev[chatId];
      if (!cur) return prev;
      return { ...prev, [chatId]: { ...cur, pinnedStudies: (cur.pinnedStudies || []).filter(id => id !== studyId) } };
    });
    try {
      if (view.type === 'project-chat' && view.projId) {
        await apiDel(`/projects/${view.projId}/chats/${chatId}/pinned/${studyId}?user_id=${USER_ID}`);
      } else if (view.type === 'global-chat') {
        await apiDel(`/global-chats/${chatId}/pinned/${studyId}?user_id=${USER_ID}`);
      }
    } catch (_) {}
  };

  const pinStudy = async (chatId, study) => {
    const studyId = study.study_id;
    setChatCache(prev => {
      const cur = prev[chatId];
      if (!cur) return prev;
      if ((cur.pinnedStudies || []).includes(studyId)) return prev;
      return { ...prev, [chatId]: { ...cur, pinnedStudies: [...(cur.pinnedStudies || []), studyId] } };
    });
    try {
      let res;
      if (view.type === 'project-chat' && view.projId) {
        res = await apiPost(`/projects/${view.projId}/chats/${chatId}/pinned/${studyId}?user_id=${USER_ID}`, {});
      } else if (view.type === 'global-chat') {
        res = await apiPost(`/global-chats/${chatId}/pinned/${studyId}?user_id=${USER_ID}`, {});
      }
      if (res?.ok) {
        const data = await res.json();
        if (data.pinned_studies) {
          setChatCache(prev => ({ ...prev, [chatId]: { ...(prev[chatId] || {}), pinnedStudies: data.pinned_studies } }));
        }
      }
    } catch (_) {}
  };

  const removeCtxStudyFromChat = (chatId, studyId) => {
    setChatCache(prev => {
      const c = prev[chatId];
      if (!c) return prev;
      return { ...prev, [chatId]: { ...c, ctxStudies: (c.ctxStudies || []).filter(s => s.study_id !== studyId) } };
    });
  };

  const completeSlash = (item) => {
    setInput(item.insert);
    setTimeout(() => taRef.current?.focus(), 0);
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
        const res = await apiPost('/global-chats', { user_id: USER_ID });
        if (!res.ok) throw new Error('Failed to create chat');
        const chat = await res.json();
        setChatCache(prev => ({
          ...prev,
          [chat.chat_id]: { messages: [], title: displayMsg.slice(0, 60), ctxStudies: snapCtx },
        }));
        setGlobalChats(prev => [chat, ...prev]);
        workView   = { type: 'global-chat', chatId: chat.chat_id };
        setView(workView);
        setCtxStudies([]);
        studiesCtx = snapCtx;
      }

      // ── /systems ────────────────────────────────────────────────────────────
      if (/^\/systems\s*$/i.test(msg)) {
        let chatId;
        if (workView.type === 'project-chat') {
          const { projId } = workView;
          chatId = workView.chatId;
          if (!chatId) {
            const res = await apiPost(`/projects/${projId}/chats`, { user_id: USER_ID });
            if (!res.ok) throw new Error('Failed to create chat');
            const chat = await res.json();
            chatId = chat.chat_id;
            setChatCache(prev => ({ ...prev, [chatId]: { messages: [], title: '/systems', pinnedStudies: [], totalStudiesInProject: chat.total_studies_in_project } }));
            setView(v => ({ ...v, chatId }));
          }
        } else if (workView.type === 'global-chat') {
          chatId = workView.chatId;
          if (!chatId) {
            const res = await apiPost('/global-chats', { user_id: USER_ID });
            if (!res.ok) throw new Error('Failed to create chat');
            const chat = await res.json();
            chatId = chat.chat_id;
            setChatCache(prev => ({ ...prev, [chatId]: { messages: [], title: '/systems' } }));
            setGlobalChats(prev => [chat, ...prev]);
            setView(v => ({ ...v, chatId }));
          }
        }
        if (!chatId) return;
        optimisticAppend(chatId, '/systems — Model status');
        patchLast(chatId, m => ({ ...m, pendingStep: { name: 'probe', label: 'Probing all models…' } }));
        const res = await fetch(`${API}/systems`);
        if (!res.ok) throw new Error('Systems check failed');
        const models = await res.json();
        patchLast(chatId, m => ({ ...m, ui: { kind: 'systems_status', models }, pendingStep: null, isStreaming: false }));
        return;
      }

      // ── /report + regular messages ──────────────────────────────────────────
      if (workView.type === 'project-chat') {
        let { projId, chatId } = workView;
        if (!chatId) {
          const res = await apiPost(`/projects/${projId}/chats`, { user_id: USER_ID });
          if (!res.ok) throw new Error('Failed to create chat');
          const chat = await res.json();
          chatId = chat.chat_id;
          setChatCache(prev => ({
            ...prev,
            [chatId]: {
              messages: [],
              title: displayMsg.slice(0, 60),
              pinnedStudies: chat.pinned_studies || [],
              totalStudiesInProject: chat.total_studies_in_project,
            },
          }));
          setView(v => ({ ...v, chatId }));
        }
        optimisticAppend(chatId, displayMsg);
        const res = await fetch(`${API}/projects/${projId}/chats/${chatId}/message/stream`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: USER_ID,
            message: msg,
            model: selectedModel,
            ...(reportStudyId != null && { report_study_id: reportStudyId }),
            ...(pinStudyIds   != null && { pin_study_ids: pinStudyIds }),
          }), signal: ctrl.signal,
        });
        if (!res.ok || !res.body) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || 'Stream failed');
        }
        await parseSSE(res, {
          onToken:     ({ token }) => patchLast(chatId, m => ({ ...m, content: (m.content||'') + (token||'') })),
          onUi:        (payload)  => patchLast(chatId, m => ({ ...m, ui: payload, content: '' })),
          onStepStart: ({ name, label }) => patchLast(chatId, m => ({ ...m, pendingStep: { name, label } })),
          onStepDone:  ({ name, label, detail }) => patchLast(chatId, m => ({
            ...m, pendingStep: null, steps: [...(m.steps || []), { name, label, detail }],
          })),
          onDone: (payload) => {
            const title = displayMsg.slice(0, 60);
            applyStreamDone(chatId, title, reportStudyId, payload?.pinned_studies ?? null);
            setOpenProject(prev => prev ? {
              ...prev, chats: (prev.chats||[]).map(c => c.chat_id === chatId ? { ...c, title } : c)
            } : prev);
          },
          onError: ({ error }) => setCompErr(error || 'Error'),
        }, ctrl.signal);

      } else if (workView.type === 'global-chat') {
        let { chatId } = workView;
        if (!chatId) {
          const res = await apiPost('/global-chats', { user_id: USER_ID });
          if (!res.ok) throw new Error('Failed to create chat');
          const chat = await res.json();
          chatId = chat.chat_id;
          setChatCache(prev => ({ ...prev, [chatId]: { messages: [], title: displayMsg.slice(0, 60) } }));
          setGlobalChats(prev => [chat, ...prev]);
          setView(v => ({ ...v, chatId }));
        }
        optimisticAppend(chatId, displayMsg);
        const ctxToSend = studiesCtx !== null ? studiesCtx : (chatCache[chatId]?.ctxStudies || []);
        const res = await fetch(`${API}/global-chats/${chatId}/message/stream`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: USER_ID,
            message: sendMsg,
            model: selectedModel,
            selected_studies: ctxToSend,
            ...(reportStudyId != null && { report_study_id: reportStudyId }),
            ...(pinStudyIds   != null && { pin_study_ids: pinStudyIds }),
            ...(isDeepSearch            && { deep_search: true }),
          }),
          signal: ctrl.signal,
        });
        if (!res.ok || !res.body) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || 'Stream failed');
        }
        await parseSSE(res, {
          onToken:             onTokenAgent(chatId),
          onUi:                (payload) => patchLast(chatId, m => ({ ...m, ui: payload, content: '' })),
          onStepStart:         ({ name, label }) => patchLast(chatId, m => ({ ...m, pendingStep: { name, label } })),
          onStepDone:          ({ name, label, detail }) => patchLast(chatId, m => ({
            ...m, pendingStep: null, steps: [...(m.steps || []), { name, label, detail }],
          })),
          onQueryPlan:         (payload) => patchLast(chatId, m => ({ ...m, queryPlan: payload })),
          onAgentStart:        onAgentStart(chatId),
          onSegmentToolCall:   onSegmentToolCall(chatId),
          onSegmentToolResult: onSegmentToolResult(chatId),
          onDone: (payload) => {
            const title = displayMsg.slice(0, 60);
            applyStreamDone(chatId, title, reportStudyId, payload?.pinned_studies ?? null);
            setGlobalChats(prev => prev.map(c => c.chat_id === chatId ? { ...c, title } : c));
          },
          onError: ({ error }) => setCompErr(error || 'Error'),
        }, ctrl.signal);
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
    const res = await apiPost(`/projects/${projId}/studies/enrich-all`, { user_id: USER_ID });
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
  const slashMatches   = useMemo(() => {
    if (!/^\/\S*$/.test(input)) return [];
    const q = input.toLowerCase();
    return SLASH_COMMANDS.filter(c => c.cmd.startsWith(q));
  }, [input]);

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
    query, results, firstStudies, searching, searched, sqlQuery, showSql, deepSearch,
    ctxStudies, showNewProj, newProjName, mergeWorkspaceId, showMergePanel, pendingMergeStudy, sidebarCollapsed,
    input, sending, compErr, selectedModel, theme,
    slashIndex, slashDismissed, showModelPicker, showPlusMenu, anthropicKeySet,
    modalStudy, modalDetail, modalDetailLoading,
    projDetailLoading, chatLoading,
    // refs
    taRef, bottomRef,
    // handlers
    loadProjects, fetchProjectDetail, loadGlobalChats, loadFirstStudies,
    createProject, deleteProject, addStudyToProject, removeStudy,
    openProjChat, openGlobChat, newProjChat, deleteProjChat, newGlobChat, deleteGlobChat,
    unpinStudy, pinStudy, sendMessage, openStudyModal, closeModal, enrichAllStudies, doSearch,
    removeCtxStudyFromChat, completeSlash,
    // derived
    projStudyIds, ctxStudyIds, displayStudies, isChat, canSend, topTitle,
    activeMsgs, lastContent, slashMatches,
  };
}
