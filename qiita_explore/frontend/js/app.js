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

  if (status === 'loading') {
    return <div className="auth-loading"><WreathLoader size={32} /></div>;
  }
  if (status === 'anonymous') {
    return <ConnectQiita onConnected={refresh} />;
  }
  return <AuthenticatedApp identity={identity} onLogout={logout} onClaimDone={refresh} />;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
