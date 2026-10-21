import { useEffect, useRef } from 'react';
import { cpToCu, cuToCp } from '../sync/operations';
import type { SyncClient } from '../sync/SyncClient';
import { transformIndexOps } from '../sync/transform';

/**
 * Uncontrolled textarea driven imperatively by the SyncClient. Controlled React
 * inputs reset the caret on every remote change; here remote ops are applied to
 * the caret too, so your cursor stays where you were typing.
 */
export function Editor({ client, readOnly }: { client: SyncClient; readOnly: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const ta = ref.current!;
    ta.value = client.text;
    return client.subscribe((state, change) => {
      if (!change || change.kind === 'local' || ta.value === state.text) return;
      if (change.kind === 'remote' && document.activeElement === ta) {
        const before = ta.value;
        const start = transformIndexOps(cuToCp(before, ta.selectionStart), change.ops);
        const end = transformIndexOps(cuToCp(before, ta.selectionEnd), change.ops);
        ta.value = state.text;
        ta.setSelectionRange(cpToCu(state.text, start), cpToCu(state.text, end));
      } else {
        ta.value = state.text;
      }
    });
  }, [client]);

  return (
    <textarea
      ref={ref}
      className="editor"
      spellCheck={false}
      readOnly={readOnly}
      aria-label="Document text"
      onInput={(e) => client.applyLocalText(e.currentTarget.value)}
    />
  );
}
