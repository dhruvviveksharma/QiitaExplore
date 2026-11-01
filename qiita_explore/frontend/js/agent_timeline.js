// An agent reply: a timeline of the turn's work, then its answer. The header
// ("Working · 6s" with the wreath, then "Worked for 14s") heads a rail that
// links each step and tool call to the one before; text the model wrote
// between tool calls rides the rail too. The answer (text after the last tool
// call) sits below and always shows. A finished turn folds its timeline behind
// the header unless a widget in it is waiting on the user. elapsed_s is saved
// with the turn (backend helpers/chat_turn.py), so a reload shows the header too.
// Globals in scope: React, useState, useEffect (utils.js), WreathLoader (loaders.js),
//   CheckIcon, ChevronIcon (icons.js), ToolCallCard, MarkdownText, CopyResponseButton (components.js)

// Widgets that wait on the user (pick a study, confirm an aggregation): a turn
// holding one stays open when it finishes.
const _ACTION_WIDGET_KINDS = new Set(['study_choice', 'aggregation_proposal']);

function fmtElapsed(s) {
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

// Ticks on its own, so the reply's per-token re-renders don't drive the clock.
function WorkingHeader({ isStreaming, startedAt, elapsedS, open, onToggle }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!isStreaming) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [isStreaming]);
  const secs = isStreaming ? Math.max(0, Math.round((now - startedAt) / 1000)) : elapsedS;
  return (
    <button className="tl-item tl-head" onClick={onToggle} aria-expanded={open}>
      <span className="tl-icon">{isStreaming ? <WreathLoader size={20} /> : <CheckIcon size={12} />}</span>
      <span>{isStreaming ? `Working · ${fmtElapsed(secs)}` : `Worked for ${fmtElapsed(secs)}`}</span>
      <ChevronIcon dir={open ? 'up' : 'down'} size={11} />
    </button>
  );
}

function AgentMessageBubble({ segments, isStreaming, startedAt, elapsedS, msgKey, onPinStudy, onMergeStudy, onOpenStudy, pinnedStudyIds, onViewAllStudies, steps, pendingStep, widgetCtx }) {
  const [open, setOpen] = useState(null);   // null: follow the default below
  const segs = segments || [];
  const textContent = segs.filter(s => s.type === 'text' && s.content).map(s => s.content).join('\n\n');
  const lastTool = segs.map(s => s.type).lastIndexOf('tool');
  const hasWork = lastTool >= 0 || steps?.length > 0 || !!pendingStep;
  const showHead = isStreaming || (Number.isFinite(elapsedS) && hasWork);
  const waiting = segs.some(s => _ACTION_WIDGET_KINDS.has(s.result?.ui_payload?.kind));
  const isOpen = !showHead || (open ?? (isStreaming || waiting));
  const text = (seg, i) => seg.content && (
    <MarkdownText key={i} content={seg.content}
      className={`msg-bubble agent-bubble${(!seg.done && isStreaming) ? ' streaming' : ''}`} />
  );
  return (
    <div className="agent-msg">
      {(showHead || hasWork) && (
        <div className="agent-timeline">
          {showHead && <WorkingHeader isStreaming={isStreaming} startedAt={startedAt} elapsedS={elapsedS}
                         open={isOpen} onToggle={() => setOpen(!isOpen)} />}
          {isOpen && (steps || []).map((step, si) => (
            <div key={si} className="tl-item tl-step">
              <span className="tl-icon"><CheckIcon size={12} /></span>
              <span className="step-label">{step.label}</span>
              {step.detail && <span className="step-detail"> · {step.detail}</span>}
            </div>
          ))}
          {isOpen && pendingStep && (
            <div className="tl-item tl-step">
              <span className="tl-icon"><WreathLoader size={16} /></span>
              <span className="step-label">{pendingStep.label}</span>
            </div>
          )}
          {isOpen && segs.slice(0, lastTool + 1).map((seg, i) =>
            seg.type === 'tool' ? (
              <ToolCallCard key={i} seg={seg} msgKey={`${msgKey}-${i}`}
                onPin={onPinStudy} onMerge={onMergeStudy} onOpen={onOpenStudy}
                onViewAllStudies={onViewAllStudies}
                pinnedStudyIds={pinnedStudyIds} widgetCtx={widgetCtx} />
            ) : seg.type === 'text' && seg.content ? (
              <div key={i} className="tl-item tl-note">{text(seg)}</div>
            ) : null
          )}
        </div>
      )}
      {segs.slice(lastTool + 1).map((seg, j) => seg.type === 'text' && text(seg, lastTool + 1 + j))}
      {!isStreaming && textContent && <CopyResponseButton text={textContent} />}
    </div>
  );
}
