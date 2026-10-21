import { describe, expect, it } from 'vitest';
import { applyOp, applyOps, compact, cpToCu, cuToCp, del, diff, insert } from '../operations';

describe('operations', () => {
  it('applies inserts and deletes', () => {
    expect(applyOp('Hello world', insert(5, ','))).toBe('Hello, world');
    expect(applyOp('Hello world', del(5, 6))).toBe('Hello');
    expect(applyOps('abc', [del(0, 1), insert(2, 'd')])).toBe('bcd');
  });

  it('counts code points like the Python server', () => {
    expect(applyOp('😀b', insert(1, 'a'))).toBe('😀ab');
    expect(applyOp('a😀b', del(1, 1))).toBe('ab');
    expect(cuToCp('a😀b', 3)).toBe(2);
    expect(cpToCu('a😀b', 2)).toBe(3);
  });

  it('rejects out-of-range ops', () => {
    expect(() => applyOp('abc', insert(4, 'x'))).toThrow();
    expect(() => applyOp('abc', del(2, 2))).toThrow();
  });

  it('diffs a single edit region', () => {
    expect(diff('Hello world', 'Hello, world')).toEqual([insert(5, ',')]);
    expect(diff('Hello world', 'Hello')).toEqual([del(5, 6)]);
    expect(diff('Hello world', 'Hello there')).toEqual([del(6, 5), insert(6, 'there')]);
    expect(diff('a😀c', 'a😃c')).toEqual([del(1, 1), insert(1, '😃')]);
    expect(diff('same', 'same')).toEqual([]);
  });

  it('diff output always reproduces the target', () => {
    const cases = [['', 'abc'], ['abc', ''], ['aaaa', 'aa'], ['abcabc', 'abc'], ['x😀y', 'x😀😀y']];
    for (const [a, b] of cases) expect(applyOps(a, diff(a, b))).toBe(b);
  });

  it('compacts typing and backspacing bursts', () => {
    expect(compact([insert(0, 'a'), insert(1, 'b'), insert(2, 'c')])).toEqual([insert(0, 'abc')]);
    expect(compact([del(3, 1), del(2, 1), del(1, 1)])).toEqual([del(1, 3)]);
    expect(compact([del(1, 1), del(1, 1)])).toEqual([del(1, 2)]);
    expect(compact([insert(0, 'a'), insert(5, 'b')])).toEqual([insert(0, 'a'), insert(5, 'b')]);
  });
});
