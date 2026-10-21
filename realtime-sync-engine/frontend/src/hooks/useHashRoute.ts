import { useEffect, useState } from 'react';

/** "#/doc/<id>" — shareable URLs so the same document opens in another window. */
export function useHashRoute(): [string | null, (id: string | null) => void] {
  const read = () => {
    const m = location.hash.match(/^#\/doc\/([0-9a-f-]{36})$/i);
    return m ? m[1] : null;
  };
  const [docId, setDocId] = useState<string | null>(read);
  useEffect(() => {
    const onChange = () => setDocId(read());
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  const navigate = (id: string | null) => {
    location.hash = id ? `#/doc/${id}` : '';
  };
  return [docId, navigate];
}
