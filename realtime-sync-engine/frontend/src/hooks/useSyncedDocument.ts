import { useEffect, useState } from 'react';
import { SyncClient, type SyncState } from '../sync/SyncClient';

/** One SyncClient per open document; React only renders its state. */
export function useSyncedDocument(documentId: string, token: string) {
  const [client, setClient] = useState<SyncClient | null>(null);
  const [state, setState] = useState<SyncState | null>(null);

  useEffect(() => {
    const c = new SyncClient({ documentId, token });
    const unsubscribe = c.subscribe((s) => setState(s));
    c.start();
    setClient(c);
    return () => {
      unsubscribe();
      c.stop();
    };
  }, [documentId, token]);

  return { client, state };
}
