/**
 * Rebasing local pending edits over remote edits the server already accepted.
 *
 * The server never merges: it accepts a mutation only at the current version.
 * When a remote revision lands under our unsent/unacknowledged edits, we shift our
 * edits' positions so they still mean what the user intended, then resubmit them
 * at the new version.
 *
 * `transform(a, b)` takes two op sequences that both apply to the same text S and
 * returns [a', b'] such that   apply(apply(S, b), a') === apply(apply(S, a), b').
 * `b` has priority: when both insert at the same position, b's text comes first.
 * The client always passes the remote (already committed) sequence as `b`.
 */

import { cpLength, del, insert, type Op } from './operations';

/** Rewrite `op` so it applies after `other`. `otherFirst` breaks insert/insert ties. */
export function transformOp(op: Op, other: Op, otherFirst: boolean): Op[] {
  if (op.kind === 'insert') {
    if (other.kind === 'insert') {
      const shift = other.pos < op.pos || (other.pos === op.pos && otherFirst);
      return [shift ? insert(op.pos + cpLength(other.text), op.text) : op];
    }
    const end = other.pos + other.length;
    if (op.pos <= other.pos) return [op];
    if (op.pos >= end) return [insert(op.pos - other.length, op.text)];
    return [insert(other.pos, op.text)]; // insert point was deleted: keep the text at the gap
  }

  // op is a delete
  const start = op.pos;
  const end = op.pos + op.length;
  if (other.kind === 'insert') {
    const len = cpLength(other.text);
    if (other.pos <= start) return [del(start + len, op.length)];
    if (other.pos >= end) return [op];
    // Someone typed inside the range we're deleting: delete around their text.
    const before = other.pos - start;
    return [del(start, before), del(start + len, op.length - before)];
  }

  const oStart = other.pos;
  const oEnd = other.pos + other.length;
  if (end <= oStart) return [op];
  if (start >= oEnd) return [del(start - other.length, op.length)];
  const overlap = Math.min(end, oEnd) - Math.max(start, oStart);
  const remaining = op.length - overlap;
  if (remaining === 0) return [];
  return [del(Math.min(start, oStart), remaining)];
}

export function transform(a: readonly Op[], b: readonly Op[]): [Op[], Op[]] {
  if (a.length === 0 || b.length === 0) return [[...a], [...b]];
  if (a.length === 1 && b.length === 1) {
    return [transformOp(a[0], b[0], true), transformOp(b[0], a[0], false)];
  }
  if (a.length > 1) {
    const [head, b1] = transform([a[0]], b);
    const [tail, b2] = transform(a.slice(1), b1);
    return [[...head, ...tail], b2];
  }
  const [a1, head] = transform(a, [b[0]]);
  const [a2, tail] = transform(a1, b.slice(1));
  return [a2, [...head, ...tail]];
}

/** Move a cursor (code point index) through an op applied by someone else. */
export function transformIndex(index: number, op: Op): number {
  if (op.kind === 'insert') return op.pos < index ? index + cpLength(op.text) : index;
  if (index <= op.pos) return index;
  if (index >= op.pos + op.length) return index - op.length;
  return op.pos;
}

export function transformIndexOps(index: number, ops: readonly Op[]): number {
  let i = index;
  for (const op of ops) i = transformIndex(i, op);
  return i;
}
