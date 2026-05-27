function renderApp(s) {
  const {
    setView, setOpenProjId, setProjInnerTab, setShowNewProj, setNewProjName,
    setQuery, setResults, setSearched, setSqlQuery, setShowSql,
    setCtxStudies, setInput, setSelectedModel,
    projects, projLoading, openProjId, openProject, view,
    chatCache, globalChats, projInnerTab,
    query, results, firstStudies, searching, searched, sqlQuery, showSql,
    ctxStudies, showNewProj, newProjName,
    input, sending, compErr, selectedModel,
    modalStudy, modalDetail, modalDetailLoading,
    projDetailLoading, chatLoading,
    taRef, bottomRef,
    createProject, deleteProject, addStudyToProject, removeStudy,
    openProjChat, openGlobChat, newProjChat, deleteProjChat, newGlobChat, deleteGlobChat,
    unpinStudy, sendMessage, openStudyModal, closeModal, enrichAllStudies, doSearch,
    projStudyIds, ctxStudyIds, displayStudies, isChat, canSend, topTitle,
    activeMsgs,
  } = s;

  return (
    <div className="app">

      {/* ══════════════════ SIDEBAR ══════════════════ */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="app-logo app-logo-home" onClick={() => setView({ type: 'browse' })}>Qiita<span>Explorer</span></div>
        </div>

        <div className="sidebar-body">

          <div className="sb-label">Projects</div>
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
                    <button className={`inner-tab ${projInnerTab === 'sources' ? 'active' : ''}`} onClick={() => setProjInnerTab('sources')}>Sources</button>
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
            <button className="new-proj-btn" onClick={() => setShowNewProj(true)}>+ New Project</button>
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
          <span className="topbar-title">{topTitle}</span>
          {view.type === 'project-chat' && openProject?.studies?.length > 0 && (
            <span className="topbar-badge">{openProject.studies.length} sources</span>
          )}
        </div>

        <div className="content">

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

              {!openProjId && ctxStudies.length > 0 && (
                <div className="ctx-bar">
                  <span className="ctx-label">Global chat context</span>
                  <span className="ctx-hint">Searches Qiita on every message; chips add these studies as extra context. Remove all chips for search-only.</span>
                  {ctxStudies.map(s => (
                    <button key={s.study_id} className="ctx-chip"
                      onClick={() => setCtxStudies(prev => prev.filter(x => x.study_id !== s.study_id))}>
                      {(s.study_title||'Untitled').slice(0,32)} ×
                    </button>
                  ))}
                </div>
              )}

              {sqlQuery && (
                <>
                  <label className="sql-toggle">
                    <input type="checkbox" checked={showSql} onChange={e => setShowSql(e.target.checked)} />
                    Show generated SQL
                  </label>
                  {showSql && <div className="sql-block">WHERE {sqlQuery.where_clause}</div>}
                </>
              )}

              {searching && <div className="state-loading"><div className="spinner" /><br />Searching…</div>}

              {!searching && (
                <>
                  <div className="browse-count">{searched ? `${results.length} results` : 'First 20 studies'}</div>
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
                            <span className="study-id-badge">ID {study.study_id}</span>
                            <div className="study-card-actions" onClick={e => e.stopPropagation()}>
                              {openProjId ? (
                                <button className="btn-card-add" disabled={inProj} onClick={() => addStudyToProject(study)}>
                                  {inProj ? '✓ Saved' : '+ Add to Project'}
                                </button>
                              ) : (
                                <button className={`btn-card-ctx ${inCtx ? 'on' : ''}`}
                                  onClick={() => setCtxStudies(prev =>
                                    inCtx ? prev.filter(s => s.study_id !== study.study_id) : [...prev, study])}>
                                  {inCtx ? '✓ Context' : '+ Context'}
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
              {view.type === 'project-chat' && openProject?.studies?.length > 0 && (
                <div className="sources-bar">
                  <span className="sources-label">Sources</span>
                  {(openProject.studies||[]).map(s => (
                    <button key={s.study_id} className="src-chip" onClick={() => openStudyModal(s)}>
                      {(s.study_title||'Untitled').slice(0,40)}
                    </button>
                  ))}
                </div>
              )}
              {view.type === 'global-chat' && ctxStudies.length > 0 && (
                <div className="sources-bar">
                  <span className="sources-label">Context</span>
                  <span className="sources-hint">Merged with DB search on send</span>
                  {ctxStudies.map(s => (
                    <button key={s.study_id} className="src-chip removable"
                      onClick={() => setCtxStudies(prev => prev.filter(x => x.study_id !== s.study_id))}>
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
                      {m.role === 'assistant' && m.ui?.kind === 'samples_report' ? (
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
        <div className="composer-wrap">
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
          <div className={`composer ${!isChat ? 'muted' : ''}`}>
            <textarea
              ref={taRef}
              className="composer-ta"
              rows={1}
              placeholder={isChat ? 'Message…' : 'Open a chat to start messaging'}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && isChat) { e.preventDefault(); sendMessage(); } }}
              disabled={!isChat || sending}
            />
            <button className="composer-send" onClick={sendMessage} disabled={!canSend}>↑</button>
          </div>
          <div className="composer-model">
            <label className="composer-model-label" htmlFor="composer-model-select">Model:</label>
            <select
              id="composer-model-select"
              className="composer-model-select"
              value={selectedModel}
              onChange={e => setSelectedModel(e.target.value)}
              disabled={sending}
              title="Choose the LLM that answers your message. Switch if one is down."
            >
              <option value="qwen3">Qwen 3 (397B)</option>
              <option value="qwen3-small">Qwen 3 Small (27B)</option>
              <option value="gpt-oss">GPT-OSS (120B)</option>
              <option value="gemma">Gemma (31B)</option>
              <option value="gemma-small">Gemma Small (~8B)</option>
              <option value="kimi">Kimi (1T)</option>
              <option value="glm-5">GLM-5 (744B)</option>
              <option value="minimax-m2">Minimax M2 (230B)</option>
            </select>
          </div>
          {compErr && <div className="composer-error">{compErr}</div>}
        </div>
      </div>

      {/* ══════════════════ MODAL ══════════════════════ */}
      {modalStudy && (
        <div className="modal-overlay" onClick={closeModal}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <button className="modal-close" onClick={closeModal}>×</button>
            <div className="modal-id">Study ID {modalStudy.study_id}</div>
            <div className="modal-title">{modalStudy.study_title || 'Untitled study'}</div>

            {!modalDetailLoading && modalDetail?.isPrivate ? (
              <div style={{display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'3rem 1rem', gap:'1rem', color:'#aaa'}}>
                <span style={{fontSize:'4rem', lineHeight:1}}>✕</span>
                <span style={{fontSize:'1rem', fontStyle:'italic', textAlign:'center'}}>This is a private study and its data is not publicly available.</span>
              </div>
            ) : (
              <>
                {(modalStudy.data_types || modalStudy.num_samples != null || modalStudy.num_preps != null) && (
                  <div className="modal-stats">
                    {(modalStudy.data_types || '').split(',').map(t => t.trim()).filter(Boolean).map(t => (
                      <span key={t} className="dtype-chip">{t}</span>
                    ))}
                    {modalStudy.num_samples != null && <span className="modal-stat">{modalStudy.num_samples} samples</span>}
                    {modalStudy.num_preps   != null && <span className="modal-stat">{modalStudy.num_preps} preps</span>}
                  </div>
                )}

                {modalStudy.study_abstract && (
                  <div className="modal-section"><h4>Abstract</h4><p>{modalStudy.study_abstract}</p></div>
                )}
                {modalStudy.pi_name && (
                  <div className="modal-section">
                    <h4>Principal Investigator</h4>
                    <p>{modalStudy.pi_name}{modalStudy.pi_affiliation ? ` — ${modalStudy.pi_affiliation}` : ''}</p>
                  </div>
                )}
                {modalStudy.pi_email && (
                  <div className="modal-section"><h4>Contact</h4><p>{modalStudy.pi_email}</p></div>
                )}

                <div className="modal-section">
                  <h4>Prep Templates</h4>
                  <PrepsTable detail={modalDetail} loading={modalDetailLoading} />
                </div>

                {!modalDetailLoading && modalDetail && (
                  <div className="modal-section">
                    <h4>Samples{modalDetail.total_samples != null ? ` (${modalDetail.total_samples} total${modalDetail.total_samples > 200 ? ', showing first 200' : ''})` : ''}</h4>
                    <SamplesBrowser
                      samples={modalDetail.samples || []}
                      totalSamples={modalDetail.total_samples}
                      layout="two-pane"
                      fetchFields={async (sampleId) => {
                        const res = await apiFetch(`/studies/${modalStudy.study_id}/samples/${encodeURIComponent(sampleId)}`);
                        if (!res.ok) return null;
                        const d = await res.json();
                        return d.fields || null;
                      }}
                    />
                  </div>
                )}

                {!modalDetailLoading && modalDetail && (
                  <div className="modal-section">
                    <h4>Artifacts</h4>
                    <ArtifactsTable detail={modalDetail} loading={modalDetailLoading} />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
