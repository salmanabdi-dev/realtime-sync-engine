# Architecture, component by component

Each section answers the same four questions: what it is, why it exists, how it
works, and how it connects to the rest. Read them in order; each builds on the
previous one.

```
Browser tab (React)                         Server process (uvicorn, ×2)                 Shared
───────────────────                         ────────────────────────────                 ──────
Editor ─► SyncClient ─► Connection ═══WS═══► TokenAuthMiddleware ─► DocumentConsumer
   ▲          │                                                        │      ▲
   │          │ rebases pending ops                                    ▼      │ doc_update
   │          │ over remote revisions                         commit_mutation │
   └──────────┘                                                        │      │
                                                                       ▼      │
                                                                 PostgreSQL   │
                                                                 (CAS UPDATE) │
                                                                       │      │
                                                          group_send ──┴─► Redis PUBLISH ─► every process
```

---

## 1. Data model: `backend/apps/documents/models.py`

**What.** Four tables plus Django's `auth_user`: `documents`, `document_memberships`,
`document_states`, `document_revisions`.

**Why.** PostgreSQL is the single source of truth. Every guarantee the system
makes (no lost updates, no duplicate versions, no double-applied retries) is
ultimately enforced by a row or a constraint here.

**How.**

| table | key | purpose |
|---|---|---|
| `documents` | `id` UUID | title, who created it, when |
| `document_memberships` | `(document_id, user_id)` unique | access control; `role` ∈ owner/editor/viewer (CHECK) |
| `document_states` | `document_id` (1:1) | the hot row: current `content` and `version` |
| `document_revisions` | `(document_id, version)` unique, `(document_id, mutation_id)` unique | append-only log of accepted mutations |

The state row is split from `documents` because it is updated on every edit
burst; keeping metadata elsewhere keeps the contended row small. `content` is the
single deliberate materialization (it could be rebuilt from revisions) and is only
written in the same transaction as the revision that produced it.

**Connections.** `concurrency.py` writes `document_states` and `document_revisions`;
the REST API and the consumer read memberships for authorization.

## 2. Pure text operations: `backend/apps/sync/operations.py` and `frontend/src/sync/operations.ts`

**What.** `insert(pos, text)` and `delete(pos, length)`, applied in sequence.

**Why.** Sending operations instead of whole documents keeps messages tiny (which
matters for latency) and makes it possible to rebase concurrent edits instead of
throwing one user's typing away.

**How.** Both sides count positions in Unicode code points. Python does that
natively; the TypeScript side converts from UTF-16 at its boundary (`cuToCp`,
`cpToCu`). Without this, one emoji desynchronizes every position after it.
`diff()` turns a textarea change into ops; `compact()` merges keystroke bursts.

**Connections.** The server only *applies* ops. The client also *transforms* them
(section 8).

## 3. Wire protocol: `backend/apps/sync/protocol.py`, `frontend/src/sync/protocol.ts`

**What.** Message parsing/validation (server) and types (client). Full reference
in [protocol.md](protocol.md).

**Why.** Every message from a browser is untrusted. Validating in one place keeps
the consumer simple and makes malformed input an `error` reply rather than an
exception.

**How.** Hand-rolled validation (no schema library on the hot path) that rejects
booleans posing as integers, unpaired surrogates and NUL characters (which
PostgreSQL cannot store), and oversized mutations. JSON is encoded with orjson.

## 4. Optimistic concurrency: `backend/apps/sync/concurrency.py`

**What.** `commit_mutation()`: apply a mutation if, and only if, the document is
still at the client's `expected_version`.

**Why.** Two users editing simultaneously must never silently overwrite each
other (the lost-update problem). Locks held across network round trips would be
slow and fragile; optimistic concurrency lets the common, uncontended case run
without waiting.

**How.**

```
BEGIN
  0. mutation_id already committed?           → Accepted(duplicate)   (idempotent retry)
  1. SELECT content, version                   (plain read, no lock)
  2. version != expected?                      → Conflict             (fast path)
  3. new_content = apply(content, ops)         (pure, may raise InvalidOperation)
  4. UPDATE document_states SET content=…, version=version+1
       WHERE document_id=… AND version=expected
     0 rows?                                   → Conflict             (lost the race)
  5. INSERT document_revisions (version = expected+1, …)
COMMIT                                         → Accepted
```

Step 4 is the guarantee. If two transactions both pass step 2, the second
`UPDATE` blocks on the row lock held by the first. When the first commits,
PostgreSQL (READ COMMITTED) re-evaluates the second `UPDATE`'s `WHERE` against
the newly committed row, sees `version = expected + 1`, and updates zero rows.
That transaction returns a conflict, and nothing it computed from the stale
content is written.

A conflict carries the revisions the client missed (or a snapshot if it is more
than 200 behind). Every conflict path first re-checks the `mutation_id`, so a
mutation that was committed by an earlier copy of the same request is reported
as accepted, never as a conflict. Unique constraints are the backstop: an
`IntegrityError` is resolved the same way.

**Connections.** Called by the consumer through `database_sync_to_async` (the
ORM's transactions are synchronous). It knows nothing about sockets, which is why
`tests/db/test_simultaneous.py` can race it with real threads.

## 5. Channel layer and fan-out: `config/settings/base.py`, `backend/apps/sync/broadcast.py`

**What.** `channels_redis.pubsub.RedisPubSubChannelLayer`, with one group per
document named `doc.<uuid>`.

**Why.** WebSocket connections for the same document can live in different server
processes. Something outside those processes has to carry "version 11 was
accepted" to all of them; Redis pub/sub does that with sub-millisecond overhead.

**How.** Joining a group is a Redis `SUBSCRIBE`; `group_send` is one `PUBLISH`.
Each subscribed process receives the message and dispatches it to its local
consumers in that group. The update is JSON-encoded once in `publish_update`,
not once per receiving socket. Pub/sub is fire-and-forget, which is acceptable
because Redis is never the source of truth: version numbers let every client
detect a gap, and `resync` fills it from PostgreSQL.

**Connections.** The consumer publishes after `commit_mutation` returns (so only
committed versions are ever broadcast); each consumer's `doc_update` handler
forwards to its socket.

## 6. WebSocket consumer: `backend/apps/sync/consumers.py`, `middleware.py`, `routing.py`

**What.** `DocumentConsumer`, one instance per socket, behind `TokenAuthMiddleware`.

**Why.** It adapts the network to the engine: authentication, authorization,
group membership, message dispatch, and replies.

**How.**

- `connect`: accept, then close with 4001/4003/4004 if the token, membership or
  document is missing. Otherwise `group_add` **before** reading state (so no
  update can slip in between), then send `snapshot`, or `catchup` if the URL has
  `since` and a replay is possible. Announce presence.
- `receive`: rate-limit (token bucket), parse, dispatch.
- mutation: viewers get `forbidden`. Otherwise commit, then publish the `update`
  to the group, then `ack` the sender. Conflicts go only to the sender.
- `doc_update`: forward to the socket unless this consumer originated it.
- `doc_presence`: forward; when someone joins, reply to them directly with `here`.
- `disconnect`: `group_discard`, presence `left`.

**Connections.** Everything above. It holds no document state.

## 7. REST API: `apps/accounts`, `apps/documents/api`

**What.** Register/login (token), list/create/open documents, share with a user.

**Why.** Things that aren't real-time don't belong on the socket.

**Connections.** The frontend uses it before opening a socket; the token it
returns authenticates the socket too. Tokens are bearer credentials, not
cookies, so cross-site WebSocket hijacking isn't possible.

## 8. Client sync engine: `frontend/src/sync/SyncClient.ts`, `transform.ts`, `connection.ts`

**What.** A framework-independent state machine that keeps the user's view
consistent with the server.

**Why.** The server is strict: it only accepts mutations at the current version.
Something has to turn "your edit was rejected" into "your edit was merged" without
the user noticing.

**How.** Three layers of state: `confirmed` (server text at `version`), at most one
`inflight` mutation, and a `buffer` of unsent edits. The view is
`confirmed ∘ inflight ∘ buffer`.

- Local edit: apply to the view, append to the buffer, send if nothing is in flight.
- `ack`: fold the inflight into `confirmed`, send the buffer.
- `update`: apply to `confirmed`; rebase inflight and buffer over it with
  `transform()`; apply the transformed remote ops to the view (and to the caret).
- `conflict`: apply the missing revisions (rebasing as above), then resend the
  inflight at the new version with the **same** `mutation_id`. Reusing the id
  means a copy of the original request still racing through a dead socket can
  never cause a double apply.
- Out-of-order updates wait in a reorder buffer; a lasting gap sends `resync`.
- Reconnect: `Connection` retries with exponential backoff and jitter; the URL
  carries `since=<version>` so the server answers with the missed revisions.

`transform(a, b)` satisfies `apply(apply(S, b), a') == apply(apply(S, a), b')`;
the remote (already committed) sequence gets priority on ties. This is tested
on 5,000 random cases, and the whole client is fuzzed against an in-memory
server with random message ordering and random disconnects
(`src/sync/__tests__/`).

**Connections.** Talks to the consumer through `Connection`; React subscribes to
its state.

## 9. React UI: `frontend/src/components`

**What.** Sign-in, a document list, and the document view: status badge, peers,
revision rail, editor, activity log, share form.

**Why.** To make the engine observable: you can see versions advance, which edits
were yours, when a conflict was merged, and what is still unconfirmed.

**How.** The editor is an uncontrolled `<textarea>` driven by the SyncClient, so
remote edits move your caret correctly instead of resetting it. "Hold my changes
for 1.5 s" delays sends so you can produce conflicts by hand in two windows.

## 10. Benchmark: `benchmarks/latency`

See [benchmarks.md](benchmarks.md).

## Known limitations

- Role changes take effect on the next connection, not on open sockets.
- If a client is more than 200 revisions behind *and* has unsent edits, those
  edits are discarded (and the UI says so) because there are no ops to rebase
  over. Raise `SYNC_MAX_CATCHUP` if that matters.
- IME composition can be interrupted when a remote edit arrives mid-composition;
  correctness is unaffected.
- One server-side row per document is the write bottleneck by design (all edits
  to one document are serialized). Different documents never contend.
