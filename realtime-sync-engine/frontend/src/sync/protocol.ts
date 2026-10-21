/** Typed WebSocket contract. Mirrors backend/apps/sync/protocol.py and docs/protocol.md. */

import type { Op } from './operations';

export type Role = 'owner' | 'editor' | 'viewer';

export interface Revision {
  version: number;
  ops: Op[];
  author_id: number;
  client_id: string;
  mutation_id: string;
}

interface SessionInfo {
  role: Role;
  client_id: string;
  user: { id: number; username: string };
}

export interface SnapshotMsg extends SessionInfo {
  type: 'snapshot';
  document_id: string;
  version: number;
  content: string;
}

export interface CatchupMsg extends Partial<SessionInfo> {
  type: 'catchup';
  document_id: string;
  from_version: number;
  current_version: number;
  revisions: Revision[];
}

export interface AckMsg {
  type: 'ack';
  mutation_id: string;
  version: number;
  duplicate: boolean;
}

export interface UpdateMsg extends Revision {
  type: 'update';
  document_id: string;
  server_ts: number;
}

export interface ConflictMsg {
  type: 'conflict';
  mutation_id: string;
  expected_version: number;
  current_version: number;
  missing: Revision[] | null;
  snapshot: { version: number; content: string } | null;
}

export interface ErrorMsg {
  type: 'error';
  code: string;
  message: string;
  mutation_id?: string;
}

export interface PresenceMsg {
  type: 'presence';
  event: 'joined' | 'left' | 'here';
  user_id: number;
  username: string;
  client_id: string;
}

export interface PongMsg {
  type: 'pong';
  ts: number | null;
  server_ts: number;
}

export type ServerMessage =
  | SnapshotMsg
  | CatchupMsg
  | AckMsg
  | UpdateMsg
  | ConflictMsg
  | ErrorMsg
  | PresenceMsg
  | PongMsg;

export interface MutationMsg {
  type: 'mutation';
  document_id: string;
  expected_version: number;
  mutation_id: string;
  client_id: string;
  ops: Op[];
}

export type ClientMessage =
  | MutationMsg
  | { type: 'resync'; since_version: number }
  | { type: 'ping'; ts: number };

/** Close codes that mean "don't reconnect". */
export const CLOSE_UNAUTHENTICATED = 4001;
export const CLOSE_FORBIDDEN = 4003;
export const CLOSE_NOT_FOUND = 4004;
export const PERMANENT_CLOSE_CODES = new Set([CLOSE_UNAUTHENTICATED, CLOSE_FORBIDDEN, CLOSE_NOT_FOUND]);

export const MAX_OPS_PER_MUTATION = 100;

export function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // RFC 4122 v4 fallback for non-secure contexts.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}
