import { describe, expect, it } from 'vitest';
import { applyOps, cpLength, del, insert, type Op } from '../operations';
import { transform, transformIndex } from '../transform';
import { rng } from './fakeNetwork';

const ALPHABET = ['a', 'b', 'c', 'x', 'y', ' ', '😀'];

function randomText(r: () => number, max = 12): string {
  let s = '';
  const n = Math.floor(r() * max);
  for (let i = 0; i < n; i++) s += ALPHABET[Math.floor(r() * ALPHABET.length)];
  return s;
}

/** Random op sequence valid when applied in order to `text`. */
function randomOps(r: () => number, text: string, maxOps = 3): Op[] {
  const ops: Op[] = [];
  let cur = text;
  const n = 1 + Math.floor(r() * maxOps);
  for (let i = 0; i < n; i++) {
    const len = cpLength(cur);
    let op: Op;
    if (len === 0 || r() < 0.55) {
      op = insert(Math.floor(r() * (len + 1)), randomText(r, 4) || 'z');
    } else {
      const pos = Math.floor(r() * len);
      op = del(pos, 1 + Math.floor(r() * (len - pos)));
    }
    ops.push(op);
    cur = applyOps(cur, [op]);
  }
  return ops;
}

describe('transform', () => {
  it('rebases the design example: insert "!" vs delete " world"', () => {
    const [aPrime, bPrime] = transform([del(5, 6)], [insert(11, '!')]);
    expect(applyOps(applyOps('Hello world', [insert(11, '!')]), aPrime)).toBe('Hello!');
    expect(applyOps(applyOps('Hello world', [del(5, 6)]), bPrime)).toBe('Hello!');
  });

  it('remote wins insert ties', () => {
    const [local] = transform([insert(0, 'L')], [insert(0, 'R')]);
    expect(applyOps(applyOps('', [insert(0, 'R')]), local)).toBe('RL');
  });

  it('keeps text typed inside a range someone else deleted', () => {
    const [local] = transform([insert(3, 'NEW')], [del(1, 5)]);
    expect(applyOps(applyOps('0123456789', [del(1, 5)]), local)).toBe('0NEW6789');
    const [remoteDel] = transform([del(1, 5)], [insert(3, 'NEW')]);
    expect(applyOps(applyOps('0123456789', [insert(3, 'NEW')]), remoteDel)).toBe('0NEW6789');
  });

  it('converges for random concurrent op sequences (TP1, 5000 cases)', () => {
    const r = rng(1234);
    for (let i = 0; i < 5000; i++) {
      const base = randomText(r);
      const a = randomOps(r, base);
      const b = randomOps(r, base);
      const [aPrime, bPrime] = transform(a, b);
      const viaB = applyOps(applyOps(base, b), aPrime);
      const viaA = applyOps(applyOps(base, a), bPrime);
      if (viaA !== viaB) {
        throw new Error(`diverged on case ${i}: ${JSON.stringify({ base, a, b, viaA, viaB })}`);
      }
    }
  });

  it('moves cursors through remote edits', () => {
    expect(transformIndex(5, insert(2, 'abc'))).toBe(8);
    expect(transformIndex(5, insert(5, 'abc'))).toBe(5);
    expect(transformIndex(5, del(1, 2))).toBe(3);
    expect(transformIndex(5, del(3, 5))).toBe(3);
    expect(transformIndex(5, del(6, 2))).toBe(5);
  });
});
