import { useEffect, useState } from 'react';

/** Flashes when a conflict is resolved, and keeps a running count. */
export function ConflictIndicator({ resolved, lastAt, discarded }: { resolved: number; lastAt: number | null; discarded: number }) {
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    if (!lastAt) return;
    setFresh(true);
    const t = setTimeout(() => setFresh(false), 1600);
    return () => clearTimeout(t);
  }, [lastAt]);

  if (!resolved && !lastAt && !discarded) return null;
  return (
    <span className={`conflicts${fresh ? ' is-fresh' : ''}`}>
      {resolved === 1 ? '1 conflict merged' : `${resolved} conflicts merged`}
      {discarded > 0 && <span className="conflicts__lost">{discarded} edit(s) could not be merged</span>}
    </span>
  );
}
