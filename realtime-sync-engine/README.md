# Real-Time Sync Engine

Multi-user, real-time plain-text document editing with optimistic concurrency
control.

**Stack:** React + TypeScript · Django 5 + Django Channels · WebSockets · Redis
pub/sub · PostgreSQL 16

```
React client → WebSocket → Django Channels → version check (CAS) → PostgreSQL
            → Redis PUBLISH → Django Channels (every process) → other clients
```

- Every document state has a version. A mutation is accepted only if its
  `expected_version` matches the current one, checked atomically in a single
  conditional `UPDATE`. Stale mutations get a conflict carrying the revisions
  the client missed; the client rebases and resubmits.
- Lost updates are impossible: two writers on version 10 produce exactly one
  version 11 (proven by a threaded race test against real PostgreSQL).
- Retries are idempotent (`mutation_id`), so reconnects never apply an edit twice.
- Fan-out crosses process boundaries through Redis; the stack runs two
  independent backend processes to prove it.
- A reproducible benchmark reports mean / p50 / p95 / p99 broadcast latency
  against a 40 ms target.

Docs: [architecture (component by component)](docs/architecture.md) ·
[WebSocket protocol](docs/protocol.md) · [benchmark methodology](docs/benchmarks.md)

---

## Quick start (Docker)

Requirements: Docker with Compose v2.

```bash
make up            # or: docker compose up -d --build
```

This starts PostgreSQL, Redis, two backend processes (`:8000`, `:8001`) and the
Vite dev server. The first backend runs migrations and seeds demo data.

Open **http://localhost:5173** and sign in:

| user | password | role on the sample document |
|---|---|---|
| alice | password | owner |
| bob | password | editor |
| carol | password | viewer (read-only) |

### See it work

1. Sign in as **alice**, open *Welcome to the sync engine*.
2. Open a **new browser window** (not a tab opened from the page, so it gets its own
   sign-in), go to http://localhost:5173, sign in as **bob**, open the same document.
   (Or click *Open in new window* to open the same document as alice.)
3. Type in either window. The other updates as you type; the big version number
   and the revision rail advance together.
4. To force conflicts, tick **Hold my changes for 1.5 s before sending** in one
   window and type in both. The held edit is sent with a stale version, the
   server rejects it, the client rebases it over the other user's edit and
   resends. You'll see the amber tick on the rail, the *conflicts merged*
   counter, and the sequence in *Sync activity*. Both windows end up identical.
5. Stop the backend (`docker compose stop backend`), keep typing (the badge says
   *Offline, reconnecting*), start it again. Your edits are kept and merged.

Logs: `make logs`. Stop: `make down`. Delete the database too: `make clean`.

---

## Verify everything in one command

```bash
make verify
```

Runs the full backend suite (including the Redis two-process fan-out and the
threaded simultaneous-mutation tests), the frontend suite, then the benchmark for
1, 10 and 50 receivers plus contention. Mean / p50 / p95 / p99 are written to
`benchmarks/latency/results/latest.md` (and a timestamped `.md` + `.json`), which
are kept in git.

## Tests

```bash
make test-backend     # pytest against real PostgreSQL + Redis (inside Docker)
make test-frontend    # vitest: operations, transform property tests, client fuzzing
```

| requirement | test |
|---|---|
| multiple users connecting to one document | `tests/ws/test_connection.py::test_multiple_users_connect_to_one_document_and_see_presence` |
| WebSocket connection / disconnection, auth close codes | `tests/ws/test_connection.py` |
| real-time update propagation | `tests/ws/test_propagation.py` |
| Redis-backed fan-out (two layer instances; two real server processes) | `tests/integration/test_redis_fanout.py` |
| PostgreSQL persistence and constraints | `tests/db/test_models.py`, `test_propagation.py::test_broadcast_state_is_persisted` |
| optimistic concurrency, version increments, stale rejection | `tests/db/test_concurrency.py` |
| simultaneous updates (threads, real row locks) | `tests/db/test_simultaneous.py` |
| conflict recovery / resynchronization | `tests/ws/test_conflict_recovery.py`, `frontend/src/sync/__tests__/SyncClient.test.ts` |
| client convergence under random interleavings and disconnects | `SyncClient.test.ts` (fuzz) |

Run a subset: `docker compose run --rm --no-deps backend pytest tests/db -v`.
Skip the process-spawning test: `pytest -m "not integration"`.

## Benchmark

```bash
make bench
```

Restarts both backends with production-like settings, then runs fan-out (1, 10, 50
receivers) and contention (4 writers) scenarios. Sender on server A, receivers on
server B, so every message crosses Redis. Prints mean / p50 / p95 / p99 / max, a
histogram and per-stage server timings, saves JSON to `benchmarks/latency/results/`,
and exits non-zero if p99 ≥ 40 ms or any delivery was lost.

```bash
make bench BENCH_ARGS="--scenario fanout --receivers 1,100 --count 5000 --rate 200"
```

Afterwards, `make up` returns the servers to dev settings. Methodology:
[docs/benchmarks.md](docs/benchmarks.md).

---

## Running without Docker

Requirements: Python 3.11+, Node 20+, PostgreSQL 14+, Redis 6+.

```bash
# 1. Database and Redis
createuser -s sync && psql -c "ALTER USER sync PASSWORD 'sync';"
createdb -O sync sync_engine
redis-server &

# 2. Backend (two processes to see cross-process fan-out)
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo
uvicorn config.asgi:application --port 8000 --ws websockets &
uvicorn config.asgi:application --port 8001 --ws websockets &

# 3. Frontend
cd ../frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api and /ws to :8000)

# Tests
cd ../backend && pytest
cd ../frontend && npm test
```

Environment variables (all optional):

| variable | default | meaning |
|---|---|---|
| `POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `localhost` / `5432` / `sync_engine` / `sync` / `sync` | database |
| `REDIS_URL` | `redis://localhost:6379/0` | channel layer |
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` | `dev`, `test`, `bench` |
| `SYNC_MAX_CATCHUP` | `200` | conflicts further behind get a snapshot |
| `SYNC_DB_THREAD_SENSITIVE` | `1` | `0` runs DB work for different sockets in parallel |
| `SYNC_RATE_LIMIT` | `200` | messages per second per socket |
| `DB_POOL` | `0` | `1` enables Django's psycopg pool |
| `CHANNEL_LAYER_BACKEND` | `redis-pubsub` | `memory` for single-process experiments only |
| `BACKEND_URL` (frontend) | `http://localhost:8000` | Vite proxy target |

---

## Repository layout

```
backend/
  config/            settings (base/dev/test/bench), ASGI router, URLs
  apps/accounts/     token auth endpoints; wait_for_services, seed_demo commands
  apps/documents/    models + migration, services, permissions, REST API
  apps/sync/
    operations.py    pure insert/delete application (code points)
    protocol.py      message validation and builders
    concurrency.py   commit_mutation(): the optimistic concurrency algorithm
    broadcast.py     group naming, Redis fan-out helpers
    consumers.py     DocumentConsumer (WebSocket adapter)
    middleware.py    token auth for sockets
    routing.py       ws/documents/<uuid>/
    metrics.py       per-stage timings at /api/metrics/sync/
  tests/             unit, db, ws, integration, api
frontend/
  src/sync/          engine: operations, transform, protocol, connection, SyncClient (+ tests)
  src/components/    UI
  src/api/http.ts    REST client
benchmarks/latency/  benchmark runner, clients, stats; results/
docs/                architecture, protocol, benchmarks
```

## REST API

| method | path | |
|---|---|---|
| POST | `/api/auth/register/` | `{username, password}` → `{token, user}` |
| POST | `/api/auth/login/` | `{username, password}` → `{token, user}` |
| GET | `/api/documents/` | documents you're a member of |
| POST | `/api/documents/` | `{title, content?}` → create (you become owner) |
| GET | `/api/documents/<id>/` | content, version, members |
| POST | `/api/documents/<id>/members/` | owner only: `{username, role}` |
| GET / DELETE | `/api/metrics/sync/` | server stage timings / reset |

Authenticate with `Authorization: Token <token>`.
