import { describe, expect, it } from 'vitest';
import { cpLength, del, insert, type Op } from '../operations';
import { debugLayers, SyncClient } from '../SyncClient';
import { FakeNetwork, rng, sleep } from './fakeNetwork';

const INITIAL = 'Hello world';

function makeClient(net: FakeNetwork, extra: Partial<ConstructorParameters<typeof SyncClient>[0]> = {}) {
  const c = new SyncClient({
    documentId: 'd',
    token: 't',
    wsBase: 'ws://test',
    socketFactory: net.factory,
    heartbeatMs: 0,
    minBackoffMs: 1,
    gapTimeoutMs: 5,
    ...extra,
  });
  c.start();
  return c;
}

function socketOf(net: FakeNetwork, index: number) {
  return net.sockets[index];
}

describe('SyncClient', () => {
  it('loads the snapshot and becomes synced', () => {
    const net = new FakeNetwork(INITIAL);
    const c = makeClient(net);
    net.drain();
    expect(c.getState()).toMatchObject({ status: 'synced', version: 0, text: INITIAL, role: 'editor' });
  });

  it('sends a local edit, applies the ack, and the other client receives it', () => {
    const net = new FakeNetwork(INITIAL);
    const a = makeClient(net);
    const b = makeClient(net);
    net.drain();
    a.applyLocalText('Hello, world');
    expect(a.getState().status).toBe('saving');
    net.drain();
    expect(a.getState()).toMatchObject({ status: 'synced', version: 1, text: 'Hello, world' });
    expect(b.getState()).toMatchObject({ status: 'synced', version: 1, text: 'Hello, world' });
    expect(net.server.content).toBe('Hello, world');
  });

  it('only keeps one mutation in flight and batches the rest', () => {
    const net = new FakeNetwork('');
    const a = makeClient(net);
    net.drain();
    a.applyLocalText('a');
    a.applyLocalText('ab');
    a.applyLocalText('abc');
    const sock = socketOf(net, 0);
    expect(sock.up.length).toBe(1); // only "a" is on the wire
    net.drain();
    expect(net.server.revisions.map((r) => r.ops)).toEqual([[insert(0, 'a')], [insert(1, 'bc')]]);
    expect(a.getState()).toMatchObject({ status: 'synced', version: 2, text: 'abc' });
  });

  it('resolves the design scenario: both edit version 0, one conflicts, both converge', () => {
    const net = new FakeNetwork(INITIAL);
    const a = makeClient(net);
    const b = makeClient(net);
    net.drain();
    a.applyLocalOps([insert(11, '!')]);
    b.applyLocalOps([del(5, 6)]);
    // Server processes A's mutation first, then B's (stale) one.
    socketOf(net, 0).deliverUp();
    socketOf(net, 1).deliverUp();
    net.drain();
    for (const c of [a, b]) expect(c.getState()).toMatchObject({ status: 'synced', version: 2, text: 'Hello!' });
    expect(b.getState().conflictsResolved).toBe(1);
    expect(b.getState().log.some((e) => e.kind === 'conflict')).toBe(true);
    expect(net.server.content).toBe('Hello!');
  });

  it('applies out-of-order updates in version order and resyncs a lasting gap', async () => {
    const net = new FakeNetwork('');
    const c = makeClient(net);
    net.drain();
    const feed = (msg: object) => (c as unknown as { handleMessage(m: object): void }).handleMessage(msg);
    const update = (version: number, ops: Op[]) =>
      ({ type: 'update', document_id: 'd', server_ts: 0, version, ops, author_id: 2, client_id: 'o', mutation_id: `m${version}` });

    feed(update(2, [insert(1, 'b')])); // arrives early: must wait for v1
    expect(c.getState()).toMatchObject({ version: 0, text: '' });
    feed(update(1, [insert(0, 'a')]));
    expect(c.getState()).toMatchObject({ version: 2, text: 'ab' });

    feed(update(4, [insert(2, 'd')])); // v3 never arrives
    await sleep(20);
    const sent = net.sockets[0].up.map((m) => JSON.parse(m));
    expect(sent.some((m) => m.type === 'resync' && m.since_version === 2)).toBe(true);
  });

  it('read-only users cannot change the text', () => {
    const net = new FakeNetwork(INITIAL);
    const c = makeClient(net);
    net.drain();
    (c as unknown as { state: { role: string } }).state.role = 'viewer';
    c.applyLocalText('changed');
    expect(c.text).toBe(INITIAL);
  });

  it('keeps edits made while offline and syncs them after reconnecting', async () => {
    const net = new FakeNetwork(INITIAL);
    const a = makeClient(net);
    const b = makeClient(net);
    net.drain();
    net.drop(socketOf(net, 0));
    net.drain();
    expect(a.getState().status).toBe('offline');
    a.applyLocalText('Hello brave world');
    b.applyLocalText('Hello world!');
    net.drain();
    await sleep(20); // reconnect backoff
    net.drain();
    for (const c of [a, b]) expect(c.text).toBe('Hello brave world!');
    expect(net.server.content).toBe('Hello brave world!');
  });

  it('a mutation committed while its ack was lost is not applied twice', async () => {
    const net = new FakeNetwork('');
    const a = makeClient(net);
    net.drain();
    a.applyLocalText('x');
    const sock = socketOf(net, 0);
    sock.close(4000); // client reconnects; the mutation is still on the wire
    sock.deliverUp(); // ...and reaches the server anyway, ack goes nowhere
    net.drain();
    await sleep(20);
    net.drain();
    expect(net.server.content).toBe('x');
    expect(net.server.version).toBe(1);
    expect(a.getState()).toMatchObject({ status: 'synced', text: 'x', version: 1 });
  });

  it('converges under random edits and random delivery order (fuzz)', async () => {
    for (let seed = 1; seed <= 40; seed++) {
      await fuzz(seed, false);
    }
  });

  it('converges with random disconnects and reconnects (fuzz)', async () => {
    for (let seed = 100; seed <= 125; seed++) {
      await fuzz(seed, true);
    }
  });
});

async function fuzz(seed: number, withDrops: boolean) {
  const r = rng(seed);
  const net = new FakeNetwork(INITIAL);
  const clients = [makeClient(net), makeClient(net), makeClient(net)];
  net.drain(r);

  for (let step = 0; step < 400; step++) {
    const roll = r();
    if (roll < 0.25) {
      const c = clients[Math.floor(r() * clients.length)];
      if (c.canEdit()) c.applyLocalOps([randomOp(r, c.text)]);
    } else if (withDrops && roll < 0.27) {
      const live = net.sockets.filter((s) => s.readyState === 1);
      if (live.length) net.drop(live[Math.floor(r() * live.length)]);
    } else {
      net.step(r);
    }
    if (withDrops && step % 50 === 0) await sleep(3);
  }

  // Let everything settle: deliver all messages, let reconnect timers fire.
  for (let i = 0; i < 200; i++) {
    net.drain(r);
    const settled = clients.every((c) => c.getState().status === 'synced');
    if (settled && net.idle) break;
    await sleep(3);
  }

  const server = net.server;
  expect(server.replay()).toBe(server.content); // revision log is consistent
  server.revisions.forEach((rev, i) => expect(rev.version).toBe(i + 1));
  const ids = server.revisions.map((rev) => rev.mutation_id);
  expect(new Set(ids).size).toBe(ids.length); // nothing applied twice
  for (const c of clients) {
    const layers = debugLayers(c);
    if (c.text !== server.content || layers.version !== server.version) {
      throw new Error(`seed ${seed}: client diverged ${JSON.stringify({ client: c.text, server: server.content, layers })}`);
    }
    expect(c.getState().status).toBe('synced');
    expect(c.getState().pendingOps).toBe(0);
  }
}

function randomOp(r: () => number, text: string): Op {
  const len = cpLength(text);
  if (len === 0 || r() < 0.6) {
    const chars = ['a', 'b', 'c', ' ', '😀', 'xy'];
    return insert(Math.floor(r() * (len + 1)), chars[Math.floor(r() * chars.length)]);
  }
  const pos = Math.floor(r() * len);
  return del(pos, 1 + Math.floor(r() * Math.min(3, len - pos)));
}
