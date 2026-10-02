// Inline widgets for the chat's study tools (backend helpers/study_tools.py).
// A tool's ui_payload carries ids only; the widget loads what it shows through
// the same endpoints and caches as the study view, so a saved chat re-renders
// its widgets on reload. ToolResultWidget (components.js) hands every kind in
// STUDY_WIDGET_KINDS to ChatStudyWidget, with the ctx from chatWidgetCtx.
// Globals in scope: React, useState, useEffect (utils.js), apiJson, fetchStudyDetail (utils.js),
//   PrepTemplatesSection, StudySamplesSection, sampleFieldsFetcher (study_detail.js),
//   SamplesBrowser, SampleFieldsCard (components.js), PrepGroupedSamples (prep_samples.js),
//   ArtifactNetwork, FilePathRow (artifact_network.js), filterGraphByPrep (merge_artifacts.js),
//   AggregationProposalCard (chat_aggregate_widget.js), STUDY_SLASH_COMMANDS (chat_slash.js),
//   PinIcon (icons.js)

const STUDY_WIDGET_KINDS = new Set(['study_preps', 'study_samples', 'sample_metadata', 'prep_graph',
  'artifact_files', 'aggregation_proposal', 'study_choice']);
const _CHAT_GRAPH = { zoomNeedsModifier: true, showPaths: true };
const _FILES_SHOWN = 50;

// What the widgets need from the app; built per render in app_render.js.
function chatWidgetCtx(s) {
  return {
    agg: s.agg,
    sending: s.sending,
    // fromModal: the study page's Back is history.back(), i.e. this chat.
    openStudyPage: sid => s.setView({ type: 'study', studyId: sid, fromModal: true }),
    openAggregations: () => { s.setView({ type: 'aggregations' }); s.setSidebarCollapsed(true); },
    sendCommand: text => s.sendMessage(text),
  };
}

// A widget that throws shows a line instead of taking the whole app down.
class WidgetBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return <p className="cw-note cw-error">This widget couldn't be shown ({String(this.state.error.message || this.state.error)}).</p>;
  }
}

function ChatStudyWidget({ payload, ctx }) {
  const Body = {
    study_preps: PrepsWidget, study_samples: SamplesWidget, sample_metadata: SampleWidget,
    prep_graph: GraphWidget, artifact_files: FilesWidget, study_choice: StudyChoiceWidget,
    aggregation_proposal: AggregationProposalCard,
  }[payload.kind];
  return (
    <WidgetBoundary>
      <div className="cw">{Body && <Body p={payload} ctx={ctx || {}} />}</div>
    </WidgetBoundary>
  );
}

// Title row; "Open study ↗" is a real link (new tab with ⌘/Ctrl), a plain click
// opens the study page in place.
function WidgetHead({ title, sub, studyId, ctx }) {
  const open = e => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0 || !ctx.openStudyPage) return;
    e.preventDefault();
    ctx.openStudyPage(studyId);
  };
  return (
    <div className="cw-head">
      <span className="cw-title">{title}</span>
      {sub && <span className="cw-sub">{sub}</span>}
      <span className="cw-spacer" />
      {studyId != null && <a className="cw-open" href={`#/studies/${studyId}`} onClick={open}>Open study ↗</a>}
    </div>
  );
}

const _studyLabel = p => `Study ${p.study_id}${p.study_title ? ` · ${p.study_title}` : ''}`;

function useStudyDetail(studyId) {
  const [state, setState] = useState({ detail: null, loading: true, error: '' });
  useEffect(() => {
    let live = true;
    fetchStudyDetail(studyId)
      .then(d => { if (live) setState({ detail: d, loading: false, error: '' }); })
      .catch(e => { if (live) setState({ detail: null, loading: false, error: e.message || 'Could not load' }); });
    return () => { live = false; };
  }, [studyId]);
  return state;
}

// Loading / error / private, or null when the detail is ready.
function detailNote({ detail, loading, error }) {
  if (loading) return <p className="cw-note">Loading…</p>;
  if (error) return <p className="cw-note cw-error">{error}</p>;
  if (detail?.isPrivate) return <p className="cw-note">This study is private.</p>;
  return null;
}

function PrepsWidget({ p, ctx }) {
  const st = useStudyDetail(p.study_id);
  const detail = st.detail && p.data_type
    ? { ...st.detail, preps: (st.detail.preps || []).filter(x => x.data_type === p.data_type) } : st.detail;
  return (
    <>
      <WidgetHead title={`Preps · ${_studyLabel(p)}`} sub={p.data_type} studyId={p.study_id} ctx={ctx} />
      {st.error || st.detail?.isPrivate ? detailNote(st) : (
        <PrepTemplatesSection study={{ study_id: p.study_id }} detail={detail} loading={st.loading}
          graphProps={_CHAT_GRAPH} />
      )}
    </>
  );
}

function SamplesWidget({ p, ctx }) {
  const sub = p.prep_id != null ? `prep ${p.prep_id}` : p.data_type;
  return (
    <>
      <WidgetHead title={`Samples · ${_studyLabel(p)}`} sub={sub} studyId={p.study_id} ctx={ctx} />
      {p.prep_id != null ? <PrepSamples p={p} />
        : p.data_type ? <PrepGroupedSamples studyId={p.study_id} dataType={p.data_type}
                          fetchFields={sampleFieldsFetcher(p.study_id)} />
        : <AllSamples p={p} />}
    </>
  );
}

function PrepSamples({ p }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    apiJson(`/studies/${p.study_id}/preps/${p.prep_id}/samples`)
      .then(x => { if (live) setD(x); })
      .catch(e => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [p.study_id, p.prep_id]);
  if (err) return <p className="cw-note cw-error">{err}</p>;
  if (!d) return <p className="cw-note">Loading…</p>;
  return (
    <>
      {d.total > d.samples.length && (
        <p className="cw-note">Showing the first {d.samples.length.toLocaleString()} of {d.total.toLocaleString()}</p>)}
      <SamplesBrowser samples={d.samples} layout="two-pane" fetchFields={sampleFieldsFetcher(p.study_id)} />
    </>
  );
}

function AllSamples({ p }) {
  const st = useStudyDetail(p.study_id);
  const note = detailNote(st);
  if (note) return note;
  const total = st.detail.total_samples;
  return (
    <>
      {total > (st.detail.samples || []).length && (
        <p className="cw-note">Showing the first {(st.detail.samples || []).length} of {total.toLocaleString()}; Group by prep lists each prep's samples.</p>)}
      <StudySamplesSection studyId={p.study_id} samples={st.detail.samples} />
    </>
  );
}

function SampleWidget({ p, ctx }) {
  const [fields, setFields] = useState(undefined);   // undefined = loading, null = not found
  useEffect(() => {
    let live = true;
    sampleFieldsFetcher(p.study_id)(p.sample_id).then(f => { if (live) setFields(f); }, () => { if (live) setFields(null); });
    return () => { live = false; };
  }, [p.study_id, p.sample_id]);
  return (
    <>
      <WidgetHead title={`Sample ${p.sample_id}`} sub={`study ${p.study_id}`} studyId={p.study_id} ctx={ctx} />
      {fields === null ? <p className="cw-note">No metadata found.</p>
        : <div className="cw-sample"><SampleFieldsCard sampleId={p.sample_id} fields={fields} loading={fields === undefined} /></div>}
    </>
  );
}

function GraphWidget({ p, ctx }) {
  const st = useStudyDetail(p.study_id);
  const note = detailNote(st);
  return (
    <>
      <WidgetHead title={`Processing graph · prep ${p.prep_id}${p.data_type ? ` (${p.data_type})` : ''}`}
        sub={_studyLabel(p)} studyId={p.study_id} ctx={ctx} />
      {note || <ArtifactNetwork key={p.prep_id} graph={filterGraphByPrep(st.detail.artifact_graph || [], p.prep_id)}
                 studyId={p.study_id} {..._CHAT_GRAPH} />}
    </>
  );
}

function FilesWidget({ p, ctx }) {
  const st = useStudyDetail(p.study_id);
  const [all, setAll] = useState(false);
  const note = detailNote(st);
  const byId = {};
  for (const n of (st.detail?.artifact_graph || [])) if (n.kind === 'artifact') byId[n.artifact_id] = n;
  const nodes = (p.artifact_ids || []).map(id => byId[id]).filter(Boolean);
  const total = nodes.reduce((k, n) => k + (n.filepaths || []).length, 0);
  let budget = all ? Infinity : _FILES_SHOWN;
  return (
    <>
      <WidgetHead title={`Files · ${_studyLabel(p)}`} sub={p.prep_id != null ? `prep ${p.prep_id}` : null}
        studyId={p.study_id} ctx={ctx} />
      {note || (
        <div className="cw-files">
          {nodes.map(n => {
            const fps = (n.filepaths || []).slice(0, Math.max(0, budget));
            budget -= fps.length;
            return fps.length > 0 && (
              <div key={n.artifact_id} className="cw-files-artifact">
                <div className="cw-files-name">
                  {n.name || n.artifact_type} <span className="cw-files-meta">
                    artifact {n.artifact_id} · {n.artifact_type} · {n.visibility}</span>
                </div>
                {fps.map(fp => <FilePathRow key={fp.filepath_id} fp={fp} artifactId={n.artifact_id} studyId={p.study_id} />)}
              </div>
            );
          })}
          {nodes.length === 0 && <p className="cw-note">No files.</p>}
          {!all && total > _FILES_SHOWN && (
            <button className="show-more-btn" onClick={() => setAll(true)}>▼ Show all {total} files</button>)}
        </div>
      )}
    </>
  );
}

// "Which study?" — resolve_study couldn't settle it, so the user picks; Use
// re-runs the slash command with the id (or names the study to the model).
function StudyChoiceWidget({ p, ctx }) {
  const [picked, setPicked] = useState(null);
  const cmd = STUDY_SLASH_COMMANDS.find(c => c.tool === p.for_tool)?.cmd;
  const use = (c) => {
    setPicked(c.study_id);
    ctx.sendCommand?.(cmd ? `${cmd} ${c.study_id}${p.text ? ` ${p.text}` : ''}`
                          : `Use study ${c.study_id}${p.text ? `: ${p.text}` : ''}`);
  };
  return (
    <>
      <WidgetHead title="Which study did you mean?" sub={p.text ? `“${p.text}”` : null} ctx={ctx} />
      <div className="cw-choices">
        {(p.candidates || []).map(c => (
          <div key={c.study_id} className={`cw-choice${picked === c.study_id ? ' on' : ''}`}>
            <span className="cw-choice-pin" title={c.pinned ? 'Pinned to this chat' : undefined}>
              {c.pinned && <PinIcon size={12} />}</span>
            <span className="cw-choice-id">{c.study_id}</span>
            <span className="cw-choice-title">{c.study_title}</span>
            <span className="cw-choice-meta">{[c.pi_name, c.num_samples != null && `${c.num_samples.toLocaleString()} samples`,
              c.data_types].filter(Boolean).join(' · ')}</span>
            <button className="btn-card-add" disabled={picked != null || !!ctx.sending} onClick={() => use(c)}>
              {picked === c.study_id ? '✓ Using' : 'Use'}</button>
          </div>
        ))}
      </div>
    </>
  );
}
