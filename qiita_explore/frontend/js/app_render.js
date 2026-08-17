function ChatTitleEditor({ title, onSave }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(title || '');
  useEffect(() => { setVal(title || ''); }, [title]);
  const save = async () => {
    const t = val.trim();
    setEditing(false);
    if (!t || t === title) { setVal(title || ''); return; }
    await onSave(t);
  };
  if (editing) return (
    <input className="topbar-title-input" value={val} autoFocus
      onChange={e => setVal(e.target.value)}
      onBlur={save}
      onKeyDown={e => {
        if (e.key === 'Enter') { e.preventDefault(); save(); }
        if (e.key === 'Escape') { setEditing(false); setVal(title || ''); }
      }} />
  );
  return (
    <span className="topbar-title editable" onClick={() => setEditing(true)} title="Click to rename">
      {title}<span className="rename-hint">✎</span>
    </span>
  );
}

function renderApp(s, account) {
  const {
    setView, setOpenProjId, setProjInnerTab, setShowNewProj, setNewProjName,
    setQuery, setResults, setSearched, setSqlQuery, setAppliedFilters, setShowSql,
    setCtxStudies, setInput, setSelectedModel, setTheme,
    setSlashIndex, setSlashDismissed,
    setMergeWorkspaceId, setShowMergePanel, setPendingMergeStudy,
    setShowModelPicker, setShowPlusMenu, setAnthropicKeySet, setSidebarCollapsed,
    setEditingChatId, setEditChatVal,
    openSearchResultsPanel, closeSearchResultsPanel, finishCloseSearchResultsPanel, openMergePanel,
    projects, projLoading, openProjId, openProject, view,
    chatCache, globalChats, projInnerTab,
    query, results, searching, searched, sqlQuery, appliedFilters, showSql,
    ctxStudies, showNewProj, newProjName, mergeWorkspaceId, showMergePanel, pendingMergeStudy, sidebarCollapsed,
    editingChatId, editChatVal,
    searchResultsPanel, searchResultsClosing,
    input, sending, compErr, addStudyErr, selectedModel, theme,
    slashIndex, slashDismissed, showModelPicker, showPlusMenu, anthropicKeySet,
    modalStudy, modalDetail, modalDetailLoading,
    projDetailLoading, chatLoading,
    taRef, bottomRef,
    createProject, deleteProject, addStudyToProject, removeStudy,
    openProjChat, openGlobChat, newProjChat, deleteProjChat, newGlobChat, deleteGlobChat,
    unpinStudy, pinStudy, sendMessage, openStudyModal, closeModal, enrichAllStudies, doSearch,
    completeSlash, renameChat, renameProjChat, renameGlobChat,
    projStudyIds, ctxStudyIds, displayStudies, isChat, canSend, topTitle,
    activeMsgs, slashMatches,
  } = s;

  // One list of {study_id, study_title}; ids are derived where a bare id is
  // needed, rather than kept as a second array in lockstep.
  const pinnedMeta         = chatCache[view.chatId]?.pinnedStudyMeta || [];
  const hasProjectSources  = view.type === 'project-chat' && openProject?.studies?.length > 0;
  const hasGlobalPins      = view.type === 'global-chat' && pinnedMeta.length > 0;
  const hasSourcesBar      = hasProjectSources || hasGlobalPins;
  const resultsDrawerOpen  = !!(searchResultsPanel && !searchResultsClosing);

  // Shared save for both sidebar chat lists' inline rename (see chat-row
  // rendering below) — mirrors MergesTab's saveRename pattern.
  const saveRenameChat = (kind, projId, chatId) => {
    const t = editChatVal.trim();
    setEditingChatId(null);
    if (!t) return;
    if (kind === 'proj') renameProjChat(projId, chatId, t);
    else renameGlobChat(chatId, t);
  };

  return (
    <div className={`app${theme === 'dark' ? ' dark' : ''}`}>

      {/* ══════════════════ SIDEBAR ══════════════════ */}
      <aside className={`sidebar${sidebarCollapsed ? ' collapsed' : ''}`}>
        <div className="sidebar-header">
          <div className="app-logo app-logo-home" onClick={() => setView({ type: 'browse' })}>
            <span className="app-logo-mark"><WreathLoader size={28} /></span>
            <span className="app-logo-text">Qiita<em>Explore</em></span>
          </div>
        </div>

        <div className="sidebar-body">

          {/* Mirrors the "View all →" drawer (SearchResultsPanel below) —
              same searchResultsPanel state drives both, so they open and
              close together. This is the sidebar rendering of the same
              retrieved-studies list, using the Studies-tab's row pattern
              (title + study ID) rather than the drawer's InlineStudyCard. */}
          {searchResultsPanel && (
            <>
              <div className="sb-label with-action">
                <span>Search Results ({(searchResultsPanel.studies || []).length})</span>
                <button className="sb-label-close" onClick={closeSearchResultsPanel} title="Clear">×</button>
              </div>
              {(searchResultsPanel.studies || []).length === 0 ? (
                <div className="folder-empty">No studies in this result set.</div>
              ) : (
                (searchResultsPanel.studies || []).map(s => (
                  <div key={s.study_id} className="chat-row" onClick={() => openStudyModal(s)}>
                    <div className="cr-content">
                      <div className="cr-title">{s.study_title || 'Untitled'}</div>
                      <div className="cr-date">ID {s.study_id}</div>
                    </div>
                  </div>
                ))
              )}
            </>
          )}

          <div className="sb-label">Workspaces</div>
          {projLoading && <div className="sb-loading">Loading…</div>}

          {projects.map(p => (
            <div key={p.project_id}>
              <div
                className={`folder-row ${openProjId === p.project_id ? 'open' : ''} ${view.projId === p.project_id ? 'viewing' : ''}`}
                onClick={() => {
                  if (openProjId === p.project_id) setOpenProjId(null);
                  else { setOpenProjId(p.project_id); setProjInnerTab('chats'); }
                }}
              >
                <span className="folder-caret"><ChevronIcon open={openProjId === p.project_id} /></span>
                <span className="folder-icon-svg">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>
                  </svg>
                </span>
                <span className="folder-name">{p.name}</span>
                <button className="folder-del" title="Delete" onClick={e => { e.stopPropagation(); deleteProject(p.project_id); }}>×</button>
              </div>

              {openProjId === p.project_id && (
                <div className="folder-expanded">
                  {projDetailLoading && !openProject && (
                    <div className="folder-loading">
                      <InfinityLoader w={64} h={40} />
                    </div>
                  )}
                  <button className="folder-new-chat-btn" onClick={() => newProjChat(p.project_id)}>
                    <span className="fnc-plus">+</span>
                    <span className="fnc-text">New chat in {p.name}</span>
                  </button>

                  <div className="inner-tabs">
                    <button className={`inner-tab ${projInnerTab === 'chats' ? 'active' : ''}`} onClick={() => setProjInnerTab('chats')}>Chats</button>
                    <button className={`inner-tab ${projInnerTab === 'sources' ? 'active' : ''}`} onClick={() => setProjInnerTab('sources')}>Studies</button>
                  </div>

                  {projInnerTab === 'chats' && (openProject?.chats || []).length === 0 && (
                    <div className="folder-empty">No chats yet.</div>
                  )}
                  {projInnerTab === 'chats' && (openProject?.chats || []).map(c => (
                    <div
                      key={c.chat_id}
                      className={`chat-row ${view.chatId === c.chat_id ? 'active' : ''}`}
                      onClick={() => openProjChat(p.project_id, c.chat_id)}
                    >
                      <div className="cr-content">
                        {editingChatId === c.chat_id ? (
                          <input className="cr-title-input" value={editChatVal} autoFocus
                            onChange={e => setEditChatVal(e.target.value)}
                            onBlur={() => saveRenameChat('proj', p.project_id, c.chat_id)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') { e.preventDefault(); saveRenameChat('proj', p.project_id, c.chat_id); }
                              if (e.key === 'Escape') setEditingChatId(null);
                            }}
                            onClick={e => e.stopPropagation()} />
                        ) : (
                          <div className="cr-title editable" onClick={e => {
                            e.stopPropagation();
                            setEditingChatId(c.chat_id);
                            setEditChatVal(chatCache[c.chat_id]?.title || c.title || 'New chat');
                          }}>
                            {chatCache[c.chat_id]?.title || c.title || 'New chat'}
                            <span className="rename-hint">✎</span>
                          </div>
                        )}
                        {c.updated_at && (
                          <div className="cr-date">
                            {formatDate(c.updated_at)}
                          </div>
                        )}
                      </div>
                      <button className="cr-del" onClick={e => { e.stopPropagation(); deleteProjChat(p.project_id, c.chat_id); }}>×</button>
                    </div>
                  ))}

                  {projInnerTab === 'sources' && (openProject?.studies || []).length > 0 && (
                    <div className="sources-tab-header">
                      <button className="folder-refresh-btn" title="Refresh sample/prep data from Qiita"
                        onClick={e => { e.stopPropagation(); enrichAllStudies(p.project_id); }}>
                        ↻ Refresh Data
                      </button>
                    </div>
                  )}

                  {projInnerTab === 'sources' && (openProject?.studies || []).length === 0 && (
                    <div className="folder-empty">No studies yet. Use Browse to add some.</div>
                  )}
                  {projInnerTab === 'sources' && (openProject?.studies || []).map(s => (
                    <div key={s.study_id} className="chat-row" onClick={() => openStudyModal(s)}>
                      <div className="cr-content">
                        <div className="cr-title">{s.study_title || 'Untitled'}</div>
                        <div className="cr-date">ID {s.study_id}</div>
                      </div>
                      <button className="cr-del" onClick={e => { e.stopPropagation(); removeStudy(s.study_id); }}>×</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}

          {!showNewProj ? (
            <button className="new-proj-btn" onClick={() => setShowNewProj(true)}>+ New Workspace</button>
          ) : (
            <div className="new-proj-form">
              <input
                className="new-proj-input"
                placeholder="Project name…"
                value={newProjName}
                onChange={e => setNewProjName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') createProject(); if (e.key === 'Escape') { setShowNewProj(false); setNewProjName(''); } }}
                autoFocus
              />
              <div className="new-proj-actions">
                <button className="npb-create" onClick={createProject}>Create</button>
                <button className="npb-cancel" onClick={() => { setShowNewProj(false); setNewProjName(''); }}>Cancel</button>
              </div>
            </div>
          )}

          <div className="sb-label" style={{ marginTop: 24 }}>Global Chats</div>
          <button className="new-proj-btn" onClick={newGlobChat}>+ New Global Chat</button>
          {globalChats.map(c => (
            <div
              key={c.chat_id}
              className={`chat-row flat ${view.type === 'global-chat' && view.chatId === c.chat_id ? 'active' : ''}`}
              onClick={() => openGlobChat(c.chat_id)}
            >
              <div className="cr-content">
                {editingChatId === c.chat_id ? (
                  <input className="cr-title-input" value={editChatVal} autoFocus
                    onChange={e => setEditChatVal(e.target.value)}
                    onBlur={() => saveRenameChat('global', null, c.chat_id)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') { e.preventDefault(); saveRenameChat('global', null, c.chat_id); }
                      if (e.key === 'Escape') setEditingChatId(null);
                    }}
                    onClick={e => e.stopPropagation()} />
                ) : (
                  <div className="cr-title editable" onClick={e => {
                    e.stopPropagation();
                    setEditingChatId(c.chat_id);
                    setEditChatVal(chatCache[c.chat_id]?.title || c.title || 'New chat');
                  }}>
                    {chatCache[c.chat_id]?.title || c.title || 'New chat'}
                    <span className="rename-hint">✎</span>
                  </div>
                )}
                {c.updated_at && (
                  <div className="cr-date">
                    {formatDate(c.updated_at)}
                  </div>
                )}
              </div>
              <button className="cr-del" onClick={e => { e.stopPropagation(); deleteGlobChat(c.chat_id); }}>×</button>
            </div>
          ))}
        </div>

      </aside>

      {/* ══════════════════ MAIN ══════════════════════ */}
      <div className={`main${(showMergePanel || resultsDrawerOpen) ? ' merge-open' : ''}`}>

        <div className={`topbar${hasSourcesBar ? ' has-sources-bar' : ''}`}>
          {(view.type === 'browse' || view.type === 'merges') ? (
            <>
              <button className={`topbar-nav${view.type === 'browse' ? ' active' : ''}`}
                onClick={() => { setView({ type: 'browse' }); setSidebarCollapsed(false); }}>Browse Studies</button>
              {SHOW_MERGES && (
                <button className={`topbar-nav${view.type === 'merges' ? ' active' : ''}`}
                  onClick={() => { setView({ type: 'merges' }); setSidebarCollapsed(true); }}>Merges</button>
              )}
              {view.type === 'merges' && (
                <button
                  className="sidebar-toggle-btn"
                  title={sidebarCollapsed ? 'Show sidebar' : 'Hide sidebar'}
                  onClick={() => setSidebarCollapsed(c => !c)}
                ><ChevronIcon dir={sidebarCollapsed ? 'right' : 'left'} /></button>
              )}
            </>
          ) : (
            <>
              {isChat && view.chatId ? (
                <ChatTitleEditor title={topTitle} onSave={renameChat} />
              ) : (
                <span className="topbar-title">{topTitle}</span>
              )}
              {view.type === 'project-chat' && openProject?.studies?.length > 0 && (
                <span className="topbar-badge">{openProject.studies.length} sources</span>
              )}
              {isChat && view.chatId && (
                <CopyResponseButton
                  title="Copy chat link"
                  text={window.location.origin + window.location.pathname + buildHash(view, null)}
                />
              )}
            </>
          )}
          <span className="topbar-spacer" />
          {SHOW_MERGES && view.type !== 'merges' && (
            <button className={`merge-toggle-btn ${showMergePanel ? 'active' : ''}`}
              title="Merge Workspace"
              onClick={() => openMergePanel(!showMergePanel)}>
              <MergeIcon /> Merge
            </button>
          )}
          <AccountBar
            identity={account.identity}
            onLogout={account.onLogout}
            theme={theme}
            onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')} />
        </div>

        {/* Sibling of .topbar, NOT a child of .content — .content is the scroll
            container and its width moves with its scrollbar and the merge
            panel's padding, so a bar inside it can neither stay flush with the
            pill nor stay put while you scroll. */}
        {hasProjectSources && (
          <div className="sources-bar">
            <span className="sources-label">Studies</span>
            {(openProject.studies||[]).map(s => (
              <button key={s.study_id} className="src-chip" onClick={() => openStudyModal(s)}>
                {(s.study_title||'Untitled').slice(0, CHIP_TITLE_MAX)}
              </button>
            ))}
          </div>
        )}
        {hasGlobalPins && (
          <div className="sources-bar">
            <span className="sources-label">Pinned</span>
            {pinnedMeta.map(p => (
              <button key={p.study_id} className="src-chip removable"
                title={p.study_title || `Study ${p.study_id}`}
                onClick={() => unpinStudy(view.chatId, p.study_id)}>
                {pinChipLabel(p)}
              </button>
            ))}
          </div>
        )}

        <div className="content">

          {/* ── MERGES ── */}
          {view.type === 'merges' && (
            <div className="merges-layout">
              <div className="merges-list-col">
                <MergesTab
                  onOpenWorkspace={(wsId) => setMergeWorkspaceId(wsId)}
                  activeWorkspaceId={mergeWorkspaceId}
                />
              </div>
              <div className="merges-detail-col">
                {mergeWorkspaceId
                  ? <MergeWorkspacePanel
                      key={mergeWorkspaceId}
                      workspaceId={mergeWorkspaceId}
                      setWorkspaceId={setMergeWorkspaceId}
                      pendingStudy={pendingMergeStudy}
                      clearPendingStudy={() => setPendingMergeStudy(null)}
                      onClose={() => setMergeWorkspaceId(null)}
                      embedded
                    />
                  : <div className="merges-detail-empty">Open a workspace to view it here</div>
                }
              </div>
            </div>
          )}

          {/* ── BROWSE ── */}
          {view.type === 'browse' && (
            <div className="browse-panel">
              <div className="browse-search-row">
                <input
                  className="browse-input"
                  placeholder="Search by keyword, author, or topic…"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && doSearch()}
                />
                <button className="btn-search" onClick={() => doSearch()} disabled={searching || !query.trim()}>
                  {searching ? '…' : 'Search'}
                </button>
                {searched && (
                  <button className="btn-clear" onClick={() => { setQuery(''); setResults([]); setSearched(false); setSqlQuery(null); setAppliedFilters(null); }}>
                    Clear
                  </button>
                )}
              </div>

              <div className="browse-chips">
                {['soil microbiome','gut bacteria','ocean samples','Rob Knight','UC San Diego','16S rRNA'].map(q => (
                  <button key={q} className="browse-chip" onClick={() => doSearch(q)}>{q}</button>
                ))}
              </div>


              {(sqlQuery || appliedFilters?.pi) && (
                <>
                  {appliedFilters?.pi && <PiFilterLine pi={appliedFilters.pi} />}
                  <label className="sql-toggle">
                    <input type="checkbox" checked={showSql} onChange={e => setShowSql(e.target.checked)} />
                    Show generated SQL
                  </label>
                  {showSql && sqlQuery && <div className="sql-block">WHERE {sqlQuery.where_clause}</div>}
                </>
              )}

              {searching && <div className="state-loading"><HelixLoader w={160} h={80} /><br />Deep searching sample metadata…</div>}

              {!searching && (
                <>
                  <div className="browse-count">{searched ? `${results.length} results` : 'GOLD studies'}</div>
                  {addStudyErr && <div className="browse-error">{addStudyErr}</div>}
                  {searched && results.length === 0 && <div className="state-empty">No studies matched your search.</div>}
                  <div className="studies-grid">
                    {displayStudies.map(study => {
                      const inProj = projStudyIds.includes(study.study_id);
                      const inCtx  = ctxStudyIds.includes(study.study_id);
                      const dataTypeList = splitTypes(study.data_types);
                      const metaParts = [
                        study.num_samples != null ? `${study.num_samples} samples` : null,
                        study.num_preps    != null ? `${study.num_preps} preps`    : null,
                      ].filter(Boolean);
                      return (
                        <div key={study.study_id} className="study-card" onClick={() => openStudyModal(study)}>
                          <div className="study-card-top">
                            <div style={{display:'flex',gap:'6px',alignItems:'center'}}>
                              <span className="study-id-badge">ID {study.study_id}</span>
                              {study.is_gold && <span className="gold-badge">GOLD</span>}
                            </div>
                            <div className="study-card-actions" onClick={e => e.stopPropagation()}>
                              {openProjId ? (
                                <button className="btn-card-add" disabled={inProj} onClick={() => addStudyToProject(study)}>
                                  {inProj ? '✓ Saved' : '+ Add to Project'}
                                </button>
                              ) : (
                                <button className={`btn-card-ctx ${inCtx ? 'on' : ''}`}
                                  onClick={() => setCtxStudies(prev =>
                                    inCtx ? prev.filter(s => s.study_id !== study.study_id) : [...prev, study])}>
                                  {inCtx ? '✓ Pinned' : '+ Pin'}
                                </button>
                              )}
                              {SHOW_MERGES && (
                                <button className="btn-card-merge"
                                  onClick={() => {
                                    setPendingMergeStudy(study);
                                    openMergePanel(true);
                                  }}>
                                  + Merge
                                </button>
                              )}
                            </div>
                          </div>
                          <div className="study-card-title">{study.study_title || 'Untitled study'}</div>
                          <div className="study-card-abstract">{study.study_abstract || 'No abstract available.'}</div>
                          {dataTypeList.length > 0 && (
                            <div className="study-card-types">
                              {dataTypeList.map(t => <span key={t} className="dtype-chip">{t}</span>)}
                            </div>
                          )}
                          {metaParts.length > 0 && (
                            <div className="study-card-meta">{metaParts.join(' · ')}</div>
                          )}
                          {(study.pi_name || study.pi_affiliation) && (
                            <div className="study-card-pi">
                              {[study.pi_name, study.pi_affiliation].filter(Boolean).join(' · ')}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          )}

          {/* ── CHAT ── */}
          {isChat && (
            <>
              <div className="chat-messages">
                {activeMsgs.length === 0 && chatLoading ? (
                  <div className="state-loading"><InfinityLoader w={100} h={62} /></div>
                ) : activeMsgs.length === 0 ? (
                  <div className="chat-empty">
                    <div className="chat-empty-title">
                      {view.type === 'project-chat'
                        ? `Chat with ${s.projects.find(p => p.project_id === view.projId)?.name || 'Project'}`
                        : 'Global Chat'}
                    </div>
                    <p className="chat-empty-sub">
                      {view.type === 'project-chat'
                        ? `Ask anything about your ${openProject?.studies?.length || 0} saved studies.`
                        : 'Each message runs a database search. Add optional context chips from Browse to highlight specific studies alongside search results.'}
                    </p>
                    <div className="chat-empty-chips">
                      {[
                        { label: 'What are the key themes?' },
                        { label: 'Who are the PIs?' },
                        { label: 'Summarize the abstracts' },
                        { label: 'What sample types were used?' },
                        { label: '/report 104 - Full study report', send: '/report 104' },
                        { label: '/systems — Check model status', send: '/systems' },
                      ].map(chip => (
                          <button key={chip.label} className="chat-starter" onClick={() => {
                            if (chip.send) sendMessage(chip.send);
                            else { setInput(chip.label); s.taRef.current?.focus(); }
                          }}>{chip.label}</button>
                        ))}
                    </div>
                  </div>
                ) : (
                  activeMsgs.map((m, i) => (
                    <div key={i} className={`msg-row ${m.role}${m.ui?.kind === 'samples_report' ? ' article' : ''}`}>
                      {m.role === 'assistant' && (m.segments !== null || m.ui?.kind === 'agent_segments') ? (
                        <AgentMessageBubble
                          segments={m.segments ?? m.ui?.segments ?? []}
                          isStreaming={!!m.isStreaming}
                          msgKey={`${view.chatId}-${i}`}
                          onPinStudy={study => pinStudy(view.chatId, study)}
                          onMergeStudy={study => { setPendingMergeStudy(study); openMergePanel(true); }}
                          onOpenStudy={openStudyModal}
                          onViewAllStudies={openSearchResultsPanel}
                          pinnedStudyIds={pinnedMeta.map(p => p.study_id)} />
                      ) : m.role === 'assistant' && m.ui?.kind === 'samples_report' ? (
                        <SamplesReportBubble ui={m.ui} messageKey={`${view.chatId}-${i}`} />
                      ) : m.role === 'assistant' && m.ui?.kind === 'systems_status' ? (
                        <SystemsStatusBubble ui={m.ui} />
                      ) : m.role === 'assistant' ? (
                        <>
                          {(m.steps?.length > 0 || m.pendingStep) && (
                            <div className="msg-steps">
                              {(m.steps || []).map((step, si) => (
                                <div key={si} className="msg-step-done">
                                  <span className="step-dot" />
                                  <span className="step-label">{step.label}</span>
                                  {step.detail && <span className="step-detail"> · {step.detail}</span>}
                                </div>
                              ))}
                              {m.pendingStep && (
                                <div className="msg-step-pending">
                                  <div className="step-spinner" />
                                  <span className="step-label">{m.pendingStep.label}</span>
                                </div>
                              )}
                            </div>
                          )}
                          {m.isStreaming && !m.content && !m.steps?.length && !m.pendingStep ? (
                            <InfinityLoader w={80} h={50} />
                          ) : (!m.isStreaming || m.content) ? (
                            <>
                              <div
                                className={`msg-bubble${m.isStreaming ? ' streaming' : ''}`}
                                dangerouslySetInnerHTML={{
                                  __html: renderMarkdown(m.content || (!m.isStreaming ? '*No response*' : ''))
                                }}
                              />
                              {m.isStreaming && m.content && <div style={{marginTop:'6px'}}><InfinityLoader w={64} h={40} /></div>}
                              {!m.isStreaming && m.content && <CopyResponseButton text={m.content} />}
                            </>
                          ) : null}
                        </>
                      ) : (
                        <div className="msg-bubble">{m.content}</div>
                      )}
                    </div>
                  ))
                )}
                <div ref={bottomRef} />
              </div>
            </>
          )}

          {view.type === 'project-chat' && !view.chatId && !isChat && (
            <div className="chat-empty" style={{paddingTop:60}}>
              <div className="chat-empty-title">Select a chat</div>
              <p className="chat-empty-sub">Pick a chat from the sidebar or start a new one.</p>
            </div>
          )}
        </div>

        {/* Composer */}
        {view.type !== 'merges' && <div className="composer-wrap">
          {showModelPicker && (
            <ModelPickerCard
              current={selectedModel}
              anthropicKeySet={anthropicKeySet}
              onPick={(model, keySaved) => {
                setSelectedModel(model);
                if (keySaved) setAnthropicKeySet(true);
                setShowModelPicker(false);
              }}
              onClose={() => setShowModelPicker(false)}
            />
          )}
          {isChat && view.chatId && (
            <PinnedBar
              studies={pinnedMeta}
              onRemove={sid => unpinStudy(view.chatId, sid)}
              onOpen={openStudyModal}
              hint={(() => {
                const total = chatCache[view.chatId]?.totalStudiesInProject;
                return (total != null && total > pinnedMeta.length)
                  ? <span className="composer-pins-hint">{pinnedMeta.length} of {total} studies in context</span>
                  : null;
              })()} />
          )}
          {view.type === 'browse' && (
            <PinnedBar studies={ctxStudies} onRemove={id => setCtxStudies(prev => prev.filter(s => s.study_id !== id))} />
          )}
          {slashMatches.length > 0 && !slashDismissed && (
            <div className="slash-menu">
              <SlashCommandMenu matches={slashMatches} activeIndex={slashIndex} onPick={completeSlash} />
            </div>
          )}
          <div className={`composer ${!(isChat || view.type === 'browse') ? 'muted' : ''}`}>
            <textarea
              ref={taRef}
              className="composer-ta"
              rows={1}
              placeholder={(() => {
                if (!(isChat || view.type === 'browse')) return 'Open a chat to start messaging';
                const pinned = pinnedMeta.length;
                if (pinned > 0) return `Ask about ${pinned} pinned stud${pinned === 1 ? 'y' : 'ies'}…`;
                return 'use "/" for commands, ask or search for studies';
              })()}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                const menuOpen = slashMatches.length > 0 && !slashDismissed;
                if (menuOpen) {
                  if (e.key === 'ArrowDown') { e.preventDefault(); setSlashIndex(i => Math.min(i + 1, slashMatches.length - 1)); return; }
                  if (e.key === 'ArrowUp')   { e.preventDefault(); setSlashIndex(i => Math.max(i - 1, 0)); return; }
                  if (e.key === 'Tab' || e.key === 'Enter') {
                    e.preventDefault();
                    completeSlash(slashMatches[slashIndex]);
                    return;
                  }
                  if (e.key === 'Escape')    { e.preventDefault(); setSlashDismissed(true); return; }
                }
                if (e.key === 'Enter' && !e.shiftKey && (isChat || view.type === 'browse')) { e.preventDefault(); sendMessage(); }
              }}
              disabled={!(isChat || view.type === 'browse') || sending}
            />
            <div className="composer-actions">
              <div style={{position:'relative'}}>
                <button className="composer-plus"
                        onClick={() => setShowPlusMenu(v => !v)}
                        title="Quick actions">+</button>
                {showPlusMenu && (
                  <PlusMenu
                    onClose={() => setShowPlusMenu(false)}
                    onCompleteSlash={cmd => { completeSlash(cmd); setShowPlusMenu(false); }}
                    onOpenModelPicker={() => { setShowModelPicker(true); setShowPlusMenu(false); }}
                  />
                )}
              </div>
              <span style={{flex:1}} />
              <span className="composer-model-chip"
                    onClick={() => setShowModelPicker(v => !v)}
                    title="Click or type /model to change">
                {selectedModel}
              </span>
              <button className="composer-send" onClick={sendMessage} disabled={!canSend}>↑</button>
            </div>
          </div>
          {compErr && <div className="composer-error">{compErr}</div>}
        </div>}
      </div>

      {/* ══════════════════ MODAL ══════════════════════ */}
      {modalStudy && (
        <StudyModal study={modalStudy} detail={modalDetail}
          loading={modalDetailLoading} onClose={closeModal}
          drawerOpen={!!(showMergePanel || resultsDrawerOpen)}
          shareUrl={window.location.origin + window.location.pathname + buildHash(view, modalStudy.study_id)} />
      )}
      {showMergePanel && (
        <MergeWorkspacePanel
          workspaceId={mergeWorkspaceId}
          setWorkspaceId={setMergeWorkspaceId}
          pendingStudy={pendingMergeStudy}
          clearPendingStudy={() => setPendingMergeStudy(null)}
          onClose={() => openMergePanel(false)}
        />
      )}
      {searchResultsPanel && (
        <SearchResultsPanel
          studies={searchResultsPanel.studies}
          title={searchResultsPanel.title}
          detail={searchResultsPanel.detail}
          closing={searchResultsClosing}
          onClose={closeSearchResultsPanel}
          onExited={finishCloseSearchResultsPanel}
          onPin={study => view.chatId && pinStudy(view.chatId, study)}
          onMerge={study => { setPendingMergeStudy(study); openMergePanel(true); }}
          onOpen={openStudyModal}
          isPinned={sid => pinnedMeta.some(p => p.study_id === sid)}
        />
      )}
    </div>
  );
}
