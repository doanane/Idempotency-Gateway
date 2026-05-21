# Architecture Diagram

The diagram below shows all components of the Idempotency Gateway and how they communicate.

![Architecture Diagram](<../images/Architecture Diagram.png>)

---

## Component Overview

| Component | Role |
|---|---|
| Client | Browser or API consumer that initiates payment requests |
| Rate Limiter | Sliding-window guard — 10 requests per 60 seconds per IP |
| Ticket Generator | Issues unique `order-XXXXXX` keys via `POST /generate-ticket` |
| Ticket Validator | Rejects any key not issued by the ticket system |
| Idempotency Store | In-memory dict storing records with SHA-256 body hash, status, and 24-hour TTL |
| Race Condition Handler | One `asyncio.Event` per in-flight key — blocks duplicate requests without polling |
| Audit Logger | Writes structured JSONL events to stdout and `audit_log.json` |
| Payment Processor | Simulated 2-second delay producing a UUID `transaction_id` |

---

```mermaid
flowchart TD
    Client(["CLIENT\nBrowser or API Consumer"])

    subgraph GW["IDEMPOTENCY GATEWAY — FastAPI v1.0.0"]
        direction TB
        RL["RATE LIMITER\n10 req / 60s per IP\nSliding window — returns 429 if exceeded"]
        TG["TICKET GENERATOR\nPOST /generate-ticket\nIssues order-XXXXXX keys — 30min TTL"]
        TV["TICKET VALIDATOR\nRejects unrecognised keys\nInvalidates old unused tickets"]
        IS["IDEMPOTENCY STORE\nIn-memory dict — 24hr TTL\nSHA-256 body hash — PROCESSING or COMPLETED"]
        AE["RACE CONDITION HANDLER\nasyncio.Event per in-flight key\nBlocks duplicates — 10s timeout"]
        AL["AUDIT LOGGER\nstdout + audit_log.json\nJSONL structured events"]
    end

    PP(["PAYMENT PROCESSOR\nSimulated 2-second delay\nUUID transaction_id"])
    LogFile(["audit_log.json"])

    Client -->|"Step 1: POST /generate-ticket"| TG
    TG -->|"Returns order-a3f8b2"| Client
    Client -->|"Step 2: POST /process-payment\nIdempotency-Key: order-a3f8b2"| RL
    RL -->|"under limit — allowed"| TV
    RL -->|"429 Too Many Requests"| Client
    TV -->|"valid ticket"| IS
    TV -->|"400 Ticket not recognised"| Client
    IS -->|"new key — process"| AE
    IS -->|"200 X-Cache-Hit: true — duplicate"| Client
    IS -->|"409 Conflict — body mismatch"| Client
    AE -->|"trigger payment"| PP
    PP -->|"result stored"| IS
    IS -->|"201 Created"| Client
    IS --> AL
    AL --> LogFile
```
