function AuthenticatedApp({ identity, onLogout, onClaimDone }) {
  const state = useAppState();
  return (
    <>
      <LegacyClaimBanner onDone={onClaimDone} />
      {/* AccountBar renders inside the topbar (see app_render.js) so flex
          centers it, rather than floating over the page on fixed coordinates. */}
      {renderApp(state, { identity, onLogout })}
    </>
  );
}

function App() {
  const { status, identity, refresh, logout } = useAuth();

  // Keep the address bar pointed at /login whenever we're anonymous — covers
  // first-load-anonymous, explicit logout, and mid-session-401 uniformly,
  // since all three only ever manifest as status === 'anonymous'. Stashes
  // whatever hash was there as ?next= so a deep link opened while logged out
  // still resolves after signing in, instead of always landing on Browse.
  useEffect(() => {
    if (status !== 'anonymous') return;
    if (window.location.hash.startsWith('#/login')) return; // already there, avoid churn
    const current = window.location.hash;
    const suffix = current && current !== '#/browse' ? `?next=${encodeURIComponent(current)}` : '';
    window.location.hash = `#/login${suffix}`;
  }, [status]);

  // Restore whatever hash was stashed before the /login redirect. Routed
  // through parseHash/buildHash (already-global, from useUrlSync.js) rather
  // than a raw decode+assign: URLSearchParams.get() already fully decodes
  // `next`, so a second decodeURIComponent() would double-decode and can
  // throw URIError on a malformed link — which auth.js's connect try/catch
  // would swallow as a false "Network error" even though sign-in succeeded.
  const handleConnected = () => {
    const qs = window.location.hash.split('?')[1] || '';
    const nextHash = new URLSearchParams(qs).get('next');
    const { view, studyId } = nextHash ? parseHash(nextHash) : { view: { type: 'browse' }, studyId: null };
    window.location.hash = buildHash(view, studyId);
    refresh();
  };

  if (status === 'loading') {
    return <div className="auth-loading"><WreathLoader size={32} /></div>;
  }
  if (status === 'anonymous') {
    return <ConnectQiita onConnected={handleConnected} />;
  }
  return <AuthenticatedApp identity={identity} onLogout={logout} onClaimDone={refresh} />;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
