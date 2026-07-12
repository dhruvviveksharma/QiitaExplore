// Paste-PAT authentication gate. Loaded before app.js — app.js's App /
// AuthenticatedApp split (see app.js) ensures useAppState() (which fires
// project/chat/study/settings/merge requests) never runs until useAuth()
// has confirmed a session via GET /auth/me.
//
// The PAT itself never persists client-side: it lives only in the connect
// form's local input state, is POSTed once, then cleared immediately
// (success or failure) — never a URL param, localStorage, or sessionStorage.

function useAuth() {
  // 'loading' | 'anonymous' | 'authenticated' | 'unavailable'
  // 'unavailable' means the backend couldn't verify the session right now
  // (e.g. Qiita is momentarily unreachable during periodic PAT reverify —
  // see helpers/auth_middleware.py's 503 handling). The session itself is
  // NOT lost server-side, so this must not be treated as 'authenticated'
  // (would run the app against a stale identity) or as 'anonymous' (would
  // needlessly force the user to re-paste their PAT) — just retry shortly.
  const [status,   setStatus]   = useState('loading');
  const [identity, setIdentity] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const res = await apiFetch('/auth/me');
      if (!res.ok) {
        setStatus('unavailable');
        return;
      }
      const d = await res.json();
      if (d.anonymous) {
        clearCsrfToken();
        setIdentity(null);
        setStatus('anonymous');
      } else {
        setCsrfToken(d.csrf_token);
        setIdentity(d);
        setStatus('authenticated');
      }
    } catch (_) {
      clearCsrfToken();
      setIdentity(null);
      setStatus('anonymous');
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await apiFetch('/auth/logout', { method: 'POST' });
    } catch (_) {}
    clearCsrfToken();
    setIdentity(null);
    setStatus('anonymous');
  }, []);

  return { status, identity, refresh, logout };
}

function ConnectQiita({ onConnected }) {
  const [loginUrl, setLoginUrl] = useState(null);
  const [token,    setToken]    = useState('');
  const [busy,     setBusy]     = useState(false);
  const [error,    setError]    = useState('');

  useEffect(() => {
    apiFetch('/auth/login-url')
      .then(r => r.json())
      .then(d => setLoginUrl(d.url))
      .catch(() => {});
  }, []);

  const handleConnect = async () => {
    const pat = token.trim();
    if (!pat || busy) return;
    setBusy(true); setError('');
    try {
      const res = await apiFetch('/auth/connect', { method: 'POST', body: JSON.stringify({ token: pat }) });
      setToken(''); // clear the pasted PAT from state immediately, success or not
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(d.error || `Connect failed (${res.status})`);
        return;
      }
      const identity = await res.json();
      setCsrfToken(identity.csrf_token);
      onConnected(identity);
    } catch (_) {
      setToken('');
      setError('Network error — check that the backend is reachable and try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-gate">
      <div className="auth-card">
        <div className="app-logo app-logo-home auth-logo">
          <span className="app-logo-mark"><WreathLoader size={32} /></span>
          <span className="app-logo-text">Qiita<em>Explorer</em></span>
        </div>
        <ol className="auth-steps">
          <li>Log in with your Qiita account (opens in a new tab).</li>
          <li>Copy the access token Qiita shows you.</li>
          <li>Paste it below to connect.</li>
        </ol>
        <a
          className={`merge-btn-primary auth-login-link${loginUrl ? '' : ' disabled'}`}
          href={loginUrl || undefined}
          target="_blank"
          rel="noopener noreferrer"
          onClick={e => { if (!loginUrl) e.preventDefault(); }}
        >
          Log in with Qiita ↗
        </a>
        <input
          type="password"
          autoComplete="off"
          className="auth-token-input"
          placeholder="Paste your Qiita access token (qk_…)"
          value={token}
          onChange={e => setToken(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleConnect(); }}
        />
        {error && <div className="auth-error">{error}</div>}
        <button className="merge-btn-primary auth-connect-btn" disabled={busy || !token.trim()} onClick={handleConnect}>
          {busy ? 'Connecting…' : 'Connect'}
        </button>
      </div>
    </div>
  );
}

function LegacyClaimBanner({ csrfToken, onDone }) {
  const [counts,  setCounts]  = useState(null);
  const [visible, setVisible] = useState(true);
  const [busy,    setBusy]    = useState(false);

  useEffect(() => {
    apiFetch('/auth/legacy-default')
      .then(r => r.json())
      .then(d => { if (d.eligible) setCounts(d.counts); else setVisible(false); })
      .catch(() => setVisible(false));
  }, []);

  const claim = async () => {
    setBusy(true);
    try {
      await apiFetch('/auth/claim-default', { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } });
    } finally {
      setBusy(false);
      setVisible(false);
      onDone();
    }
  };

  if (!visible || !counts) return null;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  return (
    <div className="auth-claim-banner">
      <span>Found {total} existing item{total === 1 ? '' : 's'} from before login was added. Claim {total === 1 ? 'it' : 'them'} as yours?</span>
      <button className="merge-btn-ghost" onClick={() => setVisible(false)}>Not now</button>
      <button className="merge-btn-primary" disabled={busy} onClick={claim}>
        {busy ? 'Claiming…' : 'Claim'}
      </button>
    </div>
  );
}

function AccountBar({ identity, onLogout }) {
  return (
    <div className="auth-account-bar">
      <span className="auth-account-email" title={identity.user_id}>{identity.email || identity.user_id}</span>
      <button className="merge-btn-ghost" onClick={onLogout}>Log out</button>
    </div>
  );
}
