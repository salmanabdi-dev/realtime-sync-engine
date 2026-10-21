/**
 * Text operations, mirroring backend/apps/sync/operations.py.
 *
 * Positions and lengths count Unicode code points (what Python's str uses), not
 * UTF-16 code units (what JavaScript strings use). An emoji is 1 on the server and
 * 2 in `"😀".length`, so every conversion between the textarea (code units) and
 * the wire (code points) goes through the helpers here.
 */

export interface InsertOp {
  kind: 'insert';
  pos: number;
  text: string;
}
export interface DeleteOp {
  kind: 'delete';
  pos: number;
  length: number;
}
export type Op = InsertOp | DeleteOp;

const SURROGATE = /[\uD800-\uDFFF]/;

export const hasAstral = (s: string): boolean => SURROGATE.test(s);

export function cpLength(s: string): number {
  return hasAstral(s) ? Array.from(s).length : s.length;
}

export function insert(pos: number, text: string): InsertOp {
  return { kind: 'insert', pos, text };
}

export function del(pos: number, length: number): DeleteOp {
  return { kind: 'delete', pos, length };
}

export class InvalidOperationError extends Error {}

export function applyOp(text: string, op: Op): string {
  const astral = hasAstral(text) || (op.kind === 'insert' && hasAstral(op.text));
  if (!astral) {
    if (op.kind === 'insert') {
      if (op.pos > text.length) throw new InvalidOperationError(`insert at ${op.pos} past end ${text.length}`);
      return text.slice(0, op.pos) + op.text + text.slice(op.pos);
    }
    if (op.pos + op.length > text.length) {
      throw new InvalidOperationError(`delete ${op.pos}+${op.length} past end ${text.length}`);
    }
    return text.slice(0, op.pos) + text.slice(op.pos + op.length);
  }
  const cps = Array.from(text);
  if (op.kind === 'insert') {
    if (op.pos > cps.length) throw new InvalidOperationError(`insert at ${op.pos} past end ${cps.length}`);
    cps.splice(op.pos, 0, op.text);
  } else {
    if (op.pos + op.length > cps.length) {
      throw new InvalidOperationError(`delete ${op.pos}+${op.length} past end ${cps.length}`);
    }
    cps.splice(op.pos, op.length);
  }
  return cps.join('');
}

export function applyOps(text: string, ops: readonly Op[]): string {
  let out = text;
  for (const op of ops) out = applyOp(out, op);
  return out;
}

/**
 * Smallest single-region edit turning `before` into `after`: one delete and/or one
 * insert at the first differing position. A textarea input event is always one
 * contiguous change (typing, paste over a selection, cut, backspace), so this is
 * exact for real input.
 */
export function diff(before: string, after: string): Op[] {
  if (before === after) return [];
  const a = hasAstral(before) || hasAstral(after) ? Array.from(before) : before;
  const b = hasAstral(before) || hasAstral(after) ? Array.from(after) : after;
  const max = Math.min(a.length, b.length);
  let prefix = 0;
  while (prefix < max && a[prefix] === b[prefix]) prefix++;
  let suffix = 0;
  while (suffix < max - prefix && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;

  const ops: Op[] = [];
  const removed = a.length - prefix - suffix;
  if (removed > 0) ops.push(del(prefix, removed));
  const added = typeof b === 'string' ? b.slice(prefix, b.length - suffix) : b.slice(prefix, b.length - suffix).join('');
  if (added.length > 0) ops.push(insert(prefix, added));
  return ops;
}

/** Merge adjacent typing/backspacing so a burst of keystrokes becomes one op. */
export function compact(ops: readonly Op[]): Op[] {
  const out: Op[] = [];
  for (const op of ops) {
    const prev = out[out.length - 1];
    if (prev && prev.kind === 'insert' && op.kind === 'insert') {
      if (op.pos === prev.pos + cpLength(prev.text)) {
        out[out.length - 1] = insert(prev.pos, prev.text + op.text);
        continue;
      }
    }
    if (prev && prev.kind === 'delete' && op.kind === 'delete') {
      if (op.pos === prev.pos) {
        out[out.length - 1] = del(prev.pos, prev.length + op.length); // forward delete
        continue;
      }
      if (op.pos + op.length === prev.pos) {
        out[out.length - 1] = del(op.pos, prev.length + op.length); // backspace
        continue;
      }
    }
    out.push(op);
  }
  return out;
}

/** UTF-16 index (textarea selection) -> code point index (wire). */
export function cuToCp(text: string, cuIndex: number): number {
  const head = text.slice(0, cuIndex);
  return hasAstral(head) ? Array.from(head).length : head.length;
}

/** Code point index (wire) -> UTF-16 index (textarea selection). */
export function cpToCu(text: string, cpIndex: number): number {
  if (!hasAstral(text)) return Math.min(cpIndex, text.length);
  let cu = 0;
  let cp = 0;
  for (const ch of text) {
    if (cp >= cpIndex) break;
    cu += ch.length;
    cp++;
  }
  return cu;
}
