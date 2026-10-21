import { useState, type FormEvent } from 'react';
import { api, type Session } from '../api/http';

export function LoginScreen({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent, mode: 'login' | 'register') {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(mode === 'login' ? await api.login(username, password) : await api.register(username, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach the server.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form className="login__card" onSubmit={(e) => submit(e, 'login')}>
        <h1 className="login__title">Sync Engine</h1>
        <p className="login__lede">
          Shared plain-text documents. Every change gets a version number, and every window sees it within
          milliseconds.
        </p>
        <label className="field">
          <span>Username</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="login__actions">
          <button className="btn btn--primary" disabled={busy}>Sign in</button>
          <button className="btn" type="button" disabled={busy || !username || !password} onClick={(e) => submit(e, 'register')}>
            Create account
          </button>
        </div>
        <p className="login__hint">
          Demo accounts: <strong>alice</strong> (owner), <strong>bob</strong> (editor), <strong>carol</strong> (viewer),
          all with password <code>password</code>. Each browser tab keeps its own sign-in, so you can be alice in one
          window and bob in another.
        </p>
      </form>
    </main>
  );
}
