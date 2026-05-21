# Idempotency Gateway

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat-square&logo=fastapi&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-37%20passing-2dc653?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)

A payment processing API that guarantees exactly-once execution using idempotency keys. Built for **FinSafe Transactions Ltd.** to solve the double-charging problem caused by network retries, client timeouts, and accidental duplicate submissions.

When a payment request is sent and the network drops before the response arrives, the client has no way of knowing whether the payment went through. Without an idempotency layer, retrying that request risks charging the customer twice. This gateway eliminates that risk entirely — no matter how many times the same request is replayed, the payment is processed exactly once and every subsequent attempt gets back the original response instantly.

---

## Table of Contents

- [Overview](#overview)
- [Diagrams and Flow](#diagrams-and-flow)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Setup Instructions](#setup-instructions)
- [API Documentation](#api-documentation)
- [Example Requests](#example-requests)
- [Design Decisions](#design-decisions)
- [Developer's Choice: Rate Limiting](#developers-choice-rate-limiting)
- [Testing Strategy](#testing-strategy)
- [Project Structure](#project-structure)
- [Known Limitations](#known-limitations)
- [Summary](#summary)

---

## Overview

FinSafe's clients — online shops, mobile apps, and partner services — occasionally experience network timeouts. When that happens, their systems automatically retry the failed request. Without protection, both the original and the retry get processed, resulting in a double charge.

This gateway sits in front of the payment processor and enforces a strict rule: **the same payment is never executed twice.** Every request must carry a system-generated `Idempotency-Key`. The gateway tracks the status of each key, stores the result of the first successful execution, and returns that stored result to every subsequent caller — without touching the payment processor again.

The implementation covers five distinct scenarios:

| Scenario | What happens |
|---|---|
| First request | Payment is processed and the result is stored |
| Duplicate request | Cached result is returned instantly with `X-Cache-Hit: true` |
| Conflict | Same key used with a different body — rejected with 409 |
| Race condition | Two identical requests arrive simultaneously — second one waits and gets the same result |
| Rate limit | More than 10 requests per 60 seconds from one IP — rejected with 429 |

---

## Diagrams and Flow

### Architecture Diagram

Shows all gateway components and how they connect, from the client generating a ticket through to payment processing and audit logging.

![Architecture Diagram](<images/Architecture Diagram.png>)

> Full component breakdown: [docs/architecture_diagram.md](docs/architecture_diagram.md)

---

### Sequence Diagram

Shows the full request lifecycle across all five scenarios.

![Sequence Diagram](<images/Sequence Diagram.webp>)

> Full diagram with Mermaid source: [docs/sequence_diagram.md](docs/sequence_diagram.md)

---

### Decision Flowchart

Shows every decision the gateway makes when a `POST /process-payment` request arrives, from rate limit check down to returning the cached response.

![Decision Flowchart](<images/Decision Flowchart.drawio.svg>)

> Full diagram with decision table: [docs/decision_flowchart.md](docs/decision_flowchart.md)

---

## Features

- **Exactly-once payment processing** — duplicate requests return the stored result without re-executing the payment
- **System-generated idempotency keys** — clients must call `POST /generate-ticket` before submitting a payment; random or fabricated keys are rejected
- **Conflict detection** — if a key is reused with a different request body, the gateway returns 409 with a clear error
- **Race condition handling** — concurrent requests with the same key are serialised using `asyncio.Event`; the second request blocks and waits for the first to finish rather than starting a new payment
- **Payment status endpoint** — clients can check whether a payment went through before deciding to retry
- **24-hour TTL** — idempotency records expire automatically; expiry is checked eagerly on every read
- **IP-based rate limiting** — sliding window of 10 requests per 60 seconds per client IP
- **Structured audit logging** — every gateway event is written to `audit_log.json` in JSONL format for compliance and debugging
- **CI pipeline** — GitHub Actions runs the full test suite on every push

---

## Tech Stack

| Technology | Purpose |
|---|---|
| Python 3.11+ | Runtime |
| FastAPI | ASGI web framework — chosen for native `asyncio` support required for race condition handling |
| Pydantic v2 | Request and response validation with custom field validators |
| Uvicorn | ASGI server |
| asyncio.Event | Race condition synchronisation primitive |
| hashlib SHA-256 | Deterministic body hashing for payload comparison |
| pytest + pytest-asyncio | Async test suite |
| httpx | Async HTTP client for testing |
| GitHub Actions | Continuous integration |

---

## Setup Instructions

### Prerequisites

- Python 3.11 or higher
- pip

### Installation

```bash
git clone https://github.com/doanane/Idempotency-Gateway.git
cd Idempotency-Gateway

python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Running the Server

```bash
uvicorn app.main:app --reload
```

The server starts at `http://127.0.0.1:8000` by default.

Interactive API docs (Swagger UI) are available at `http://127.0.0.1:8000/docs`.

### Running the Tests

```bash
pytest tests/ -v
```

---

## API Documentation

### POST /generate-ticket

Generates a unique, system-issued idempotency key. This key must be used as the `Idempotency-Key` header when calling `POST /process-payment`. Keys not issued by this endpoint are rejected.

Generating a new ticket while a previous unused ticket is still active will invalidate the previous one. Tickets expire after 30 minutes.

**No request body or headers required.**

**Response: 201 Created**
```json
{
  "ticket_id": "order-a3f8b2",
  "message": "Use this ticket_id as your Idempotency-Key when calling POST /process-payment.",
  "note": "Generating a new ticket invalidates your previous unused ticket. This ticket expires in 30 minutes."
}
```

**Response: 429 Too Many Requests**
```json
{
  "error": "Too many requests.",
  "hint": "You may send at most 10 requests per 60 seconds."
}
```

---

### POST /process-payment

Submits a payment request. Requires a valid ticket from `POST /generate-ticket` as the `Idempotency-Key` header.

**Required Header**

| Header | Type | Description |
|---|---|---|
| Idempotency-Key | string | A valid ticket issued by `POST /generate-ticket`. Max 255 characters. |

**Request Body**

| Field | Type | Constraints |
|---|---|---|
| amount | float | Must be greater than zero |
| currency | string | One of: `GHS`, `USD`, `EUR`, `GBP`, `NGN` (case-insensitive) |

**Response: 201 Created** — first successful request
```json
{
  "message": "Charged 100 GHS",
  "status": "success",
  "idempotency_key": "order-a3f8b2",
  "transaction_id": "d4f1a2b3-8c91-4e2f-b7a0-1f6d3c9e5082",
  "processed_at": "2026-05-21T10:30:00+00:00"
}
```

**Response: 200 OK** — duplicate request (same key, same body)

Returns the exact same body as the first response, plus the header `X-Cache-Hit: true`. No payment is processed again.

**Response: 409 Conflict** — same key, different body
```json
{
  "error": "Idempotency key already used for a different request body.",
  "hint": "Use a new ticket from POST /generate-ticket if you intend to make a different payment."
}
```

**Response: 400 Bad Request** — unrecognised or empty key
```json
{
  "error": "Ticket not recognised. Generate a ticket first using POST /generate-ticket.",
  "hint": "Use POST /generate-ticket to get a valid ticket_id, then retry with that as your Idempotency-Key."
}
```

**Response: 422 Unprocessable Entity** — invalid payload or missing header

Returned by FastAPI validation when the amount is zero or negative, the currency is unsupported, a required field is missing, or the `Idempotency-Key` header is absent entirely.

**Response: 429 Too Many Requests**
```json
{
  "error": "Too many requests.",
  "hint": "You may send at most 10 requests per 60 seconds."
}
```

**Response: 503 Service Unavailable** — in-flight timeout
```json
{
  "error": "The original request is taking too long.",
  "hint": "Please retry after a moment."
}
```

---

### GET /payment-status/{ticket_id}

Returns the current status of a payment for a given ticket ID. This endpoint is the "check before retry" mechanism — after recovering from a crash, a client can call this first to see whether the payment already went through, and only retry if the status is not `COMPLETED`.

**Path Parameter**

| Parameter | Description |
|---|---|
| ticket_id | The ticket ID returned by `POST /generate-ticket` |

**Response: 200 OK** — ticket issued but payment not yet started
```json
{
  "ticket_id": "order-a3f8b2",
  "status": "PENDING",
  "message": "Ticket issued but no payment has been initiated with it yet."
}
```

**Response: 200 OK** — payment is currently being processed
```json
{
  "ticket_id": "order-a3f8b2",
  "status": "PROCESSING",
  "created_at": "2026-05-21T10:30:00+00:00",
  "expires_at": "2026-05-22T10:30:00+00:00"
}
```

**Response: 200 OK** — payment completed
```json
{
  "ticket_id": "order-a3f8b2",
  "status": "COMPLETED",
  "created_at": "2026-05-21T10:30:00+00:00",
  "expires_at": "2026-05-22T10:30:00+00:00",
  "payment": {
    "message": "Charged 100 GHS",
    "status": "success",
    "idempotency_key": "order-a3f8b2",
    "transaction_id": "d4f1a2b3-8c91-4e2f-b7a0-1f6d3c9e5082",
    "processed_at": "2026-05-21T10:30:02+00:00"
  }
}
```

**Response: 404 Not Found** — ticket not recognised
```json
{
  "error": "No payment record found for this ticket ID."
}
```

---

### GET /

Health check. Returns the service name and version.

**Response: 200 OK**
```json
{
  "service": "Idempotency Gateway",
  "status": "healthy",
  "version": "1.0.0"
}
```

---

## Example Requests

The correct flow is always: **generate ticket → submit payment → check status if needed**.

### Step 1 — Generate a ticket

```bash
curl -X POST http://127.0.0.1:8000/generate-ticket
```

Response:
```json
{ "ticket_id": "order-a3f8b2" }
```

### Step 2 — Submit the payment

```bash
curl -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-a3f8b2" \
  -d '{"amount": 100, "currency": "GHS"}'
```

### Step 3 — Retry safely (duplicate)

Run the exact same command again. No new charge is made. The response is identical and includes `X-Cache-Hit: true`.

### Step 4 — Check payment status

```bash
curl http://127.0.0.1:8000/payment-status/order-a3f8b2
```

### Step 5 — Attempt a conflict

```bash
curl -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-a3f8b2" \
  -d '{"amount": 500, "currency": "GHS"}'
```

Response: `409 Conflict`

### Step 6 — Use a fabricated key (rejected)

```bash
curl -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: my-own-key-123" \
  -d '{"amount": 100, "currency": "GHS"}'
```

Response: `400 Ticket not recognised. Generate a ticket first using POST /generate-ticket.`

---

## Design Decisions

### Why FastAPI?

FastAPI is built on Starlette and runs on the ASGI protocol, meaning every request handler is a native Python coroutine running inside a single event loop. This was not a convenience choice — it was a requirement. The `asyncio.Event` race condition mechanism only works when all concurrent requests share the same event loop. FastAPI guarantees that, while a threaded framework like Flask would not.

### Race Condition: asyncio.Event over polling

When a request arrives, the gateway immediately writes the key to the store with status `PROCESSING` and creates an `asyncio.Event`. If a second identical request arrives while the first is still running, it finds the `PROCESSING` record, retrieves the same `Event` object, and calls `await event.wait()`. When the first request finishes, it calls `event.set()`, which unblocks the second instantly. No polling, no sleep loops, zero added latency to the waiting request beyond what is necessary.

### Ticket System: enforcing system-generated keys

Allowing clients to use arbitrary strings as idempotency keys creates a risk: two different clients could accidentally use the same key, causing one payment to be silently swallowed by another's cached response. The ticket system prevents this. Every key is generated by the server using `secrets.token_hex(3)`, prefixed with `order-`, and registered before use. Any key not in the ticket store is rejected immediately. Generating a new ticket also invalidates any previous unused ticket for that IP, preventing key accumulation.

### SHA-256 Body Hashing

Payload comparison uses SHA-256 of the JSON-serialised body with `sort_keys=True`. This ensures that `{"amount": 100, "currency": "GHS"}` and `{"currency": "GHS", "amount": 100}` are treated as identical payloads, which is the correct semantic behaviour. Key order in JSON is arbitrary and should not affect idempotency.

### In-Memory Store

The store is a plain Python dictionary. It is simple, has no network overhead, and requires no infrastructure. The trade-off is that state is lost on server restart and cannot be shared across multiple processes. In a production deployment, this would be replaced with Redis, which provides atomic `SET NX` operations, distributed locking, and native TTL management.

### 24-Hour TTL with Background Cleanup

Idempotency records expire after 24 hours. Expiry is checked eagerly on every `get()` call so no stale record is ever returned. A background coroutine runs every hour to sweep and remove expired records from memory, preventing unbounded growth in long-running deployments.

---

## Developer's Choice: Rate Limiting

Beyond the core acceptance criteria, IP-based rate limiting was implemented as the primary developer's choice feature, alongside structured audit logging.

### IP-Based Rate Limiting

The gateway enforces a maximum of 10 requests per 60-second sliding window per client IP. This is implemented using a `collections.deque` per IP address — no Redis, no external dependency. The window slides continuously: on every request, timestamps older than 60 seconds are evicted from the front of the deque before the count is checked. This means the limit is always accurate to the current moment, unlike a fixed-window approach which can allow bursts at window boundaries.

**Why this matters in a real Fintech system:**

Without rate limiting, a misbehaving client or an automated retry loop gone wrong could flood the idempotency store with thousands of unique keys per minute. This would exhaust memory, slow down legitimate lookups, and bury audit logs in noise — making it impossible to investigate actual payment anomalies.

### Structured Audit Logging

Every significant gateway event is recorded as a structured JSON entry, written simultaneously to `stdout` (for live monitoring) and appended to `audit_log.json` (for persistent audit trail).

Events logged:

| Event | Trigger |
|---|---|
| `PAYMENT_PROCESSED` | First successful payment execution |
| `DUPLICATE_DETECTED` | Request returned from cache |
| `CONFLICT_DETECTED` | Key reused with a different body |
| `INFLIGHT_WAIT` | Request blocked waiting for an in-flight payment |
| `INFLIGHT_TIMEOUT` | In-flight wait exceeded 10 seconds |
| `RATE_LIMITED` | Request blocked by rate limiter |
| `TICKET_GENERATED` | New idempotency key issued |
| `TICKET_INVALID` | Unrecognised key rejected |
| `STATUS_CHECKED` | Payment status endpoint called |

Each entry includes: `timestamp`, `event`, `idempotency_key`, `client_ip`, and a `details` object with event-specific fields such as amount, currency, transaction ID, or hash values.

In a real Fintech environment this log can be streamed directly into Datadog, Splunk, or an ELK stack without any parsing step, giving compliance officers and fraud investigators a complete, tamper-evident record of every payment decision.

---

## Testing Strategy

The test suite contains **37 tests** covering every scenario and edge case. Tests are written with `pytest-asyncio` and `httpx` against the live FastAPI application — no mocking.

```bash
pytest tests/ -v
```

### Test Classes

| Class | What it covers |
|---|---|
| `TestHealthCheck` | Server is running and returns correct service info |
| `TestTicketGeneration` | Ticket format, uniqueness, invalidation of old tickets, used ticket survives new generation |
| `TestFirstPayment` | 201 response, correct message format, transaction ID, no cache hit header, currency normalisation |
| `TestDuplicateRequest` | 200 response, `X-Cache-Hit: true`, identical body, identical transaction ID |
| `TestConflictDetection` | 409 on body mismatch, correct error message |
| `TestInvalidTicket` | 422 for missing header, 400 for empty key, 400 for oversized key, 400 for unrecognised ticket |
| `TestPayloadValidation` | 422 for negative amount, zero amount, unsupported currency, missing fields |
| `TestPaymentStatus` | PENDING status, PROCESSING status, COMPLETED status with payment details, 404 for unknown ticket |
| `TestRaceCondition` | Concurrent requests produce exactly one 201 and one 200, identical bodies, single transaction ID |

### CI

GitHub Actions runs the full suite automatically on every push to any branch. Configuration: [.github/workflows/ci.yml](.github/workflows/ci.yml)

---

## Project Structure

```
Idempotency-Gateway/
├── app/
│   ├── __init__.py         — package marker
│   ├── main.py             — FastAPI app, lifespan context manager, CORS, background cleanup
│   ├── routes.py           — all endpoint handlers (generate-ticket, process-payment, payment-status)
│   ├── storage.py          — idempotency store with TTL expiry and asyncio.Event race condition handling
│   ├── ticket_store.py     — system-issued key registry with 30-minute TTL and IP-based invalidation
│   ├── models.py           — Pydantic request and response models with field validators
│   ├── logger.py           — structured JSONL audit logger (stdout + audit_log.json)
│   └── rate_limiter.py     — sliding-window IP rate limiter using collections.deque
├── tests/
│   ├── __init__.py
│   └── test_payment.py     — 37 async tests across 9 test classes
├── docs/
│   ├── architecture_diagram.md
│   ├── sequence_diagram.md
│   └── decision_flowchart.md
├── images/
│   ├── Architecture Diagram.png
│   ├── Sequence Diagram.webp
│   └── Decision Flowchart.drawio.svg
├── .github/
│   └── workflows/
│       └── ci.yml          — GitHub Actions CI pipeline
├── requirements.txt
├── pytest.ini
└── .gitignore
```

---

## Known Limitations

- **No persistence across restarts.** The in-memory store is cleared when the server stops. A production deployment would use Redis with atomic `SET NX` and native TTL support.
- **Single-process only.** Rate limiting and idempotency state are not shared across multiple server instances. Horizontal scaling requires a centralised store (Redis, DynamoDB).
- **No lock beyond asyncio.** The store is safe under asyncio's cooperative multitasking model but is not protected against true concurrent writes. This is acceptable for a single-process ASGI server but would require explicit locking or Redis transactions in a multi-threaded deployment.
- **Simulated payment processor.** The 2-second delay is a `asyncio.sleep` call. A real implementation would call an external payment gateway and handle its own failure modes.

---

## Summary

This project implements a complete idempotency gateway for a payment processing system. It solves the double-charging problem by enforcing exactly-once execution: every payment is tied to a system-generated ticket, the first execution is stored, and every retry receives the cached result without triggering a new payment.

Beyond the core requirements, the gateway adds a ticket enforcement system that prevents arbitrary keys from being used, a race condition handler that serialises simultaneous duplicate requests without polling, a payment status endpoint that gives clients a safe way to check before retrying, and a structured audit log that records every gateway decision for compliance and debugging.

The system is built on FastAPI for its native asyncio support, fully tested with 37 passing tests, and automated with a GitHub Actions CI pipeline.
