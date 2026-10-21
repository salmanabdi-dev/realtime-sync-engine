/**
 * SyncClient: the client half of the synchronization engine. Framework-agnostic.
 *
 * It holds three layers of document state:
 *
 *   confirmed  the server's text at `version` (everything acknowledged so far)
 *   inflight   at most ONE mutation sent and not yet acked, based on `version`
 *   buffer     local edits made since, not yet sent
 *
 *   view = confirmed ∘ inflight.ops ∘ buffer        (what the user sees)
 *
 * Rules that keep every client convergent with the server:
 *
 *  1. Only one mutation in flight, so its expected_version is always the newest
 *     version we know about.
 *  2. Remote revisions are applied strictly in version order. Out-of-order arrivals
 *     wait in a small reorder buffer; a persistent gap triggers a resync.
 *  3. When a remote revision lands, pending local ops (inflight, then buffer) are
 *     rebased over it, and the transformed remote ops are applied to the view.
 *  4. A conflict means our inflight was based on a stale version. We apply the
 *     missing revisions (which rebases inflight as in rule 3), then resend the
 *     rebased inflight at the new version — with the SAME mutation_id, so the
 *     server's idempotency check guarantees it can never be applied twice, even if
 *     an earlier copy is still racing through a dead socket.
 *  5. A revision carrying our inflight's mutation_id is our own commit (seen via
 *     catchup or a late broadcast) and counts as the ack.
 */

import { Connection, type SocketFactory } from './connection';
import { applyOps, compact, diff, type Op } from './operations';
import {
  MAX_OPS_PER_MUTATION,
  uuid,
  type AckMsg,
  type CatchupMsg,
  type ConflictMsg,
  type ErrorMsg,
  type PresenceMsg,
  type Revision,
  type Role,
  type ServerMessage,
  type SnapshotMsg,
} from './protocol';
import { transform } from './transform';

export type SyncStatus =
  | 'connecting' // socket opening or waiting for the initial snapshot/catchup
  | 'synced' // everything local is acknowledged
  | 'saving' // a mutation is in flight or edits are buffered
  | 'resolving' // a conflict was received; rebased edits are being resubmitted
  | 'offline' // disconnected; reconnecting with backoff (edits are kept)
  | 'denied' // the server refused access; not retrying
  | 'closed';

export interface PeerInfo {
  clientId: string;
  userId: number;
  username: string;
}

export type LogKind =
  | 'connected'
  | 'disconnected'
  | 'snapshot'
  | 'catchup'
  | 'sent'
  | 'ack'
  | 'update'
  | 'conflict'
  | 'resync'
  | 'error';

export interface LogEntry {
  id: number;
  at: number;
  kind: LogKind;
  version?: number;
  detail: string;
}

export interface SyncState {
  status: SyncStatus;
  initialized: boolean;
  version: number;
  text: string;
  role: Role | null;
  user: { id: number; username: string } | null;
  pendingOps: number;
  inflight: boolean;
  conflictsResolved: number;
  lastConflictAt: number | null;
  discardedEdits: number;
  rttMs: number | null;
  peers: PeerInfo[];
  lastError: string | null;
  log: LogEntry[];
}

export type ViewChange =
  | { kind: 'remote'; ops: Op[] } // apply to cursor positions
  | { kind: 'reset' } // text replaced wholesale (snapshot)
  | { kind: 'local' };

export type Listener = (state: SyncState, change: ViewChange | null) => void;

export interface SyncClientOptions {
  documentId: string;
  token: string;
  /** e.g. "ws://localhost:5173" — defaults to the page origin. */
  wsBase?: string;
  socketFactory?: SocketFactory;
  heartbeatMs?: number;
  /** First reconnect delay (doubles per attempt, with jitter). */
  minBackoffMs?: number;
  /** Delay before each mutation is sent. Lets you watch conflicts happen in the UI. */
  sendDelayMs?: number;
  /** How long a version gap may persist before we ask for a resync. */
  gapTimeoutMs?: number;
  clientId?: string;
  logLimit?: number;
}

interface Inflight {
  mutationId: string;
  ops: Op[];
  baseVersion: number;
  sent: boolean;
}

export class SyncClient {
  readonly documentId: string;
  readonly clientId: string;

  private confirmed = '';
  private version = 0;
  private view = '';
  private inflight: Inflight | null = null;
  private buffer: Op[] = [];
  private reorder = new Map<number, Revision>();
  private gapTimer: ReturnType<typeof setTimeout> | null = null;
  private sendTimer: ReturnType<typeof setTimeout> | null = null;
  private initialized = false;
  private resolving = false;
  private listeners = new Set<Listener>();
  private peers = new Map<string, PeerInfo>();
  private logSeq = 0;
  private state: SyncState;
  private conn: Connection;
  private opts: Required<Pick<SyncClientOptions, 'sendDelayMs' | 'gapTimeoutMs' | 'logLimit'>> & SyncClientOptions;

  constructor(options: SyncClientOptions) {
    this.opts = { sendDelayMs: 0, gapTimeoutMs: 500, logLimit: 60, ...options };
    this.documentId = options.documentId;
    this.clientId = options.clientId ?? uuid();
    this.state = {
      status: 'connecting',
      initialized: false,
      version: 0,
      text: '',
      role: null,
      user: null,
      pendingOps: 0,
      inflight: false,
      conflictsResolved: 0,
      lastConflictAt: null,
      discardedEdits: 0,
      rttMs: null,
      peers: [],
      lastError: null,
      log: [],
    };
    this.conn = new Connection({
      buildUrl: () => this.buildUrl(),
      factory: options.socketFactory,
      heartbeatMs: options.heartbeatMs,
      minBackoffMs: options.minBackoffMs,
      onOpen: () => this.handleOpen(),
      onMessage: (msg) => this.handleMessage(msg),
      onClose: (code, willRetry) => this.handleClose(code, willRetry),
      onRtt: (ms) => this.patch({ rttMs: Math.round(ms * 10) / 10 }),
    });
  }

  // ------------------------------------------------------------------ public API

  start(): void {
    this.conn.start();
  }

  stop(): void {
    this.clearTimers();
    this.conn.stop();
    this.patch({ status: 'closed' });
  }

  /** Reconnect now (skips the backoff wait). */
  reconnect(): void {
    this.conn.reconnect();
  }

  setSendDelay(ms: number): void {
    this.opts.sendDelayMs = ms;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.state, null);
    return () => this.listeners.delete(listener);
  }

  getState(): SyncState {
    return this.state;
  }

  get text(): string {
    return this.view;
  }

  /** Report the editor's new full text; the client diffs it into ops. */
  applyLocalText(next: string): void {
    this.applyLocalOps(diff(this.view, next));
  }

  applyLocalOps(ops: Op[]): void {
    if (ops.length === 0) return;
    if (!this.canEdit()) {
      // Read-only or not loaded yet: refuse and restore the editor.
      this.publish({ kind: 'reset' });
      return;
    }
    this.view = applyOps(this.view, ops);
    this.buffer.push(...ops);
    this.flush();
    this.publish({ kind: 'local' });
  }

  canEdit(): boolean {
    return this.initialized && (this.state.role === 'owner' || this.state.role === 'editor');
  }

  // ------------------------------------------------------------------ connection events

  private buildUrl(): string {
    const base =
      this.opts.wsBase ??
      (typeof location !== 'undefined' ? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}` : '');
    const params = new URLSearchParams({ token: this.opts.token, client_id: this.clientId });
    // Reconnecting with known state: ask for the revisions we missed, not a snapshot.
    if (this.initialized) params.set('since', String(this.version));
    return `${base}/ws/documents/${this.documentId}/?${params}`;
  }

  private handleOpen(): void {
    this.addLog('connected', 'socket open, waiting for state');
    this.patch({ status: 'connecting', lastError: null });
  }

  private handleClose(code: number, willRetry: boolean): void {
    this.clearTimers();
    this.reorder.clear();
    this.peers.clear();
    if (this.inflight) this.inflight.sent = false; // fate unknown; resend (same id) on reconnect
    const reason =
      code === 4001 ? 'not signed in' : code === 4003 ? 'no access to this document' : code === 4004 ? 'document not found' : `code ${code}`;
    this.addLog('disconnected', willRetry ? `${reason}; reconnecting` : reason);
    this.patch({
      status: willRetry ? 'offline' : code >= 4001 && code <= 4004 ? 'denied' : 'closed',
      lastError: willRetry ? null : reason,
    });
  }

  private handleMessage(msg: ServerMessage): void {
    switch (msg.type) {
      case 'snapshot':
        return this.onSnapshot(msg);
      case 'catchup':
        return this.onCatchup(msg);
      case 'ack':
        return this.onAck(msg);
      case 'update':
        return this.onRevision(msg);
      case 'conflict':
        return this.onConflict(msg);
      case 'error':
        return this.onError(msg);
      case 'presence':
        return this.onPresence(msg);
      case 'pong':
        return;
    }
  }

  // ------------------------------------------------------------------ state sync

  private onSnapshot(msg: SnapshotMsg): void {
    const hadPending = this.inflight !== null || this.buffer.length > 0;
    const sameVersion = this.initialized && msg.version === this.version;
    if (this.initialized && hadPending && !sameVersion) {
      // We were too far behind for a replay, so there are no ops to rebase over.
      // Local edits can't be placed safely; the server state wins.
      const lost = (this.inflight?.ops.length ?? 0) + this.buffer.length;
      this.inflight = null;
      this.buffer = [];
      this.patch({ discardedEdits: this.state.discardedEdits + lost });
      this.addLog('error', `${lost} unsent edit(s) discarded: too far behind to merge`);
    }
    if (!sameVersion) {
      this.confirmed = msg.content;
      this.version = msg.version;
      this.view = applyOps(this.confirmed, [...(this.inflight?.ops ?? []), ...this.buffer]);
    }
    this.initialized = true;
    this.reorder.clear();
    this.addLog('snapshot', `${msg.content.length} chars`, msg.version);
    this.patch({ role: msg.role, user: msg.user });
    this.afterSync();
    this.publish({ kind: 'reset' });
  }

  private onCatchup(msg: CatchupMsg): void {
    if (msg.role) this.patch({ role: msg.role, user: msg.user ?? this.state.user });
    this.addLog('catchup', `${msg.revisions.length} revision(s)`, msg.current_version);
    for (const rev of msg.revisions) this.onRevision(rev);
    this.afterSync();
  }

  private afterSync(): void {
    // (Re)send whatever was in flight when the socket dropped, then the buffer.
    if (this.inflight && !this.inflight.sent) this.sendInflight();
    else this.flush();
    this.refreshStatus();
  }

  private onRevision(rev: Revision): void {
    if (!this.initialized || rev.version <= this.version) return; // already have it
    if (rev.version > this.version + 1) {
      this.reorder.set(rev.version, rev);
      this.armGapTimer();
      return;
    }
    this.applyRevision(rev);
    // Drain anything that was waiting for this one.
    let next = this.reorder.get(this.version + 1);
    while (next) {
      this.reorder.delete(next.version);
      this.applyRevision(next);
      next = this.reorder.get(this.version + 1);
    }
    if (this.reorder.size === 0) this.disarmGapTimer();
  }

  private applyRevision(rev: Revision): void {
    if (this.inflight && rev.mutation_id === this.inflight.mutationId) {
      // Our own commit, observed via catchup or a broadcast from an older socket.
      this.commitInflight(rev.version);
      return;
    }
    this.confirmed = applyOps(this.confirmed, rev.ops);
    this.version = rev.version;

    let remote = rev.ops;
    if (this.inflight) {
      const [rebased, remote1] = transform(this.inflight.ops, remote);
      this.inflight.ops = rebased;
      remote = remote1;
    }
    const [rebasedBuffer, remote2] = transform(this.buffer, remote);
    this.buffer = rebasedBuffer;
    this.view = applyOps(this.view, remote2);

    this.addLog('update', `${describe(rev.ops)} by user ${rev.author_id}`, rev.version);
    this.refreshStatus();
    this.publish({ kind: 'remote', ops: remote2 });
  }

  private onAck(msg: AckMsg): void {
    if (!this.inflight || msg.mutation_id !== this.inflight.mutationId) return; // stale/duplicate
    if (msg.version !== this.version + 1) {
      // We're missing revisions before ours. Fetch them; the catchup will contain
      // our own mutation_id and resolve the inflight through applyRevision().
      this.requestResync('ack ahead of local version');
      return;
    }
    this.commitInflight(msg.version);
  }

  private commitInflight(version: number): void {
    const inflight = this.inflight!;
    this.confirmed = applyOps(this.confirmed, inflight.ops);
    this.version = version;
    this.inflight = null;
    this.addLog('ack', describe(inflight.ops), version);
    if (this.resolving) {
      this.resolving = false;
      this.patch({ conflictsResolved: this.state.conflictsResolved + 1 });
    }
    this.flush();
    this.refreshStatus();
  }

  private onConflict(msg: ConflictMsg): void {
    if (!this.inflight || msg.mutation_id !== this.inflight.mutationId) return;
    this.resolving = true;
    this.patch({ lastConflictAt: Date.now() });
    this.addLog('conflict', `expected v${msg.expected_version}, server is at v${msg.current_version}`, msg.current_version);

    if (msg.snapshot) {
      // Too far behind for a replay: adopt the server state; our edit can't be placed.
      const lost = this.inflight.ops.length + this.buffer.length;
      this.inflight = null;
      this.buffer = [];
      this.resolving = false;
      this.confirmed = this.view = msg.snapshot.content;
      this.version = msg.snapshot.version;
      this.reorder.clear();
      this.patch({ discardedEdits: this.state.discardedEdits + lost });
      this.addLog('error', `${lost} edit(s) discarded: too far behind to merge`);
      this.refreshStatus();
      this.publish({ kind: 'reset' });
      return;
    }

    // Apply what we missed; each revision rebases our inflight + buffer.
    for (const rev of msg.missing ?? []) this.onRevision(rev);
    if (!this.inflight) return; // resolved while applying (it was ours after all)
    if (this.version < msg.current_version) return; // still a gap; resync will finish it
    this.sendInflight();
  }

  private onError(msg: ErrorMsg): void {
    this.addLog('error', `${msg.code}: ${msg.message}`);
    this.patch({ lastError: msg.message });
    if (this.inflight && msg.mutation_id === this.inflight.mutationId) {
      // The server refused the mutation outright (e.g. read-only, invalid op).
      // Fall back to server truth: drop pending edits and request fresh state.
      this.patch({ discardedEdits: this.state.discardedEdits + this.inflight.ops.length + this.buffer.length });
      this.inflight = null;
      this.buffer = [];
      this.view = this.confirmed;
      this.resolving = false;
      this.requestResync('mutation rejected');
      this.publish({ kind: 'reset' });
    }
  }

  private onPresence(msg: PresenceMsg): void {
    if (msg.event === 'left') this.peers.delete(msg.client_id);
    else this.peers.set(msg.client_id, { clientId: msg.client_id, userId: msg.user_id, username: msg.username });
    this.patch({ peers: [...this.peers.values()] });
  }

  // ------------------------------------------------------------------ sending

  private flush(): void {
    if (this.inflight || this.buffer.length === 0 || !this.initialized || !this.conn.isOpen) return;
    const ops = compact(this.buffer);
    this.buffer = ops.slice(MAX_OPS_PER_MUTATION);
    this.inflight = {
      mutationId: uuid(),
      ops: ops.slice(0, MAX_OPS_PER_MUTATION),
      baseVersion: this.version,
      sent: false,
    };
    this.sendInflight();
  }

  private sendInflight(): void {
    const inflight = this.inflight;
    if (!inflight || !this.conn.isOpen) return;
    if (inflight.ops.length === 0) {
      // Our edit was entirely swallowed by remote deletes: nothing left to send.
      this.inflight = null;
      this.resolving = false;
      this.flush();
      this.refreshStatus();
      return;
    }
    inflight.baseVersion = this.version;
    inflight.sent = true;
    const send = () => {
      this.sendTimer = null;
      if (this.inflight !== inflight) return;
      // Ops and version are read at send time: if remote revisions arrived during
      // the delay, the server will answer with a conflict and we rebase again.
      this.conn.send({
        type: 'mutation',
        document_id: this.documentId,
        expected_version: inflight.baseVersion,
        mutation_id: inflight.mutationId,
        client_id: this.clientId,
        ops: inflight.ops,
      });
      this.addLog('sent', `${describe(inflight.ops)}, based on v${inflight.baseVersion}`);
    };
    if (this.opts.sendDelayMs > 0) this.sendTimer = setTimeout(send, this.opts.sendDelayMs);
    else send();
    this.refreshStatus();
  }

  private requestResync(reason: string): void {
    this.addLog('resync', reason, this.version);
    this.conn.send({ type: 'resync', since_version: this.version });
  }

  private armGapTimer(): void {
    if (this.gapTimer) return;
    this.gapTimer = setTimeout(() => {
      this.gapTimer = null;
      if (this.reorder.size > 0) this.requestResync(`missing v${this.version + 1}`);
    }, this.opts.gapTimeoutMs);
  }

  private disarmGapTimer(): void {
    if (this.gapTimer) clearTimeout(this.gapTimer);
    this.gapTimer = null;
  }

  private clearTimers(): void {
    this.disarmGapTimer();
    if (this.sendTimer) clearTimeout(this.sendTimer);
    this.sendTimer = null;
  }

  // ------------------------------------------------------------------ state publication

  private refreshStatus(): void {
    if (!this.conn.isOpen || !this.initialized) {
      this.patch({});
      return;
    }
    const pending = this.inflight !== null || this.buffer.length > 0;
    const status: SyncStatus = this.resolving ? 'resolving' : pending ? 'saving' : 'synced';
    this.patch({ status });
  }

  private patch(partial: Partial<SyncState>): void {
    this.state = {
      ...this.state,
      ...partial,
      initialized: this.initialized,
      version: this.version,
      text: this.view,
      pendingOps: (this.inflight?.ops.length ?? 0) + this.buffer.length,
      inflight: this.inflight !== null,
    };
    this.publish(null);
  }

  private publish(change: ViewChange | null): void {
    for (const l of this.listeners) l(this.state, change);
  }

  private addLog(kind: LogKind, detail: string, version?: number): void {
    const entry: LogEntry = { id: ++this.logSeq, at: Date.now(), kind, detail, version };
    const log = [entry, ...this.state.log].slice(0, this.opts.logLimit);
    this.state = { ...this.state, log };
  }
}

function describe(ops: readonly Op[]): string {
  return ops
    .map((op) => (op.kind === 'insert' ? `+${JSON.stringify(op.text.length > 16 ? op.text.slice(0, 16) + '…' : op.text)}@${op.pos}` : `−${op.length}@${op.pos}`))
    .join(' ');
}

/** For tests: inspect internal layers. */
export function debugLayers(c: SyncClient): { confirmed: string; version: number; view: string; buffer: Op[]; inflight: Op[] | null } {
  const any = c as unknown as { confirmed: string; version: number; view: string; buffer: Op[]; inflight: Inflight | null };
  return { confirmed: any.confirmed, version: any.version, view: any.view, buffer: any.buffer, inflight: any.inflight?.ops ?? null };
}
