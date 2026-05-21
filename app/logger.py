import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG_FILE = Path("audit_log.json")

EVENT_PAYMENT_PROCESSED = "PAYMENT_PROCESSED"
EVENT_DUPLICATE_DETECTED = "DUPLICATE_DETECTED"
EVENT_CONFLICT_DETECTED = "CONFLICT_DETECTED"
EVENT_INFLIGHT_WAIT = "INFLIGHT_WAIT"
EVENT_INFLIGHT_TIMEOUT = "INFLIGHT_TIMEOUT"
EVENT_KEY_EXPIRED = "KEY_EXPIRED"
EVENT_RATE_LIMITED = "RATE_LIMITED"
EVENT_TICKET_GENERATED = "TICKET_GENERATED"
EVENT_TICKET_INVALID = "TICKET_INVALID"
EVENT_STATUS_CHECKED = "STATUS_CHECKED"

_console_logger = logging.getLogger("idempotency_gateway")
_console_logger.setLevel(logging.INFO)

if not _console_logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    )
    _console_logger.addHandler(_handler)


def read_logs(limit: int | None = None) -> list[dict]:
    if not AUDIT_LOG_FILE.exists():
        return []
    try:
        lines = AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
        entries = [json.loads(line) for line in lines if line.strip()]
        if limit is not None:
            entries = entries[-limit:]
        return entries
    except (OSError, json.JSONDecodeError):
        return []


def write_log(
    event: str,
    idempotency_key: str,
    client_ip: str,
    details: dict | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "idempotency_key": idempotency_key,
        "client_ip": client_ip,
        "details": details or {},
    }

    _console_logger.info("%s | key=%s ip=%s | %s", event, idempotency_key, client_ip, details or {})

    try:
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        _console_logger.warning("Could not write to audit log file %s", AUDIT_LOG_FILE)
