// Sign-in, two ways side by side — classic-Qiita email + password, or a
// qiita-miint access token — reaching the same account for the same email.
// Loaded before app.js — app.js's App / AuthenticatedApp split (see app.js)
// ensures useAppState() (which fires project/chat/study/settings/merge
// requests) never runs until useAuth() has confirmed a session via GET /auth/me.
//
// The password and the token live only in their form's local state, are POSTed
// once as JSON, then cleared (success or failure) — never a URL param,
// localStorage, or sessionStorage, and never in input history.

function useAuth() {
  // 'loading' | 'anonymous' | 'authenticated'
  const [status,   setStatus]   = useState('loading');
  const [identity, setIdentity] = useState(null);
  const [qiitaUrl, setQiitaUrl] = useState(null);   // sign-up / password reset live on classic Qiita
  const [loginMethods, setLoginMethods] = useState(null);

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
      if (Array.isArray(d.login_methods)) setLoginMethods(d.login_methods);
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

  return { status, identity, qiitaUrl, loginMethods, refresh, logout };
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
    <form className="auth-card" method="post" onSubmit={submit}>
      <h2 className="auth-card-title">Log in with Qiita</h2>
      <p className="auth-intro">Your classic Qiita email and password.</p>
      <input type="email" name="username" autoComplete="username" className="auth-input"
        placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} required />
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
  );
}

function MiintLogin({ onLoggedIn }) {
  const [loginUrl, setLoginUrl] = useState(null);
  const [token,    setToken]    = useState('');
  const [busy,     setBusy]     = useState(false);
  const [error,    setError]    = useState('');

  useEffect(() => {
    apiFetch('/auth/login-url')
      .then(r => (r.ok ? r.json() : {}))
      .then(d => { if (/^https?:\/\//.test(d.url || '')) setLoginUrl(d.url); })
      .catch(() => {});
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    const pat = token.trim();
    if (busy || !pat) return;
    setToken('');         // never kept past this request, success or not
    setBusy(true); setError('');
    try {
      const res = await apiPost('/auth/connect', { token: pat });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
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
    <form className="auth-card" method="post" onSubmit={submit}>
      <h2 className="auth-card-title">Log in with qiita-miint</h2>
      <ol className="auth-steps">
        <li>Log in on qiita-miint (opens in a new tab).</li>
        <li>Copy the access token it shows you.</li>
        <li>Paste it here.</li>
      </ol>
      <a className={`merge-btn-ghost auth-miint-link${loginUrl ? '' : ' disabled'}`} href={loginUrl || undefined}
        target="_blank" rel="noopener noreferrer" onClick={e => { if (!loginUrl) e.preventDefault(); }}>
        Open qiita-miint login ↗
      </a>
      {/* text, not password: a password manager would offer to save the token
          as this site's password, or fill the classic password in here */}
      <input type="text" name="qiita-token" autoComplete="off" spellCheck={false} className="auth-input"
        placeholder="Access token (qk_…)" value={token} onChange={e => setToken(e.target.value)} required />
      {error && <div className="auth-error" role="alert">{error}</div>}
      <button type="submit" className="merge-btn-primary auth-submit-btn" disabled={busy || !token.trim()}>
        {busy ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  );
}

// Both ways in, as equal choices. Whichever is used, the same email reaches the
// same account. A method the server has switched off isn't shown.
function LoginScreen({ qiitaUrl, loginMethods, onLoggedIn }) {
  const methods = loginMethods || ['classic', 'miint'];
  return (
    <div className="auth-gate">
      <div className="auth-screen">
        <div className="app-logo app-logo-home auth-logo">
          <span className="app-logo-mark"><WreathLoader size={32} /></span>
          <span className="app-logo-text">Qiita<em>Explore</em></span>
        </div>
        <div className="auth-options">
          {methods.includes('classic') && <QiitaLogin qiitaUrl={qiitaUrl} onLoggedIn={onLoggedIn} />}
          {methods.includes('miint') && <MiintLogin onLoggedIn={onLoggedIn} />}
        </div>
        {methods.length > 1 && (
          <p className="auth-note">Same email, same account: either way, your workspaces, chats and aggregations are there.</p>
        )}
      </div>
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
