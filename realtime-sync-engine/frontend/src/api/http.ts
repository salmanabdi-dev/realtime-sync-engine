/** REST client. Session (token + user) is kept per browser tab in sessionStorage,
 *  so two windows can be signed in as different users. */

export interface User {
  id: number;
  username: string;
}

export interface Session {
  token: string;
  user: User;
}

export interface DocumentSummary {
  id: string;
  title: string;
  role: 'owner' | 'editor' | 'viewer';
  version: number;
  created_by: string;
  created_at: string;
}

export interface Member {
  user_id: number;
  username: string;
  role: DocumentSummary['role'];
}

export interface DocumentDetail extends DocumentSummary {
  content: string;
  members: Member[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const SESSION_KEY = 'rtse.session';

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session | null): void {
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable: session lasts for this page load only */
  }
}

async function request<T>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (init.token) headers.Authorization = `Token ${init.token}`;
  const res = await fetch(path, { ...init, headers });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const detail =
      (body && (body.detail || Object.values(body as Record<string, string[]>).flat()[0])) ||
      `Request failed (${res.status})`;
    throw new ApiError(res.status, String(detail));
  }
  return body as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<Session>('/api/auth/login/', { method: 'POST', body: JSON.stringify({ username, password }) }),
  register: (username: string, password: string) =>
    request<Session>('/api/auth/register/', { method: 'POST', body: JSON.stringify({ username, password }) }),
  listDocuments: (token: string) => request<DocumentSummary[]>('/api/documents/', { token }),
  createDocument: (token: string, title: string) =>
    request<DocumentSummary>('/api/documents/', { method: 'POST', token, body: JSON.stringify({ title }) }),
  getDocument: (token: string, id: string) => request<DocumentDetail>(`/api/documents/${id}/`, { token }),
  addMember: (token: string, id: string, username: string, role: Member['role']) =>
    request<Member>(`/api/documents/${id}/members/`, {
      method: 'POST',
      token,
      body: JSON.stringify({ username, role }),
    }),
};
