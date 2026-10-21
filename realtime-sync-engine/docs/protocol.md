# WebSocket protocol

One socket per open document:

```
ws://<host>/ws/documents/<document-uuid>/?token=<api-token>&client_id=<uuid>[&since=<version>]
```

- `token`: the REST API token (`POST /api/auth/login/`).
- `client_id`: identifies the browser tab; echoed in updates and presence.
- `since`: sent on reconnect when the client already has state at that version.
  The server replies with `catchup` (missed revisions) instead of a full `snapshot`
  when it can.

All frames are JSON objects with a `type` field.

## Operations

Positions and lengths count **Unicode code points** (Python `str` indexing), not
JavaScript UTF-16 units. Ops in one mutation apply in order, each against the
result of the previous one.

```json
{"kind": "insert", "pos": 5, "text": "X"}
{"kind": "delete", "pos": 2, "length": 3}
```

Limits: at most 100 ops per mutation, 10,000 characters per insert, 1,000,000
characters per document. Text may not contain NUL or unpaired surrogates.

## Client → server

| type | fields | meaning |
|---|---|---|
| `mutation` | `document_id`, `expected_version`, `mutation_id`, `client_id`, `ops` | apply `ops` if the document is still at `expected_version` |
| `resync` | `since_version` | send me everything after this version |
| `ping` | `ts` | heartbeat; echoed in `pong` for round-trip measurement |

`mutation_id` is a client-generated UUID. The server applies a given id at most
once (`UNIQUE (document_id, mutation_id)`), so a client may safely resend the same
mutation after a reconnect or a conflict.

## Server → client

| type | fields | when |
|---|---|---|
| `snapshot` | `version`, `content`, `role`, `client_id`, `user` | after joining; also the fallback for `resync` |
| `catchup` | `from_version`, `current_version`, `revisions[]` (+ session fields on join) | after joining with `since`, or in reply to `resync` |
| `ack` | `mutation_id`, `version`, `duplicate` | your mutation is now `version` |
| `update` | `version`, `ops`, `author_id`, `client_id`, `mutation_id`, `server_ts` | someone else's accepted mutation |
| `conflict` | `mutation_id`, `expected_version`, `current_version`, `missing[]` or `snapshot` | your mutation was based on a stale version |
| `presence` | `event` (`joined` / `here` / `left`), `user_id`, `username`, `client_id` | who is connected |
| `error` | `code`, `message`, `mutation_id?` | malformed message, invalid op, read-only, rate limited |
| `pong` | `ts`, `server_ts` | reply to `ping` |

A revision (in `missing`, `revisions`) has the same shape as an `update` without
`type`, `document_id` and `server_ts`.

`missing` lists revisions `expected_version + 1 … current_version`, oldest first.
If that would be more than `SYNC_MAX_CATCHUP` (default 200) revisions, `missing`
is `null` and `snapshot` carries `{version, content}` instead.

### Error codes

`invalid_json`, `invalid_message`, `invalid_operation`, `unknown_type`,
`wrong_document`, `forbidden` (viewer tried to edit), `rate_limited`.
Errors never close the socket.

### Close codes

| code | meaning | client behavior |
|---|---|---|
| 4001 | missing or invalid token | stop, ask the user to sign in |
| 4003 | not a member of this document | stop |
| 4004 | document does not exist | stop |
| other | network or server restart | reconnect with exponential backoff and jitter |

The server accepts the handshake before checking access so that browsers actually
receive these codes (a rejected handshake only ever surfaces as 1006).

## Ordering guarantees

- The server subscribes a socket to the document group **before** reading the
  snapshot, so no accepted update can fall between the two. A client may
  therefore receive an `update` whose version it already has; it ignores it.
- Updates committed by different server processes may arrive out of order.
  Clients apply revisions strictly by version, buffer early arrivals, and send
  `resync` if a gap persists (default 500 ms).
- The sender of a mutation receives an `ack`, never its own `update`.
- A revision carrying the client's own in-flight `mutation_id` (seen through
  `catchup` or a broadcast from an older socket) counts as that mutation's ack.

## Example exchange

```
A → {"type":"mutation","document_id":"…","expected_version":10,"mutation_id":"m-a","client_id":"…","ops":[{"kind":"insert","pos":11,"text":"!"}]}
B → {"type":"mutation","document_id":"…","expected_version":10,"mutation_id":"m-b","client_id":"…","ops":[{"kind":"delete","pos":5,"length":6}]}

A ← {"type":"ack","mutation_id":"m-a","version":11,"duplicate":false}
B ← {"type":"update","version":11,"ops":[{"kind":"insert","pos":11,"text":"!"}],"mutation_id":"m-a",…}
B ← {"type":"conflict","mutation_id":"m-b","expected_version":10,"current_version":11,
     "missing":[{"version":11,"ops":[{"kind":"insert","pos":11,"text":"!"}],…}],"snapshot":null}

B rebases its delete over A's insert (unchanged here) and resends the same mutation_id:
B → {"type":"mutation","expected_version":11,"mutation_id":"m-b","ops":[{"kind":"delete","pos":5,"length":6}],…}
B ← {"type":"ack","mutation_id":"m-b","version":12,"duplicate":false}
A ← {"type":"update","version":12,…}
```
