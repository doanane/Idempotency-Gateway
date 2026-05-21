# Decision Flowchart

The flowchart below shows every decision the gateway makes when a `POST /process-payment` request arrives.

![Decision Flowchart](<../images/Decision Flowchart.drawio.svg>)

---

## Decision Logic Summary

Every incoming request passes through these checks in order. The first failure exits immediately — no further checks run.

| Step | Check | Pass | Fail |
|---|---|---|---|
| 1 | Rate limit per IP (10 req / 60s) | Continue | 429 |
| 2 | `Idempotency-Key` header present | Continue | 422 |
| 3 | Key is not empty string | Continue | 400 |
| 4 | Key length within 255 characters | Continue | 400 |
| 5 | Key was issued by `POST /generate-ticket` | Continue | 400 |
| 6 | Request body is valid (amount, currency) | Continue | 422 |
| 7 | Key not in idempotency store | Process payment → 201 | Check body hash |
| 8 | Body hash matches stored hash | Return cached response → 200 | 409 |
| 9 | Stored status is COMPLETED | Return instantly | Await `asyncio.Event` |
| 10 | Event fires within 10 seconds | Return cached response → 200 | 503 |

---

```mermaid
flowchart TD
    START(["REQUEST ARRIVES\nPOST /process-payment"])

    D1{{"Rate limit\nexceeded?\n10 req / 60s"}}
    D2{{"Idempotency-Key\nheader present?"}}
    D3{{"Key is\nempty string?"}}
    D4{{"Key length\nover 255 chars?"}}
    D5{{"Ticket valid?\nIssued by system?"}}
    D6{{"Request body\nvalid?"}}
    D7{{"Key exists\nin store?"}}
    D8{{"Body hash\nmatches?"}}
    D9{{"Record\nstatus?"}}
    D10{{"Event fired\nwithin 10s?"}}

    E1["429 Too Many Requests"]
    E2["422 Field required\nIdempotency-Key missing"]
    E3["400 Key must\nnot be empty"]
    E4["400 Key exceeds\n255 characters"]
    E5["400 Ticket not recognised\nUse POST /generate-ticket"]
    E6["422 Validation Error\nAmount or currency invalid"]
    E7["409 Conflict\nDifferent request body"]
    E8["503 Service Unavailable\nOriginal request timed out"]

    P1["mark_processing\nasyncio.Event created"]
    P2["Execute payment\n2-second delay"]
    P3["mark_completed\nStore response\nevent.set()"]
    P4["Audit log\nPAYMENT_PROCESSED"]
    P5["Audit log\nDUPLICATE_DETECTED"]
    P6["Audit log\nCONFLICT_DETECTED"]
    P7["Await asyncio.Event\n10-second timeout"]
    P8["Read completed\nrecord from store"]

    S1(["201 CREATED\nCharged X CURRENCY"])
    S2(["200 OK\nX-Cache-Hit: true\nCached response returned"])

    START --> D1
    D1 -->|YES| E1
    D1 -->|NO| D2
    D2 -->|NO| E2
    D2 -->|YES| D3
    D3 -->|YES| E3
    D3 -->|NO| D4
    D4 -->|YES| E4
    D4 -->|NO| D5
    D5 -->|NO| E5
    D5 -->|YES| D6
    D6 -->|INVALID| E6
    D6 -->|VALID| D7
    D7 -->|NO — first request| P1
    P1 --> P2
    P2 --> P3
    P3 --> P4
    P4 --> S1
    D7 -->|YES — key found| D8
    D8 -->|MISMATCH| P6
    P6 --> E7
    D8 -->|MATCHES| D9
    D9 -->|PROCESSING| P7
    P7 -->|TIMEOUT| E8
    P7 -->|FIRED| P8
    P8 --> P5
    D9 -->|COMPLETED| P5
    P5 --> S2
```
