function useAppState() {
  const [projects,    setProjects]    = useState([]);
  const [projLoading, setProjLoading] = useState(true);
  const [openProjId,  setOpenProjId]  = useState(null);
  const [openProject, setOpenProject] = useState(null);
  const [view,        setView]        = useState({ type: 'browse' });
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
  const [ctxStudies,   setCtxStudies]   = useState([]);
  const [showNewProj,  setShowNewProj]  = useState(false);
  const [newProjName,  setNewProjName]  = useState('');
  const [input,   setInput]   = useState('');
  const [sending, setSending] = useState(false);
  const [compErr, setCompErr] = useState('');
  const _VALID_MODELS = new Set(['qwen3','qwen3-small','gpt-oss','gemma','gemma-small','kimi','glm-5','minimax-m2']);
  const [selectedModel, setSelectedModelState] = useState(() => {
    try {
      const saved = localStorage.getItem('llm:model');
      return (saved && _VALID_MODELS.has(saved)) ? saved : 'qwen3';
    } catch (_) { return 'qwen3'; }
  });
  const setSelectedModel = (value) => {
    setSelectedModelState(value);
    try { localStorage.setItem('llm:model', value); } catch (_) {}
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activeMsgs.length, lastContent]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => { loadProjects(); loadGlobalChats(); loadFirstStudies(); }, []);

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
            { role: 'assistant', content: '', isStreaming: true, steps: [], pendingStep: null, queryPlan: null },
          ],
        },
      };
    });

  const applyStreamDone = (chatId, title, reportStudyId) => {
    patchLast(chatId, m => ({ ...m, isStreaming: false, pendingStep: null }));
    setChatCache(prev => {
      const cur = prev[chatId] || {};
      const pins = cur.pinnedStudies || [];
      const nextPins = (reportStudyId != null && !pins.includes(reportStudyId)) ? [...pins, reportStudyId] : pins;
      return { ...prev, [chatId]: { ...cur, title, pinnedStudies: nextPins } };
    });
  };

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

  // ─── send ─────────────────────────────────────────────────────────────────────
  const sendMessage = async () => {
    const msg = input.trim();
    if (!msg || sending) return;

    if (/^\/systems\s*$/i.test(msg)) {
      setSending(true); setCompErr(''); setInput('');
      try {
        let chatId;
        if (view.type === 'project-chat') {
          const { projId } = view;
          chatId = view.chatId;
          if (!chatId) {
            const res = await apiPost(`/projects/${projId}/chats`, { user_id: USER_ID });
            if (!res.ok) throw new Error('Failed to create chat');
            const chat = await res.json();
            chatId = chat.chat_id;
            setChatCache(prev => ({ ...prev, [chatId]: { messages: [], title: '/systems', pinnedStudies: [], totalStudiesInProject: chat.total_studies_in_project } }));
            setView(v => ({ ...v, chatId }));
          }
        } else if (view.type === 'global-chat') {
          chatId = view.chatId;
          if (!chatId) {
            const res = await apiPost('/global-chats', { user_id: USER_ID });
            if (!res.ok) throw new Error('Failed to create chat');
            const chat = await res.json();
            chatId = chat.chat_id;
            setChatCache(prev => ({ ...prev, [chatId]: { messages: [], title: '/systems' } }));
            setGlobalChats(prev => [chat, ...prev]);
            setView(v => ({ ...v, chatId }));
          }
        } else {
          return;
        }
        optimisticAppend(chatId, '/systems — Model status');
        patchLast(chatId, m => ({ ...m, pendingStep: { name: 'probe', label: 'Probing all models…' } }));
        const res = await fetch(`${API}/systems`);
        if (!res.ok) throw new Error('Systems check failed');
        const models = await res.json();
        patchLast(chatId, m => ({ ...m, ui: { kind: 'systems_status', models }, pendingStep: null, isStreaming: false }));
      } catch (e) {
        if (e.name !== 'AbortError') setCompErr(e.message || 'Failed to check systems');
      } finally {
        setSending(false);
      }
      return;
    }

    const reportMatch  = /^\/report\s+(\d+)\s*$/i.exec(msg);
    const reportStudyId = reportMatch ? parseInt(reportMatch[1], 10) : null;
    const displayMsg   = reportStudyId != null ? `/report ${reportStudyId} - Full study report` : msg;
    setSending(true); setCompErr(''); setInput('');

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      if (view.type === 'project-chat') {
        let { projId, chatId } = view;
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
            ...m,
            pendingStep: null,
            steps: [...(m.steps || []), { name, label, detail }],
          })),
          onDone:  () => {
            const title = displayMsg.slice(0, 60);
            applyStreamDone(chatId, title, reportStudyId);
            setOpenProject(prev => prev ? {
              ...prev,
              chats: (prev.chats||[]).map(c => c.chat_id === chatId ? { ...c, title } : c)
            } : prev);
          },
          onError: ({ error }) => setCompErr(error || 'Error'),
        }, ctrl.signal);

      } else if (view.type === 'global-chat') {
        let { chatId } = view;
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
        const res = await fetch(`${API}/global-chats/${chatId}/message/stream`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: USER_ID,
            message: msg,
            model: selectedModel,
            selected_studies: ctxStudies,
            ...(reportStudyId != null && { report_study_id: reportStudyId }),
          }),
          signal: ctrl.signal,
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
            ...m,
            pendingStep: null,
            steps: [...(m.steps || []), { name, label, detail }],
          })),
          onQueryPlan: (payload) => patchLast(chatId, m => ({ ...m, queryPlan: payload })),
          onDone:  () => {
            const title = displayMsg.slice(0, 60);
            applyStreamDone(chatId, title, reportStudyId);
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
    const res = await apiPost('/search', { query: q });
    if (res.ok) { const d = await res.json(); setResults(d.results || []); setSqlQuery(d.sql_query || null); }
    else setResults([]);
    setSearched(true); setSearching(false);
  };

  // ─── derived ──────────────────────────────────────────────────────────────────
  const projStudyIds   = useMemo(() => (openProject?.studies || []).map(s => s.study_id), [openProject]);
  const ctxStudyIds    = useMemo(() => ctxStudies.map(s => s.study_id), [ctxStudies]);
  const displayStudies = searched ? results : firstStudies;
  const isChat         = view.type === 'project-chat' || view.type === 'global-chat';
  const canSend        = isChat && input.trim().length > 0 && !sending;

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
    setQuery, setResults, setSearched, setSqlQuery, setShowSql,
    setCtxStudies, setInput, setSelectedModel,
    // state values
    projects, projLoading, openProjId, openProject, view,
    chatCache, globalChats, projInnerTab,
    query, results, firstStudies, searching, searched, sqlQuery, showSql,
    ctxStudies, showNewProj, newProjName,
    input, sending, compErr, selectedModel,
    modalStudy, modalDetail, modalDetailLoading,
    projDetailLoading, chatLoading,
    // refs
    taRef, bottomRef,
    // handlers
    loadProjects, fetchProjectDetail, loadGlobalChats, loadFirstStudies,
    createProject, deleteProject, addStudyToProject, removeStudy,
    openProjChat, openGlobChat, newProjChat, deleteProjChat, newGlobChat, deleteGlobChat,
    unpinStudy, sendMessage, openStudyModal, closeModal, enrichAllStudies, doSearch,
    // derived
    projStudyIds, ctxStudyIds, displayStudies, isChat, canSend, topTitle,
    activeMsgs, lastContent,
  };
}
