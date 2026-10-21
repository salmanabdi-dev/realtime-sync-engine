import { useState } from 'react';
import { loadSession, saveSession, type Session } from './api/http';
import { DocumentList } from './components/DocumentList';
import { DocumentView } from './components/DocumentView';
import { LoginScreen } from './components/LoginScreen';
import { useHashRoute } from './hooks/useHashRoute';

export function App() {
  const [session, setSession] = useState<Session | null>(loadSession);
  const [docId, navigate] = useHashRoute();

  if (!session) {
    return (
      <LoginScreen
        onSignedIn={(s) => {
          saveSession(s);
          setSession(s);
        }}
      />
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="topbar__name">Sync Engine</span>
        <span className="topbar__user">
          Signed in as <strong>{session.user.username}</strong>
          <button
            className="btn btn--quiet"
            onClick={() => {
              saveSession(null);
              setSession(null);
            }}
          >
            Sign out
          </button>
        </span>
      </header>
      <aside className="shell__side">
        <DocumentList token={session.token} activeId={docId} onOpen={navigate} />
      </aside>
      <main className="shell__main">
        {docId ? (
          <DocumentView key={docId} session={session} documentId={docId} />
        ) : (
          <div className="placeholder">
            <p>Open a document from the list, or create one.</p>
            <p>Then open it in a second window and type in both.</p>
          </div>
        )}
      </main>
    </div>
  );
}
