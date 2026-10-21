import type { LogEntry } from '../sync/SyncClient';

const TEXT: Record<LogEntry['kind'], string> = {
  connected: 'Connected',
  disconnected: 'Disconnected',
  snapshot: 'Loaded document',
  catchup: 'Caught up',
  sent: 'Sent change',
  ack: 'Change accepted',
  update: 'Received change',
  conflict: 'Conflict',
  resync: 'Resynchronizing',
  error: 'Problem',
};

export function ActivityLog({ log }: { log: LogEntry[] }) {
  return (
    <section className="activity" aria-label="Sync activity">
      <h2 className="panel-title">Sync activity</h2>
      {log.length === 0 && <p className="activity__empty">Nothing yet.</p>}
      <ol className="activity__list">
        {log.map((e) => (
          <li key={e.id} className={`activity__item activity__item--${e.kind}`}>
            <time>{new Date(e.at).toLocaleTimeString([], { hour12: false })}</time>
            <span className="activity__what">
              {TEXT[e.kind]}
              {e.version !== undefined && <span className="activity__ver"> v{e.version}</span>}
            </span>
            <span className="activity__detail">{e.detail}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
