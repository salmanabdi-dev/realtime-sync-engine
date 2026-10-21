import type { LogEntry } from '../sync/SyncClient';

/**
 * The document's recent history as a strip of version ticks: your accepted edits,
 * other people's, and the points where your edit was rebased after a conflict.
 */
export function RevisionRail({ log, version }: { log: LogEntry[]; version: number }) {
  const ticks = [...log]
    .reverse()
    .filter((e) => (e.kind === 'ack' || e.kind === 'update' || e.kind === 'conflict') && e.version !== undefined);

  return (
    <div className="rail" aria-label={`Version ${version}`}>
      <span className="rail__version">v{version}</span>
      <ol className="rail__ticks">
        {ticks.map((t) => (
          <li
            key={t.id}
            className={`rail__tick rail__tick--${t.kind}`}
            title={`${t.kind === 'ack' ? 'Your edit' : t.kind === 'update' ? 'Someone else' : 'Conflict'}, v${t.version}: ${t.detail}`}
          />
        ))}
      </ol>
      <span className="rail__legend">
        <i className="rail__key rail__key--ack" /> yours <i className="rail__key rail__key--update" /> others{' '}
        <i className="rail__key rail__key--conflict" /> conflict
      </span>
    </div>
  );
}
