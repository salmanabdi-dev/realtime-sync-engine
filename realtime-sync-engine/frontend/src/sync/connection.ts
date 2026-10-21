/**
 * Socket lifecycle: open, heartbeat, reconnect with exponential backoff + jitter.
 * Knows nothing about documents; SyncClient decides what to send on (re)open.
 */

import { PERMANENT_CLOSE_CODES, type ClientMessage, type ServerMessage } from './protocol';

export interface SocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number; reason: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export type SocketFactory = (url: string) => SocketLike;

const OPEN = 1;

type ResolvedOptions = Required<Omit<ConnectionOptions, 'onRtt'>> & Pick<ConnectionOptions, 'onRtt'>;

export interface ConnectionOptions {
  buildUrl: () => string;
  factory?: SocketFactory;
  onOpen: () => void;
  onMessage: (msg: ServerMessage) => void;
  onClose: (code: number, willRetry: boolean) => void;
  onRtt?: (ms: number) => void;
  heartbeatMs?: number; // 0 disables
  minBackoffMs?: number;
  maxBackoffMs?: number;
}

export class Connection {
  private socket: SocketLike | null = null;
  private attempts = 0;
  private stopped = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private lastSeen = 0;
  private readonly opts: ResolvedOptions;

  constructor(opts: ConnectionOptions) {
    this.opts = {
      factory: (url) => new WebSocket(url) as unknown as SocketLike,
      heartbeatMs: 15000,
      minBackoffMs: 250,
      maxBackoffMs: 10000,
      ...Object.fromEntries(Object.entries(opts).filter(([, v]) => v !== undefined)),
    } as ResolvedOptions;
  }

  get isOpen(): boolean {
    return this.socket?.readyState === OPEN;
  }

  start(): void {
    this.stopped = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.stopHeartbeat();
    this.socket?.close(1000, 'client closed');
    this.socket = null;
  }

  /** Force a reconnect (e.g. heartbeat timeout or user action). */
  reconnect(): void {
    this.socket?.close(4000, 'reconnect');
  }

  send(msg: ClientMessage): boolean {
    if (!this.socket || this.socket.readyState !== OPEN) return false;
    this.socket.send(JSON.stringify(msg));
    return true;
  }

  private open(): void {
    const socket = this.opts.factory(this.opts.buildUrl());
    this.socket = socket;
    socket.onopen = () => {
      this.attempts = 0;
      this.lastSeen = Date.now();
      this.startHeartbeat();
      this.opts.onOpen();
    };
    socket.onmessage = (ev) => {
      this.lastSeen = Date.now();
      let msg: ServerMessage;
      try {
        msg = JSON.parse(String(ev.data)) as ServerMessage;
      } catch {
        return;
      }
      if (msg.type === 'pong' && msg.ts != null) this.opts.onRtt?.(performance.now() - msg.ts);
      this.opts.onMessage(msg);
    };
    socket.onclose = (ev) => {
      if (this.socket !== socket) return; // superseded
      this.stopHeartbeat();
      this.socket = null;
      const willRetry = !this.stopped && !PERMANENT_CLOSE_CODES.has(ev.code);
      this.opts.onClose(ev.code, willRetry);
      if (willRetry) this.scheduleRetry();
    };
    socket.onerror = () => {
      /* onclose follows and handles it */
    };
  }

  private scheduleRetry(): void {
    const base = Math.min(this.opts.maxBackoffMs, this.opts.minBackoffMs * 2 ** this.attempts);
    const delay = base / 2 + Math.random() * (base / 2); // jitter avoids reconnect stampedes
    this.attempts++;
    this.retryTimer = setTimeout(() => {
      if (!this.stopped) this.open();
    }, delay);
  }

  private startHeartbeat(): void {
    const every = this.opts.heartbeatMs;
    if (!every) return;
    this.stopHeartbeat();
    this.heartbeat = setInterval(() => {
      if (Date.now() - this.lastSeen > every * 2) {
        this.reconnect(); // silent connection: assume it's dead
        return;
      }
      this.send({ type: 'ping', ts: performance.now() });
    }, every);
  }

  private stopHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }
}
