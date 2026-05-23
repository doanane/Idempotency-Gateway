import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from app.logger import (
    EVENT_CONFLICT_DETECTED,
    EVENT_DUPLICATE_DETECTED,
    EVENT_INFLIGHT_TIMEOUT,
    EVENT_INFLIGHT_WAIT,
    EVENT_PAYMENT_PROCESSED,
    EVENT_RATE_LIMITED,
    EVENT_STATUS_CHECKED,
    EVENT_TICKET_GENERATED,
    EVENT_TICKET_INVALID,
    read_logs,
    write_log,
)
from app.models import ErrorResponse, PaymentRequest, PaymentResponse
from app.rate_limiter import RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS, rate_limiter
from app.storage import STATUS_COMPLETED, STATUS_PROCESSING, store
from app.ticket_store import ticket_store

IDEMPOTENCY_KEY_MAX_LENGTH = 255
PROCESSING_DELAY_SECONDS = 2
INFLIGHT_TIMEOUT_SECONDS = 10

router = APIRouter()


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _format_amount(amount: float) -> str:
    return str(int(amount)) if amount == int(amount) else str(amount)


@router.get("/")
async def health_check() -> dict:
    return {
        "service": "Idempotency Gateway",
        "status": "healthy",
        "version": "1.0.0",
    }


@router.get("/logs")
async def get_logs(limit: int | None = None) -> JSONResponse:
    entries = read_logs(limit=limit)
    return JSONResponse(
        status_code=200,
        content={"total": len(entries), "logs": entries},
    )


@router.post("/generate-ticket", status_code=201)
async def generate_ticket(request: Request) -> JSONResponse:
    client_ip = _get_client_ip(request)

    if not rate_limiter.is_allowed(client_ip):
        error = ErrorResponse(
            error="Too many requests.",
            hint=f"You may send at most {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS} seconds.",
        )
        return JSONResponse(status_code=429, content=error.model_dump())

    ticket_id = ticket_store.generate(client_ip)
    write_log(EVENT_TICKET_GENERATED, ticket_id, client_ip)

    return JSONResponse(
        status_code=201,
        content={
            "ticket_id": ticket_id,
            "message": "Use this ticket_id as your Idempotency-Key when calling POST /process-payment.",
            "note": "Generating a new ticket invalidates your previous unused ticket. This ticket expires in 30 minutes.",
        },
    )


@router.get("/payment-status/{ticket_id}")
async def get_payment_status(ticket_id: str, request: Request) -> JSONResponse:
    client_ip = _get_client_ip(request)
    write_log(EVENT_STATUS_CHECKED, ticket_id, client_ip)

    record = await store.get(ticket_id)

    if record is not None:
        response_data: dict = {
            "ticket_id": ticket_id,
            "status": record.status,
            "created_at": record.created_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
        }
        if record.status == STATUS_COMPLETED:
            response_data["payment"] = record.response
        return JSONResponse(status_code=200, content=response_data)

    is_valid, _ = ticket_store.validate(ticket_id)
    if is_valid:
        return JSONResponse(
            status_code=200,
            content={
                "ticket_id": ticket_id,
                "status": "PENDING",
                "message": "Ticket issued but no payment has been initiated with it yet.",
            },
        )

    error = ErrorResponse(error="No payment record found for this ticket ID.")
    return JSONResponse(status_code=404, content=error.model_dump())


@router.post("/process-payment", status_code=201)
async def process_payment(
    request: Request,
    response: Response,
    body: PaymentRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JSONResponse:
    client_ip = _get_client_ip(request)

    if not rate_limiter.is_allowed(client_ip):
        write_log(EVENT_RATE_LIMITED, idempotency_key, client_ip)
        error = ErrorResponse(
            error="Too many requests.",
            hint=f"You may send at most {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS} seconds.",
        )
        return JSONResponse(status_code=429, content=error.model_dump())

    if not idempotency_key:
        error = ErrorResponse(
            error="Idempotency-Key header must not be empty.",
            hint="Generate a valid ticket first using POST /generate-ticket, then use the returned ticket_id as your Idempotency-Key.",
        )
        return JSONResponse(status_code=400, content=error.model_dump())

    if len(idempotency_key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        error = ErrorResponse(
            error="Idempotency-Key exceeds maximum allowed length.",
            hint=f"Keys must be {IDEMPOTENCY_KEY_MAX_LENGTH} characters or fewer.",
        )
        return JSONResponse(status_code=400, content=error.model_dump())

    is_valid, reason = ticket_store.validate(idempotency_key)
    if not is_valid:
        write_log(EVENT_TICKET_INVALID, idempotency_key, client_ip, {"reason": reason})
        error = ErrorResponse(
            error=reason,
            hint="Use POST /generate-ticket to get a valid ticket_id, then retry with that as your Idempotency-Key.",
        )
        return JSONResponse(status_code=400, content=error.model_dump())

    incoming_hash = store.hash_body(body.model_dump())
    record = await store.get(idempotency_key)

    if record is not None:
        if record.body_hash != incoming_hash:
            write_log(
                EVENT_CONFLICT_DETECTED,
                idempotency_key,
                client_ip,
                {"stored_hash": record.body_hash, "incoming_hash": incoming_hash},
            )
            error = ErrorResponse(
                error="Idempotency key already used for a different request body.",
                hint="Use a new ticket from POST /generate-ticket if you intend to make a different payment.",
            )
            return JSONResponse(status_code=409, content=error.model_dump())

        if record.status == STATUS_PROCESSING:
            write_log(EVENT_INFLIGHT_WAIT, idempotency_key, client_ip)
            event = store.get_event(idempotency_key)
            if event is not None:
                try:
                    await asyncio.wait_for(event.wait(), timeout=INFLIGHT_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    write_log(EVENT_INFLIGHT_TIMEOUT, idempotency_key, client_ip)
                    error = ErrorResponse(
                        error="The original request is taking too long.",
                        hint="Please retry after a moment.",
                    )
                    return JSONResponse(status_code=503, content=error.model_dump())

            record = await store.get(idempotency_key)

        write_log(EVENT_DUPLICATE_DETECTED, idempotency_key, client_ip)
        return JSONResponse(
            status_code=200,
            content=record.response,
            headers={"X-Cache-Hit": "true"},
        )

    event = await store.mark_processing(idempotency_key, incoming_hash)

    if event is None:
        # SET NX failed: a concurrent request claimed this key between our
        # store.get() returning None and our mark_processing() call.
        # Wait for that request to finish, then return its cached response.
        write_log(EVENT_INFLIGHT_WAIT, idempotency_key, client_ip)
        inflight_event = store.get_event(idempotency_key)
        if inflight_event is not None:
            try:
                await asyncio.wait_for(inflight_event.wait(), timeout=INFLIGHT_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                write_log(EVENT_INFLIGHT_TIMEOUT, idempotency_key, client_ip)
                error = ErrorResponse(
                    error="The original request is taking too long.",
                    hint="Please retry after a moment.",
                )
                return JSONResponse(status_code=503, content=error.model_dump())
        record = await store.get(idempotency_key)
        write_log(EVENT_DUPLICATE_DETECTED, idempotency_key, client_ip)
        return JSONResponse(
            status_code=200,
            content=record.response,
            headers={"X-Cache-Hit": "true"},
        )

    ticket_store.mark_used(idempotency_key)

    await asyncio.sleep(PROCESSING_DELAY_SECONDS)

    transaction_id = str(uuid.uuid4())
    processed_at = datetime.now(timezone.utc).isoformat()
    amount_str = _format_amount(body.amount)

    payment_response = PaymentResponse(
        message=f"Charged {amount_str} {body.currency}",
        status="success",
        idempotency_key=idempotency_key,
        transaction_id=transaction_id,
        processed_at=processed_at,
    )

    response_dict = payment_response.model_dump()
    await store.mark_completed(idempotency_key, response_dict)

    write_log(
        EVENT_PAYMENT_PROCESSED,
        idempotency_key,
        client_ip,
        {"amount": body.amount, "currency": body.currency, "transaction_id": transaction_id},
    )

    return JSONResponse(status_code=201, content=response_dict)
