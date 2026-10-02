// Slash commands for the chat's study tools (backend helpers/study_tools.py).
// Each one forces its tool first: the request carries
// force_tool: {name, args[, text]}, and the model comments after the widget.
//   /preps 10317 16S            → get_study_preps(study_id=10317, data_type="16S")
//   /graph 10317 1115           → get_prep_graph(study_id=10317, prep_id=1115)
//   /preps the AGP preps        → no id: the server resolves the study from `text`
//                                 (a single matching pinned study, else a picker)
// The message itself is sent and saved as typed, so the model also reads any
// free text after the id.
// Globals in scope: none (pure data + functions)

const STUDY_SLASH_COMMANDS = [
  { cmd: '/preps',     tool: 'get_study_preps',         insert: '/preps ',     usage: '/preps 10317 [data type]',
    desc: "A study's prep templates and data types, with each prep's processing graph." },
  { cmd: '/samples',   tool: 'show_study_samples',      insert: '/samples ',   usage: '/samples 10317 [prep | data type]',
    desc: "A study's samples, with each clicked sample's metadata beside them." },
  { cmd: '/sample',    tool: 'get_sample_metadata',     insert: '/sample ',    usage: '/sample 10317 10317.00001002',
    desc: "One sample's metadata." },
  { cmd: '/graph',     tool: 'get_prep_graph',          insert: '/graph ',     usage: '/graph 10317 [prep]',
    desc: "A prep's processing graph — click a shape for its files." },
  { cmd: '/files',     tool: 'list_artifact_files',     insert: '/files ',     usage: '/files 10317 [prep | artifact]',
    desc: 'Files of a prep or an artifact, with paths and downloads.' },
  { cmd: '/aggregate', tool: 'propose_aggregation_add', insert: '/aggregate ', usage: '/aggregate 10317 [prep | data type]',
    desc: 'Add a study, or part of it, to a sample aggregation — you confirm first.' },
];

// Qiita's data types, matched exactly (any case) after the study id.
const _SLASH_DATA_TYPES = ['16S', '18S', 'ITS', 'Metagenomic', 'Metatranscriptomic', 'Metabolomic',
  'Proteomic', 'Multiomic', 'Genome Isolate', 'Full Length Operon'];

// null when msg isn't a study command; {error} for a malformed one; else {force_tool}.
function parseStudySlash(msg) {
  const m = /^(\/[a-z]+)(?:\s+([\s\S]*))?$/i.exec((msg || '').trim());
  const c = m && STUDY_SLASH_COMMANDS.find(x => x.cmd === m[1].toLowerCase());
  if (!c) return null;
  const rest = (m[2] || '').trim();
  const idm = /^(\d+)(?:\s+([\s\S]*))?$/.exec(rest);
  const tail = ((idm && idm[2]) || '').trim();
  if (c.tool === 'get_sample_metadata') {
    if (!idm || !/^\S+$/.test(tail)) return { error: `Usage: ${c.usage}` };
    return { force_tool: { name: c.tool, args: { study_id: +idm[1], sample_id: tail } } };
  }
  if (!idm) return { force_tool: { name: c.tool, args: {}, text: rest } };
  const args = { study_id: +idm[1] };
  if (/^\d+$/.test(tail)) {
    if (c.tool === 'list_artifact_files') args.artifact_id = +tail;   // the server also accepts a prep id here
    else if (c.tool === 'propose_aggregation_add') args.prep_ids = [+tail];
    else if (c.tool !== 'get_study_preps') args.prep_id = +tail;
  } else {
    const dt = _SLASH_DATA_TYPES.find(d => d.toLowerCase() === tail.toLowerCase());
    if (dt && c.tool === 'propose_aggregation_add') args.data_types = [dt];
    else if (dt && (c.tool === 'get_study_preps' || c.tool === 'show_study_samples')) args.data_type = dt;
  }
  return { force_tool: { name: c.tool, args } };
}
