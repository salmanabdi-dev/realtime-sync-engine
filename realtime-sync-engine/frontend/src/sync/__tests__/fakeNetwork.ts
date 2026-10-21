/**
 * In-memory server + network for testing SyncClient end to end.
 *
 * The FakeServer implements the same OCC contract as the Django backend (accept
 * only at the current version, idempotent mutation ids, conflict with missing
 * revisions, catchup on reconnect). The network holds every message in per-socket
 * FIFO queues and delivers them one at a time in an order chosen by a seeded RNG,
 * so tests can explore many interleavings deterministically.
 */

import type { SocketLike } from '../connection';
import { applyOps, type Op } from '../operations';
import type { ClientMessage, Revision, ServerMessage } from '../protocol';

export function rng(seed: number): () => number {
  // mulberry32
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type DownEvent = { kind: 'open' } | { kind: 'message'; data: string } | { kind: 'close'; code: number };

export class FakeSocket implements SocketLike {
  readyState = 0;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  up: string[] = [];
  down: DownEvent[] = [];
  serverAlive = false;
  readonly id: number;
  private static seq = 0;

  constructor(
    readonly net: FakeNetwork,
    readonly url: string,
  ) {
    this.id = ++FakeSocket.seq;
    this.down.push({ kind: 'open' });
  }

  send(data: string): void {
    if (this.readyState !== 1) throw new Error('send on non-open socket');
    this.up.push(data);
  }

  close(code = 1000): void {
    if (this.readyState >= 2) return;
    this.readyState = 2;
    this.net.server.disconnect(this);
    // Anything already sent upstream stays on the wire and may still reach the
    // server: that's the "mutation committed but ack lost" race.
    this.down = [{ kind: 'close', code }];
  }

  deliverDown(): void {
    const ev = this.down.shift();
    if (!ev) return;
    if (ev.kind === 'open') {
      this.readyState = 1;
      this.serverAlive = true;
      this.onopen?.({});
      this.net.server.connect(this);
    } else if (ev.kind === 'message') {
      if (this.readyState === 1) this.onmessage?.({ data: ev.data });
    } else {
      this.readyState = 3;
      this.onclose?.({ code: ev.code, reason: '' });
    }
  }

  deliverUp(): void {
    const data = this.up.shift();
    if (data !== undefined) this.net.server.receive(this, JSON.parse(data) as ClientMessage);
  }
}

export class FakeServer {
  content: string;
  version = 0;
  revisions: Revision[] = [];
  readonly initial: string;
  private conns = new Set<FakeSocket>();

  constructor(
    initial: string,
    readonly maxCatchup = 200,
  ) {
    this.content = initial;
    this.initial = initial;
  }

  private send(sock: FakeSocket, msg: ServerMessage): void {
    if (sock.serverAlive) sock.down.push({ kind: 'message', data: JSON.stringify(msg) });
  }

  connect(sock: FakeSocket): void {
    this.conns.add(sock);
    const params = new URL(sock.url, 'ws://x').searchParams;
    const since = params.get('since');
    const session = { role: 'editor' as const, client_id: params.get('client_id') ?? '', user: { id: 1, username: 'u' } };
    if (since !== null && Number(since) <= this.version && this.version - Number(since) <= this.maxCatchup) {
      this.send(sock, {
        type: 'catchup', document_id: 'd', from_version: Number(since), current_version: this.version,
        revisions: this.revisions.slice(Number(since)), ...session,
      });
    } else {
      this.send(sock, { type: 'snapshot', document_id: 'd', version: this.version, content: this.content, ...session });
    }
  }

  disconnect(sock: FakeSocket): void {
    sock.serverAlive = false;
    this.conns.delete(sock);
  }

  receive(sock: FakeSocket, msg: ClientMessage): void {
    if (msg.type === 'resync') {
      this.send(sock, { type: 'catchup', document_id: 'd', from_version: msg.since_version,
        current_version: this.version, revisions: this.revisions.slice(msg.since_version) });
      return;
    }
    if (msg.type !== 'mutation') return;
    const prior = this.revisions.find((r) => r.mutation_id === msg.mutation_id);
    if (prior) {
      this.send(sock, { type: 'ack', mutation_id: msg.mutation_id, version: prior.version, duplicate: true });
      return;
    }
    if (msg.expected_version !== this.version) {
      const behind = this.version - msg.expected_version;
      const replay = behind >= 0 && behind <= this.maxCatchup;
      this.send(sock, {
        type: 'conflict', mutation_id: msg.mutation_id, expected_version: msg.expected_version,
        current_version: this.version,
        missing: replay ? this.revisions.slice(msg.expected_version) : null,
        snapshot: replay ? null : { version: this.version, content: this.content },
      });
      return;
    }
    let next: string;
    try {
      next = applyOps(this.content, msg.ops);
    } catch (e) {
      this.send(sock, { type: 'error', code: 'invalid_operation', message: String(e), mutation_id: msg.mutation_id });
      return;
    }
    this.content = next;
    this.version += 1;
    const rev: Revision = { version: this.version, ops: msg.ops, author_id: 1, client_id: msg.client_id, mutation_id: msg.mutation_id };
    this.revisions.push(rev);
    for (const other of this.conns) {
      if (other !== sock) this.send(other, { type: 'update', document_id: 'd', server_ts: 0, ...rev });
    }
    this.send(sock, { type: 'ack', mutation_id: msg.mutation_id, version: this.version, duplicate: false });
  }

  replay(): string {
    return this.revisions.reduce((text, r) => applyOps(text, r.ops as Op[]), this.initial);
  }
}

export class FakeNetwork {
  readonly server: FakeServer;
  readonly sockets: FakeSocket[] = [];

  constructor(initial: string, maxCatchup?: number) {
    this.server = new FakeServer(initial, maxCatchup);
  }

  factory = (url: string): SocketLike => {
    const s = new FakeSocket(this, url);
    this.sockets.push(s);
    return s;
  };

  get idle(): boolean {
    return this.sockets.every((s) => s.up.length === 0 && s.down.length === 0);
  }

  /** Deliver one queued event, chosen at random. Returns false if nothing is queued. */
  step(random: () => number = Math.random): boolean {
    const choices: Array<() => void> = [];
    for (const s of this.sockets) {
      if (s.up.length) choices.push(() => s.deliverUp());
      if (s.down.length) choices.push(() => s.deliverDown());
    }
    if (!choices.length) return false;
    choices[Math.floor(random() * choices.length)]();
    return true;
  }

  /** Deliver everything (in random order) until quiet. */
  drain(random?: () => number, limit = 100_000): void {
    let n = 0;
    while (this.step(random)) {
      if (++n > limit) throw new Error('network did not quiesce');
    }
  }

  /** Drop a live connection from the server side (like a network failure). */
  drop(sock: FakeSocket): void {
    if (sock.readyState !== 1) return;
    sock.readyState = 2;
    this.server.disconnect(sock);
    sock.down = [{ kind: 'close', code: 1006 }];
  }
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
