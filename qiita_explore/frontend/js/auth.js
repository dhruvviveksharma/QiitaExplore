// Classic-Qiita email + password sign-in. Loaded before app.js — app.js's App /
// AuthenticatedApp split (see app.js) ensures useAppState() (which fires
// project/chat/study/settings/merge requests) never runs until useAuth()
// has confirmed a session via GET /auth/me.
//
// The password lives only in the form's local state, is POSTed once as JSON,
// then cleared (success or failure) — never a URL param, localStorage, or
// sessionStorage, and never in input history.

function useAuth() {
  // 'loading' | 'anonymous' | 'authenticated'
  const [status,   setStatus]   = useState('loading');
  const [identity, setIdentity] = useState(null);
  const [qiitaUrl, setQiitaUrl] = useState(null);   // sign-up / password reset live on classic Qiita

  const refresh = useCallback(async () => {
    // A dead/hung connection (e.g. a stale SSH tunnel that accepts the socket
    // but never answers) must not leave status stuck on 'loading' forever.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const res = await apiFetch('/auth/me', { signal: controller.signal });
      if (!res.ok) {
        clearCsrfToken();
        setIdentity(null);
        setStatus('anonymous');
        return;
      }
      const d = await res.json();
      if (d.qiita_url) setQiitaUrl(d.qiita_url);
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
    } finally {
      clearTimeout(timer);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Any 401 from any endpoint means the session is gone — drop straight to the
  // sign-in screen. Without this the app stays in 'authenticated' and every pane
  // silently renders empty, which is indistinguishable from losing your data.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearCsrfToken();
      setIdentity(null);
      setStatus('anonymous');
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiPost('/auth/logout', {});
    } catch (_) {}
    clearCsrfToken();
    setIdentity(null);
    setStatus('anonymous');
  }, []);

  return { status, identity, qiitaUrl, refresh, logout };
}

function QiitaLogin({ qiitaUrl, onLoggedIn }) {
  const [email,    setEmail]    = useState('');
  const [password, setPassword] = useState('');
  const [busy,     setBusy]     = useState(false);
  const [error,    setError]    = useState('');
  const qiita = /^https?:\/\//.test(qiitaUrl || '') ? qiitaUrl : 'https://qiita.ucsd.edu';

  const submit = async (e) => {
    e.preventDefault();   // first: a native submit would send the password to the page URL
    if (busy || !email.trim() || !password) return;
    const pw = password;
    setPassword('');      // never kept past this request, success or not
    setBusy(true); setError('');
    try {
      const res = await apiPost('/auth/login', { email: email.trim(), password: pw });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        // The server's message covers 401 (generic), 403 unverified, 429 (with the wait) and 503.
        setError(d.error || `Sign-in failed (${res.status})`);
        return;
      }
      setCsrfToken(d.csrf_token);
      onLoggedIn(d);
    } catch (_) {
      setError('Network error — check that the backend is reachable and try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-gate">
      <form className="auth-card" method="post" onSubmit={submit}>
        <div className="app-logo app-logo-home auth-logo">
          <span className="app-logo-mark"><WreathLoader size={32} /></span>
          <span className="app-logo-text">Qiita<em>Explore</em></span>
        </div>
        <p className="auth-intro">Sign in with your Qiita account.</p>
        <input type="email" name="username" autoComplete="username" className="auth-input"
          placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} autoFocus required />
        <input type="password" name="password" autoComplete="current-password" className="auth-input"
          placeholder="Password" value={password} onChange={e => setPassword(e.target.value)} required />
        {error && <div className="auth-error" role="alert">{error}</div>}
        <button type="submit" className="merge-btn-primary auth-submit-btn" disabled={busy || !email.trim() || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <div className="auth-links">
          <a href={`${qiita}/auth/forgot/`} target="_blank" rel="noopener noreferrer">Forgot password?</a>
          <a href={`${qiita}/auth/create/`} target="_blank" rel="noopener noreferrer">Create a Qiita account</a>
        </div>
      </form>
    </div>
  );
}

function AccountBar({ identity, onLogout, theme, onToggleTheme }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const label = identity.email || identity.user_id;

  useEffect(() => {
    if (!open) return;
    const onDown = e => { if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false); };
    const onKey  = e => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="auth-account-bar" ref={rootRef}>
      <button className="auth-avatar" onClick={() => setOpen(v => !v)} title={label}>
        {label.charAt(0).toUpperCase()}
      </button>
      {open && (
        <div className="auth-account-menu">
          <div className="auth-account-menu-id">
            <span className="auth-account-email">{label}</span>
          </div>
          <button className="auth-account-menu-item" onClick={onToggleTheme}>
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
            <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
          </button>
          <button className="auth-account-menu-item" onClick={onLogout}>Log out</button>
        </div>
      )}
    </div>
  );
}
