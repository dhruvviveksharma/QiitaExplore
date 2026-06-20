function renderApp(s) {
  const {
    setView, setOpenProjId, setProjInnerTab, setShowNewProj, setNewProjName,
    setQuery, setResults, setSearched, setSqlQuery, setShowSql, setDeepSearch,
    setCtxStudies, setInput, setSelectedModel, setTheme,
    setSlashIndex, setSlashDismissed,
    setMergeWorkspaceId, setShowMergePanel, setPendingMergeStudy,
    setShowModelPicker, setAnthropicKeySet, setSidebarCollapsed,
    projects, projLoading, openProjId, openProject, view,
    chatCache, globalChats, projInnerTab,
    query, results, firstStudies, searching, searched, sqlQuery, showSql, deepSearch,
    ctxStudies, showNewProj, newProjName, mergeWorkspaceId, showMergePanel, pendingMergeStudy, sidebarCollapsed,
    input, sending, compErr, selectedModel, theme,
    slashIndex, slashDismissed, showModelPicker, anthropicKeySet,
    modalStudy, modalDetail, modalDetailLoading,
    projDetailLoading, chatLoading,
    taRef, bottomRef,
    createProject, deleteProject, addStudyToProject, removeStudy,
    openProjChat, openGlobChat, newProjChat, deleteProjChat, newGlobChat, deleteGlobChat,
    unpinStudy, sendMessage, openStudyModal, closeModal, enrichAllStudies, doSearch,
    removeCtxStudyFromChat, completeSlash,
    projStudyIds, ctxStudyIds, displayStudies, isChat, canSend, topTitle,
    activeMsgs, slashMatches,
  } = s;

  return (
    <div className={`app${theme === 'dark' ? ' dark' : ''}`}>

      {/* ══════════════════ SIDEBAR ══════════════════ */}
      <aside className={`sidebar${sidebarCollapsed ? ' collapsed' : ''}`}>
        <div className="sidebar-header">
          <div className="app-logo app-logo-home" onClick={() => setView({ type: 'browse' })}>Qiita<span>Explorer</span></div>
        </div>

        <div className="sidebar-body">

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
                <span className="folder-caret">{openProjId === p.project_id ? '▾' : '▸'}</span>
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
                      <div className="typing-dots"><span/><span/><span/></div>
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
                        <div className="cr-title">{chatCache[c.chat_id]?.title || c.title || 'New chat'}</div>
                        {c.updated_at && (
                          <div className="cr-date">
                            {new Date(c.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
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
                <div className="cr-title">{chatCache[c.chat_id]?.title || c.title || 'New chat'}</div>
                {c.updated_at && (
                  <div className="cr-date">
                    {new Date(c.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                  </div>
                )}
              </div>
              <button className="cr-del" onClick={e => { e.stopPropagation(); deleteGlobChat(c.chat_id); }}>×</button>
            </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <button
            className={`browse-btn ${view.type === 'browse' ? 'active' : ''}`}
            onClick={() => setView({ type: 'browse' })}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{marginRight:6}}>
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            Browse Studies
          </button>
        </div>
      </aside>

      {/* ══════════════════ MAIN ══════════════════════ */}
      <div className="main">

        <div className="topbar">
          {(view.type === 'browse' || view.type === 'merges') ? (
            <>
              <button className={`topbar-nav${view.type === 'browse' ? ' active' : ''}`}
                onClick={() => { setView({ type: 'browse' }); setSidebarCollapsed(false); }}>Browse Studies</button>
              <button className={`topbar-nav${view.type === 'merges' ? ' active' : ''}`}
                onClick={() => { setView({ type: 'merges' }); setSidebarCollapsed(true); }}>Merges</button>
              {view.type === 'merges' && (
                <button
                  className="sidebar-toggle-btn"
                  title={sidebarCollapsed ? 'Show sidebar' : 'Hide sidebar'}
                  onClick={() => setSidebarCollapsed(c => !c)}
                >{sidebarCollapsed ? '▶' : '◀'}</button>
              )}
            </>
          ) : (
            <>
              <span className="topbar-title">{topTitle}</span>
              {view.type === 'project-chat' && openProject?.studies?.length > 0 && (
                <span className="topbar-badge">{openProject.studies.length} sources</span>
              )}
            </>
          )}
          <span className="topbar-spacer" />
          {view.type !== 'merges' && (
            <button className={`merge-toggle-btn ${showMergePanel ? 'active' : ''}`}
              title="Merge Workspace"
              onClick={() => setShowMergePanel(p => !p)}>
              ⊕ Merge
            </button>
          )}
          <button className="theme-toggle"
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? '☀' : '☽'}
          </button>
        </div>

        <div className={`content${showMergePanel ? ' merge-open' : ''}`}>

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
                <button
                  className={`btn-deepsearch${deepSearch ? ' active' : ''}`}
                  onClick={() => setDeepSearch(v => !v)}
                  title="Also scan sample metadata (slower)"
                >⚡ Deep</button>
                {searched && (
                  <button className="btn-clear" onClick={() => { setQuery(''); setResults([]); setSearched(false); setSqlQuery(null); }}>
                    Clear
                  </button>
                )}
              </div>

              <div className="browse-chips">
                {['soil microbiome','gut bacteria','ocean samples','Rob Knight','UC San Diego','16S rRNA'].map(q => (
                  <button key={q} className="browse-chip" onClick={() => doSearch(q)}>{q}</button>
                ))}
              </div>


              {sqlQuery && (
                <>
                  <label className="sql-toggle">
                    <input type="checkbox" checked={showSql} onChange={e => setShowSql(e.target.checked)} />
                    Show generated SQL
                  </label>
                  {showSql && <div className="sql-block">WHERE {sqlQuery.where_clause}</div>}
                </>
              )}

              {searching && <div className="state-loading"><div className="spinner" /><br />{deepSearch ? 'Deep searching sample metadata…' : 'Searching…'}</div>}

              {!searching && (
                <>
                  <div className="browse-count">{searched ? `${results.length} results` : 'GOLD studies'}</div>
                  {searched && results.length === 0 && <div className="state-empty">No studies matched your search.</div>}
                  <div className="studies-grid">
                    {displayStudies.map(study => {
                      const inProj = projStudyIds.includes(study.study_id);
                      const inCtx  = ctxStudyIds.includes(study.study_id);
                      const dataTypeList = (study.data_types || '').split(',').map(t => t.trim()).filter(Boolean);
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
                              <button className="btn-card-merge"
                                onClick={() => {
                                  setPendingMergeStudy(study);
                                  setShowMergePanel(true);
                                }}>
                                + Merge
                              </button>
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
              {view.type === 'project-chat' && openProject?.studies?.length > 0 && (
                <div className="sources-bar">
                  <span className="sources-label">Studies</span>
                  {(openProject.studies||[]).map(s => (
                    <button key={s.study_id} className="src-chip" onClick={() => openStudyModal(s)}>
                      {(s.study_title||'Untitled').slice(0,40)}
                    </button>
                  ))}
                </div>
              )}
              {view.type === 'global-chat' && (chatCache[view.chatId]?.ctxStudies || []).length > 0 && (
                <div className="sources-bar">
                  <span className="sources-label">Context</span>
                  <span className="sources-hint">Merged with DB search on send</span>
                  {(chatCache[view.chatId]?.ctxStudies || []).map(s => (
                    <button key={s.study_id} className="src-chip removable"
                      onClick={() => removeCtxStudyFromChat(view.chatId, s.study_id)}>
                      {(s.study_title||'Untitled').slice(0,40)} ×
                    </button>
                  ))}
                </div>
              )}

              <div className={`chat-messages${activeMsgs.some(m => m.ui?.kind === 'samples_report') ? ' chat-messages-wide' : ''}`}>
                {activeMsgs.length === 0 && chatLoading ? (
                  <div className="state-loading"><div className="spinner" /></div>
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
                      {['What are the key themes?','Who are the PIs?','Summarize the abstracts','What sample types were used?','/report 104 - Full study report','/systems — Check model status']
                        .map(q => (
                          <button key={q} className="chat-starter" onClick={() => { setInput(q); s.taRef.current?.focus(); }}>{q}</button>
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
                          msgKey={`${view.chatId}-${i}`} />
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
                          {m.queryPlan && (
                            <div className="msg-query-plan">
                              <span className="qp-icon">⌕</span>
                              <span className="qp-desc">{m.queryPlan.description}</span>
                              {(m.queryPlan.keywords || []).length > 0 && (
                                <span className="qp-keywords">
                                  {m.queryPlan.keywords.map(k => (
                                    <span key={k} className="qp-kw-chip">{k}</span>
                                  ))}
                                </span>
                              )}
                              <span className="qp-mode">{m.queryPlan.match_mode}</span>
                            </div>
                          )}
                          {m.queryPlan?.sql_where && (
                            <details className="msg-sql-details">
                              <summary className="msg-sql-summary">Show SQL</summary>
                              <div className="sql-block">WHERE {m.queryPlan.sql_where}</div>
                            </details>
                          )}
                          {m.isStreaming && !m.content && !m.steps?.length && !m.pendingStep ? (
                            <div className="msg-bubble"><div className="typing-dots"><span/><span/><span/></div></div>
                          ) : (!m.isStreaming || m.content) ? (
                            <div
                              className={`msg-bubble${m.isStreaming ? ' streaming' : ''}`}
                              dangerouslySetInnerHTML={{
                                __html: DOMPurify.sanitize(marked.parse(m.content || (!m.isStreaming ? '*No response*' : '')))
                              }}
                            />
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
          {isChat && view.chatId && (chatCache[view.chatId]?.pinnedStudies || []).length > 0 && (
            <div className="composer-pins">
              <span className="composer-pins-label">Pinned:</span>
              {(chatCache[view.chatId]?.pinnedStudies || []).map(sid => (
                <span key={sid} className="composer-pin-chip">
                  Study {sid}
                  <button className="composer-pin-x" title="Unpin" onClick={() => unpinStudy(view.chatId, sid)}>×</button>
                </span>
              ))}
              {(() => {
                const cur    = chatCache[view.chatId] || {};
                const pinned = (cur.pinnedStudies || []).length;
                const total  = cur.totalStudiesInProject;
                return (total != null && total > pinned)
                  ? <span className="composer-pins-hint">{pinned} of {total} studies in context</span>
                  : null;
              })()}
            </div>
          )}
          {view.type === 'browse' && (
            <PinnedBar studies={ctxStudies} onRemove={id => setCtxStudies(prev => prev.filter(s => s.study_id !== id))} />
          )}
          {slashMatches.length > 0 && !slashDismissed && (
            <SlashCommandMenu matches={slashMatches} activeIndex={slashIndex} onPick={completeSlash} />
          )}
          <div className={`composer ${!(isChat || view.type === 'browse') ? 'muted' : ''}`}>
            <textarea
              ref={taRef}
              className="composer-ta"
              rows={1}
              placeholder={(isChat || view.type === 'browse') ? 'Message…' : 'Open a chat to start messaging'}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                const menuOpen = slashMatches.length > 0 && !slashDismissed;
                if (menuOpen) {
                  if (e.key === 'ArrowDown') { e.preventDefault(); setSlashIndex(i => Math.min(i + 1, slashMatches.length - 1)); return; }
                  if (e.key === 'ArrowUp')   { e.preventDefault(); setSlashIndex(i => Math.max(i - 1, 0)); return; }
                  if (e.key === 'Tab')       { e.preventDefault(); completeSlash(slashMatches[slashIndex]); return; }
                  if (e.key === 'Escape')    { e.preventDefault(); setSlashDismissed(true); return; }
                }
                if (e.key === 'Enter' && !e.shiftKey && (isChat || view.type === 'browse')) { e.preventDefault(); sendMessage(); }
              }}
              disabled={!(isChat || view.type === 'browse') || sending}
            />
            <button className="composer-send" onClick={sendMessage} disabled={!canSend}>↑</button>
          </div>
          <div className="composer-model" onClick={() => setShowModelPicker(v => !v)}
               title="Click or type /model to change" style={{cursor:'pointer'}}>
            <span className="composer-model-label">Model:</span>
            <span className="composer-model-name">{selectedModel}</span>
          </div>
          {compErr && <div className="composer-error">{compErr}</div>}
        </div>}
      </div>

      {/* ══════════════════ MODAL ══════════════════════ */}
      {modalStudy && (
        <StudyModal study={modalStudy} detail={modalDetail}
          loading={modalDetailLoading} onClose={closeModal} />
      )}
      {showMergePanel && (
        <MergeWorkspacePanel
          workspaceId={mergeWorkspaceId}
          setWorkspaceId={setMergeWorkspaceId}
          pendingStudy={pendingMergeStudy}
          clearPendingStudy={() => setPendingMergeStudy(null)}
          onClose={() => setShowMergePanel(false)}
        />
      )}
    </div>
  );
}
