import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type DocumentSummary } from '../api/http';

interface Props {
  token: string;
  activeId: string | null;
  onOpen: (id: string) => void;
}

export function DocumentList({ token, activeId, onOpen }: Props) {
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null);
  const [title, setTitle] = useState('');
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await api.listDocuments(token));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load documents.');
    }
  }, [token]);

  useEffect(() => {
    refresh();
  }, [refresh, activeId]);

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    try {
      const doc = await api.createDocument(token, title.trim());
      setTitle('');
      await refresh();
      onOpen(doc.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the document.');
    }
  }

  return (
    <nav className="doclist" aria-label="Documents">
      <form className="doclist__new" onSubmit={create}>
        <input
          aria-label="New document title"
          placeholder="New document"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
        />
        <button className="btn btn--primary" disabled={!title.trim()}>Create</button>
      </form>
      {error && <p className="form-error">{error}</p>}
      {docs && docs.length === 0 && <p className="doclist__empty">No documents yet. Create one above.</p>}
      <ul className="doclist__items">
        {docs?.map((d) => (
          <li key={d.id}>
            <button
              className={`doclist__item${d.id === activeId ? ' is-active' : ''}`}
              onClick={() => onOpen(d.id)}
              aria-current={d.id === activeId ? 'page' : undefined}
            >
              <span className="doclist__title">{d.title}</span>
              <span className="doclist__meta">
                <span>{d.role}</span>
                <span>v{d.version}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <button className="btn btn--quiet doclist__refresh" onClick={refresh}>Refresh list</button>
    </nav>
  );
}
