import { useCallback, useEffect, useState } from 'react';
import { api, type DocumentDetail, type Session } from '../api/http';
import { useSyncedDocument } from '../hooks/useSyncedDocument';
import { ActivityLog } from './ActivityLog';
import { ConflictIndicator } from './ConflictIndicator';
import { ConnectionBadge } from './ConnectionBadge';
import { Editor } from './Editor';
import { RevisionRail } from './RevisionRail';
import { ShareForm } from './ShareForm';

const SEND_DELAY_MS = 1500;

export function DocumentView({ session, documentId }: { session: Session; documentId: string }) {
  const { client, state } = useSyncedDocument(documentId, session.token);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [delayed, setDelayed] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadDetail = useCallback(() => {
    api.getDocument(session.token, documentId).then(setDetail).catch(() => setDetail(null));
  }, [session.token, documentId]);
  useEffect(loadDetail, [loadDetail]);

  useEffect(() => {
    client?.setSendDelay(delayed ? SEND_DELAY_MS : 0);
  }, [client, delayed]);

  if (!client || !state) return null;

  const readOnly = !client.canEdit();
  const others = state.peers.filter((p) => p.clientId !== client.clientId);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the URL bar still has it */
    }
  }

  return (
    <div className="docview">
      <section className="docview__main">
        <header className="docview__head">
          <div className="docview__titlebar">
            <h1 className="docview__title">{detail?.title ?? 'Document'}</h1>
            <ConnectionBadge status={state.status} />
          </div>
          <div className="docview__sub">
            <span>
              {state.user ? `You are ${state.user.username}` : 'Connecting'}
              {state.role && `, ${state.role}${readOnly && state.role === 'viewer' ? ' (read-only)' : ''}`}
            </span>
            <span className="peers" aria-label="Also here">
              {others.length === 0 ? 'Nobody else is here' : `Also here: ${[...new Set(others.map((p) => p.username))].join(', ')}`}
            </span>
          </div>
        </header>

        <RevisionRail log={state.log} version={state.version} />
        <Editor client={client} readOnly={readOnly} />

        <footer className="docview__foot">
          <dl className="stats">
            <div><dt>Version</dt><dd>{state.version}</dd></div>
            <div><dt>Unconfirmed edits</dt><dd>{state.pendingOps}</dd></div>
            <div><dt>Round trip</dt><dd>{state.rttMs === null ? 'measuring' : `${state.rttMs} ms`}</dd></div>
          </dl>
          <ConflictIndicator resolved={state.conflictsResolved} lastAt={state.lastConflictAt} discarded={state.discardedEdits} />
          {state.lastError && <p className="form-error">{state.lastError}</p>}
        </footer>

        <div className="docview__tools">
          <button className="btn" onClick={() => window.open(location.href, '_blank')}>Open in new window</button>
          <button className="btn btn--quiet" onClick={copyLink}>{copied ? 'Link copied' : 'Copy link'}</button>
          <button className="btn btn--quiet" onClick={() => client.reconnect()}>Reconnect</button>
          <label className="toggle">
            <input type="checkbox" checked={delayed} onChange={(e) => setDelayed(e.target.checked)} />
            <span>
              Hold my changes for 1.5 s before sending
              <small>Type in two windows at once to watch conflicts get merged.</small>
            </span>
          </label>
        </div>

        {state.role === 'owner' && (
          <div className="docview__share">
            <h2 className="panel-title">Share</h2>
            <ShareForm token={session.token} documentId={documentId} onShared={loadDetail} />
            {detail && (
              <p className="members">
                {detail.members.map((m) => `${m.username} (${m.role})`).join(', ')}
              </p>
            )}
          </div>
        )}
      </section>
      <ActivityLog log={state.log} />
    </div>
  );
}
