import type { SyncStatus } from '../sync/SyncClient';

const LABELS: Record<SyncStatus, string> = {
  connecting: 'Connecting',
  synced: 'All changes saved',
  saving: 'Saving',
  resolving: 'Merging a conflicting edit',
  offline: 'Offline, reconnecting',
  denied: 'No access',
  closed: 'Closed',
};

export function ConnectionBadge({ status }: { status: SyncStatus }) {
  return (
    <span className={`badge badge--${status}`} role="status" aria-live="polite">
      <span className="badge__dot" aria-hidden />
      {LABELS[status]}
    </span>
  );
}
