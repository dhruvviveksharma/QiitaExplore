// KnightGPT tab: Leo's paper-RAG assistant, embedded as it is. This is the
// shared Open WebUI on kl-remote (chat.knight-lab-dev.org) with the
// knightgpt-rag model preselected. Chats, history, downloads and admin access
// are Open WebUI's own; QiitaExplore only frames it. The QiitaExplore colours
// inside the frame come from deploy/knightgpt-embed/ (custom.css + loader.js
// served on kl-remote), which switch on only when this page is the parent.
// Google sign-in refuses to load in a frame, so a first-time visitor signs in
// once in a normal tab ("Open in new tab"), then presses Reload here.
// The frame mounts the first time the tab is shown and after that only hides,
// so leaving the tab never cuts off a reply that is still streaming.
// Globals in scope: React, useState, useEffect (utils.js)

const KNIGHTGPT_URL = 'https://chat.knight-lab-dev.org/?models=knightgpt-rag';

function KnightGptTab({ visible }) {
  const [mounted, setMounted] = useState(false);
  const [frameKey, setFrameKey] = useState(0);   // bumped by Reload to remount the frame
  useEffect(() => { if (visible) setMounted(true); }, [visible]);
  if (!mounted) return null;
  return (
    <div className="kg-tab" style={visible ? undefined : { display: 'none' }}>
      <div className="kg-bar">
        <span className="kg-title">KnightGPT</span>
        <span>Blank, or asking you to sign in? Open it in a new tab, sign in with Google, then Reload.</span>
        <span className="topbar-spacer" />
        <button className="kg-btn" onClick={() => setFrameKey(k => k + 1)}>Reload</button>
        <a className="kg-btn" href={KNIGHTGPT_URL} target="_blank" rel="noopener noreferrer">Open in new tab ↗</a>
      </div>
      {/* No allow-top-navigation: the frame can't navigate QiitaExplore away. */}
      <iframe key={frameKey} className="kg-frame" src={KNIGHTGPT_URL} title="KnightGPT"
        allow="clipboard-write; microphone"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals" />
      <div className="kg-notice">Chats are saved in KnightGPT (chat.knight-lab-dev.org), where its admins can read them.</div>
    </div>
  );
}
