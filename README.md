Real-Time Sync Engine

A full-stack collaborative document editing system designed for real-time synchronization, concurrent update handling, and reliable data consistency across multiple users.

Overview

The system uses an event-driven architecture to synchronize document changes across connected clients. It combines WebSocket communication, Redis pub/sub, and PostgreSQL-backed optimistic concurrency control to coordinate simultaneous edits while maintaining consistent document state.

Tech Stack

Languages: Python, TypeScript, SQL

Frontend: React, TypeScript

Backend: Django, Django Channels

Database: PostgreSQL

Real-Time Communication: WebSockets, Redis pub/sub

Infrastructure: Docker, Docker Compose

Concurrency Control: Optimistic Concurrency Control (OCC)

Key Features

Real-Time Collaboration: Synchronizes document updates across multiple connected users using WebSockets.

Distributed Messaging: Uses Redis pub/sub and Django Channels to broadcast updates between clients and backend processes.

Optimistic Concurrency Control: Uses atomic PostgreSQL version checks to prevent lost updates during simultaneous mutations.

Conflict Detection & Recovery: Detects stale document versions and provides client-side resynchronization and conflict handling.

Idempotent Mutations: Uses mutation identifiers to prevent duplicate updates during retries.

Revision Management: Tracks document versions and maintains revision history.

Persistent Storage: Stores document state and related information in a normalized PostgreSQL relational database.

Automated Testing: Includes unit, frontend, integration, and concurrency tests.

Performance Benchmarking: Includes tools for measuring broadcast latency across multiple connected clients.

Architecture

Real-Time Synchronization

React Client → WebSocket → Django Channels → PostgreSQL Version Validation → Redis Pub/Sub → Connected Clients

Concurrent Update Handling

Client Mutation → Expected Version Check → Atomic Database Update → Version Increment → Broadcast

When multiple clients attempt to modify the same document version, the server accepts the first successful update and detects conflicting mutations through optimistic concurrency control. Clients can then resynchronize with the latest document state.

Performance

The project includes benchmarking tools for measuring:

Broadcast latency (mean, p50, p95, p99)

Real-time synchronization across multiple clients

Performance with 1, 10, and 50 receiving clients

Concurrent mutation handling

Cross-process Redis message delivery

The performance target is under 40ms broadcast latency. Actual results are pending end-to-end benchmark verification.

Project Status

Core implementation completed. Backend unit tests, frontend tests, and TypeScript type checking have passed in the development environment.

Full Docker integration testing, multi-process synchronization verification, and latency benchmarking are pending.

Technologies

React TypeScript Python Django Django Channels WebSockets Redis PostgreSQL Docker Optimistic Concurrency Control
