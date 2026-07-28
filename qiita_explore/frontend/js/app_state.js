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

  // App-level error for failed MUTATIONS, shown wherever the user is. compErr
  // above is composer-scoped (app_render.js renders it inside the composer), so
  // it cannot surface a browse-grid failure — which is why "+ Add to Project"
  // silently doing nothing looked like a broken button rather than the 403 it
  // actually was.
  const [actionErr, setActionErr] = useState('');

  const abortRef      = useRef(null);
  const taRef         = useRef(null);
  const bottomRef     = useRef(null);
  const modalAbortRef = useRef(null);
  // Bumped on every project-detail fetch; a response may only be applied if it
  // is still the newest. See fetchProjectDetail.
  const projDetailGen = useRef(0);

  /**
   * Run a mutating request and surface its failure.
   *
   * Returns the parsed body on success, or null on failure — so callers keep the
   * `if (result)` shape they had with `if (res.ok)` while a real error message
   * reaches the user. Every mutation in this file previously discarded its own
   * failure: `if (res.ok) { ... }` with no else, and the apiDel calls did not
   * check `res.ok` at all, so a failed delete still removed the row from the UI.
   *
   * A shared wrapper rather than eleven copies of the same three lines — that
   * duplication is how one of them ends up subtly different later.
   */
  const mutate = async (fn, fallbackMsg) => {
    setActionErr('');
    let res;
    try {
      res = await fn();
    } catch (e) {
      setActionErr(`${fallbackMsg}: could not reach the backend.`);
      return null;
    }
    if (res.ok) {
      // 204 and empty bodies are legitimate for DELETE.
      try { return await res.json(); } catch { return {}; }
    }
    let detail = '';
    try { detail = (await res.json())?.error || ''; } catch { /* non-JSON body */ }
    setActionErr(detail || `${fallbackMsg} (HTTP ${res.status}).`);
    return null;
  };

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
    // Generation guard. This fires from a useEffect on openProjId, so it races
    // any mutation the user makes immediately after opening a project: Add to
    // Project POSTs, gets the project back WITH the new study, calls
    // setOpenProject — and then this still-in-flight GET resolves with the
    // project as it was BEFORE the add and overwrites it. projStudyIds loses the
    // study, inProj goes false, and the button un-flips. Reported as "the button
    // does not change to added, even though the study may have been added"; both
    // halves of that sentence were true.
    //
    // Not fixed by re-fetching after the add: CLAUDE.md is explicit that
    // mutations update local state from the response body and never re-fetch,
    // and the POST already does that correctly. The stale reader is the bug.
    const gen = ++projDetailGen.current;
    setProjDetailLoading(true);
    try {
      const res = await apiFetch(`/projects/${pid}`);
      if (res.ok && gen === projDetailGen.current) setOpenProject(await res.json());
      apiPost(`/projects/${pid}/preload`, {}).catch(() => {});
    } finally {
      // Unconditional: this request IS finished either way, and leaving the flag
      // set because a newer write superseded us would spin forever.
      setProjDetailLoading(false);
    }
  };

  /**
   * Write an authoritative project from a mutation response, and invalidate any
   * project-detail GET still in flight.
   *
   * The generation bump is the load-bearing half. Guarding fetchProjectDetail
   * against *other fetches* is not enough — the race that bit is a MUTATION
   * landing while one fetch is outstanding, and only bumping the counter here
   * makes that fetch's response stale rather than newest-wins.
   */
  const commitProject = (proj) => {
    projDetailGen.current++;
    setOpenProject(proj);
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
    const proj = await mutate(() => apiPost('/projects', { name }), 'Could not create the project');
    if (!proj) return;
    setNewProjName(''); setShowNewProj(false);
    await loadProjects();
    setOpenProjId(proj.project_id);
    setProjInnerTab('chats');
  };

  const deleteProject = async (pid) => {
    if (!confirm('Delete this project and all its chats?')) return;
    // Previously unchecked: a failed DELETE still cleared the project from the
    // UI, so it looked deleted and came back on the next load.
    if (!await mutate(() => apiDel(`/projects/${pid}`), 'Could not delete the project')) return;
    if (openProjId === pid) { setOpenProjId(null); setOpenProject(null); }
    if (view.projId === pid) setView({ type: 'browse' });
    loadProjects();
  };

  const addStudyToProject = async (study) => {
    if (!openProjId) return;
    // A 403 here ("Study is not public and cannot be added",
    // routes/project_routes.py) used to produce nothing at all: no message, no
    // state change, button unchanged — indistinguishable from a broken button.
    const updated = await mutate(
      () => apiPost(`/projects/${openProjId}/studies`, { study }),
      'Could not add the study to this project',
    );
    if (updated) commitProject(updated);
  };

  const removeStudy = async (studyId) => {
    if (!openProjId) return;
    const updated = await mutate(
      () => apiDel(`/projects/${openProjId}/studies/${studyId}`),
      'Could not remove the study from this project',
    );
    if (updated) commitProject(updated);
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
    // The seeder throws on a non-ok response; this used to .catch(() => null)
    // and return, so a failed "New chat" click did nothing visible at all.
    let chat;
    try { chat = await createProjChatAndSeed(projId, 'New chat'); }
    catch (e) { setActionErr('Could not create the chat.'); return; }
    setOpenProject(prev => prev ? { ...prev, chats: [{ ...chat, messages: [] }, ...(prev.chats || [])] } : prev);
    setView({ type: 'project-chat', projId, chatId: chat.chat_id });
    setCompErr('');
  };

  const deleteProjChat = async (projId, chatId) => {
    if (!await mutate(() => apiDel(`/projects/${projId}/chats/${chatId}`),
                      'Could not delete the chat')) return;
    dropChat(chatId);
    if (view.chatId === chatId) setView({ type: 'project-chat', projId, chatId: null });
    setOpenProject(prev => prev ? { ...prev, chats: (prev.chats || []).filter(c => c.chat_id !== chatId) } : prev);
  };

  const newGlobChat = async () => {
    let chat;
    try { chat = await createGlobalChatAndSeed('New chat'); }
    catch (e) { setActionErr('Could not create the chat.'); return; }
    setView({ type: 'global-chat', chatId: chat.chat_id });
    setCompErr('');
  };

  const deleteGlobChat = async (chatId) => {
    if (!await mutate(() => apiDel(`/global-chats/${chatId}`),
                      'Could not delete the chat')) return;
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
            { role: 'assistant', content: '', isStreaming: true, steps: [], pendingStep: null, segments: null },
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

  // Coalesces per-token calls into ~one flush per animation frame instead of
  // one React state update per SSE token. A streamed reply used to call
  // onToken (-> patchLast -> setChatCache) for every single token — hundreds
  // of times for a long answer — and each one re-rendered AgentMessageBubble,
  // which re-parses the ENTIRE accumulated markdown via marked.parse +
  // DOMPurify.sanitize on every render (components.js). That's O(n^2) work in
  // reply length and was the actual source of input lag / dropped frames
  // while a reply streams. Concatenating buffered chars into one onToken call
  // keeps every downstream handler's contract unchanged — they just receive
  // fewer, larger chunks.
  function makeTokenBuffer(onToken) {
    let pending = '';
    let rafHandle = null;
    const flush = () => {
      if (rafHandle !== null) { cancelAnimationFrame(rafHandle); rafHandle = null; }
      if (!pending || !onToken) return;
      const chunk = pending;
      pending = '';
      onToken({ token: chunk });
    };
    const bufferedOnToken = ({ token }) => {
      pending += token || '';
      if (rafHandle === null) rafHandle = requestAnimationFrame(flush);
    };
    return { flush, onToken: onToken ? bufferedOnToken : undefined };
  }

  // Shared fetch+guard+parseSSE for a chat stream. `handlers.onToken`/`onDone`
  // are supplied per call site; the step/ui/error handlers are identical
  // across project and global chat.
  const streamChat = async (url, body, chatId, signal, handlers) => {
    const res = await apiFetch(url, { method: 'POST', body: JSON.stringify(body), signal });
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Stream failed');
    }

    const tokenBuffer = makeTokenBuffer(handlers.onToken);
    // Anything that can interleave with tokens must flush the buffer FIRST, or
    // ordering/final content breaks: parseSSE dispatches events synchronously
    // as they're parsed off the wire, so two SSE frames arriving in the same
    // network chunk can fire onToken then e.g. onDone with no await between
    // them — racing the rAF-scheduled flush and losing the tail of the reply.
    const flushBefore = (fn) => fn && ((...args) => { tokenBuffer.flush(); return fn(...args); });

    try {
      await parseSSE(res, {
        onUi:        (payload) => patchLast(chatId, m => ({ ...m, ui: payload, content: '' })),
        onStepStart: flushBefore(({ name, label }) => patchLast(chatId, m => ({ ...m, pendingStep: { name, label } }))),
        onStepDone:  flushBefore(({ name, label, detail }) => patchLast(chatId, m => ({
          ...m, pendingStep: null, steps: [...(m.steps || []), { name, label, detail }],
        }))),
        onError: flushBefore(({ error }) => {
          setCompErr(error || 'Error');
          patchLast(chatId, m => ({
            ...m,
            isStreaming: false,
            pendingStep: null,
            content: m.content || `⚠️ ${error || 'Something went wrong.'}`,
          }));
        }),
        ...handlers,
        onToken: tokenBuffer.onToken,
        onAgentStart:        flushBefore(handlers.onAgentStart),
        onSegmentToolCall:   flushBefore(handlers.onSegmentToolCall),
        onSegmentToolResult: flushBefore(handlers.onSegmentToolResult),
        onDone:              flushBefore(handlers.onDone),
      }, signal);
    } finally {
      // Guarantees no buffered tail is lost even if the stream throws/aborts —
      // matches the pre-coalescing behavior where every token was applied
      // immediately regardless of how the stream ended.
      tokenBuffer.flush();
    }
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

  // Chat-level, not per-message: the composer subtext names the runtime that
  // served the most recent turn. Every chat path emits `runtime` first — pi and
  // legacy, project and global — so this is wired for all of them, including
  // the non-agentic legacy project path that never emits agent_start.
  const onRuntime = (chatId) => ({ runtime }) =>
    patchChat(chatId, () => ({ runtime }));

  // The five handlers every agentic stream needs, as one unit — spread by both
  // call sites so they cannot drift. They did once: project chat used a raw
  // content-appending onToken, which renders blank, because agent_start flips
  // app_render.js to AgentMessageBubble and that reads only `segments`.
  const agentHandlers = (chatId) => ({
    onToken:             onTokenAgent(chatId),
    onAgentStart:        onAgentStart(chatId),
    onSegmentToolCall:   onSegmentToolCall(chatId),
    onSegmentToolResult: onSegmentToolResult(chatId),
    onRuntime:           onRuntime(chatId),
  });

  const unpinStudy = async (chatId, studyId) => {
    patchChat(chatId, cur => ({ pinnedStudies: (cur.pinnedStudies || []).filter(id => id !== studyId) }));
    const base = chatScopeUrl(view, chatId);
    if (!base) return;
    // Optimistic by design — a pin toggle should feel instant. But a swallowed
    // failure left the UI showing unpinned while the backend still had it, which
    // is the same lie as a delete that appears to work. Roll the optimistic
    // update back and say so.
    if (!await mutate(() => apiDel(`${base}/pinned/${studyId}`), 'Could not unpin the study')) {
      patchChat(chatId, cur => (cur.pinnedStudies || []).includes(studyId)
        ? null
        : { pinnedStudies: [...(cur.pinnedStudies || []), studyId] });
    }
  };

  const pinStudy = async (chatId, study) => {
    const studyId = study.study_id;
    patchChat(chatId, cur => (cur.pinnedStudies || []).includes(studyId)
      ? null
      : { pinnedStudies: [...(cur.pinnedStudies || []), studyId] });
    const base = chatScopeUrl(view, chatId);
    if (!base) return;
    const data = await mutate(() => apiPost(`${base}/pinned/${studyId}`, {}), 'Could not pin the study');
    if (!data) {
      // Roll back the optimistic add — see unpinStudy.
      patchChat(chatId, cur => ({ pinnedStudies: (cur.pinnedStudies || []).filter(id => id !== studyId) }));
    } else if (data.pinned_studies) {
      setChatCache(prev => ({ ...prev, [chatId]: { ...(prev[chatId] || {}), pinnedStudies: data.pinned_studies } }));
    }
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
      if (workView.type === 'project-chat' || workView.type === 'global-chat') {
        const isGlobal = workView.type === 'global-chat';
        const chatId   = await ensureChatId(workView, displayMsg.slice(0, 60));
        optimisticAppend(chatId, displayMsg);

        const ctxToSend = isGlobal ? (studiesCtx !== null ? studiesCtx : (chatCache[chatId]?.ctxStudies || [])) : null;
        const extraBody = {
          ...(reportStudyId != null && { report_study_id: reportStudyId }),
          ...(pinStudyIds   != null && { pin_study_ids: pinStudyIds }),
          ...(isGlobal && { selected_studies: ctxToSend, ...(isDeepSearch && { deep_search: true }) }),
        };
        const onTitleCommitted = (title) => isGlobal
          ? setGlobalChats(prev => prev.map(c => c.chat_id === chatId ? { ...c, title } : c))
          : setOpenProject(prev => prev ? {
              ...prev, chats: (prev.chats||[]).map(c => c.chat_id === chatId ? { ...c, title } : c)
            } : prev);

        await streamChat(
          `${chatScopeUrl(workView, chatId)}/message/stream`,
          { message: isGlobal ? sendMsg : msg, model: selectedModel, ...extraBody },
          chatId, ctrl.signal,
          {
            ...agentHandlers(chatId),
            onDone: (payload) => {
              const title = displayMsg.slice(0, 60);
              applyStreamDone(chatId, title, payload?.pinned_studies ?? null);
              onTitleCommitted(title);
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
    const d = await mutate(() => apiPost(`/projects/${projId}/studies/enrich-all`, {}),
                            'Could not refresh study details');
    if (d?.project) commitProject(d.project);
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
    input, sending, compErr, actionErr, setActionErr, selectedModel, theme,
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
