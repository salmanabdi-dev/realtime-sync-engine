import { useState, type FormEvent } from 'react';
import { api, type Member } from '../api/http';

export function ShareForm({ token, documentId, onShared }: { token: string; documentId: string; onShared: () => void }) {
  const [username, setUsername] = useState('');
  const [role, setRole] = useState<Member['role']>('editor');
  const [message, setMessage] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const m = await api.addMember(token, documentId, username.trim(), role);
      setMessage(`Shared with ${m.username} as ${m.role}.`);
      setUsername('');
      onShared();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Could not share.');
    }
  }

  return (
    <form className="share" onSubmit={submit}>
      <input aria-label="Username to share with" placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
      <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as Member['role'])}>
        <option value="editor">can edit</option>
        <option value="viewer">can view</option>
      </select>
      <button className="btn" disabled={!username.trim()}>Share</button>
      {message && <span className="share__msg">{message}</span>}
    </form>
  );
}
