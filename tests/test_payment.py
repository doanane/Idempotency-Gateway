import asyncio

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.rate_limiter import rate_limiter
from app.storage import store
from app.ticket_store import ticket_store


@pytest_asyncio.fixture(autouse=True)
async def reset_state():
    store._records.clear()
    store._events.clear()
    rate_limiter._windows.clear()
    ticket_store._tickets.clear()
    ticket_store._pending_per_ip.clear()
    yield
    store._records.clear()
    store._events.clear()
    rate_limiter._windows.clear()
    ticket_store._tickets.clear()
    ticket_store._pending_per_ip.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def get_ticket(client: AsyncClient) -> str:
    response = await client.post("/generate-ticket")
    return response.json()["ticket_id"]


class TestHealthCheck:
    async def test_returns_healthy_status(self, client: AsyncClient):
        response = await client.get("/")
        assert response.status_code == 200

    async def test_response_contains_service_name(self, client: AsyncClient):
        response = await client.get("/")
        assert response.json()["service"] == "Idempotency Gateway"


class TestTicketGeneration:
    async def test_returns_201(self, client: AsyncClient):
        response = await client.post("/generate-ticket")
        assert response.status_code == 201

    async def test_ticket_id_starts_with_order_prefix(self, client: AsyncClient):
        response = await client.post("/generate-ticket")
        assert response.json()["ticket_id"].startswith("order-")

    async def test_each_ticket_is_unique(self, client: AsyncClient):
        first = await client.post("/generate-ticket")
        second = await client.post("/generate-ticket")
        assert first.json()["ticket_id"] != second.json()["ticket_id"]

    async def test_new_ticket_invalidates_previous_unused_ticket(self, client: AsyncClient):
        first_ticket = await get_ticket(client)
        await client.post("/generate-ticket")

        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": first_ticket},
        )
        assert response.status_code == 400
        assert "Ticket not recognised" in response.json()["error"]

    async def test_used_ticket_remains_valid_after_new_ticket_is_generated(self, client: AsyncClient):
        first_ticket = await get_ticket(client)
        await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": first_ticket},
        )
        await client.post("/generate-ticket")

        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": first_ticket},
        )
        assert response.status_code == 200
        assert response.headers.get("x-cache-hit") == "true"


class TestFirstPayment:
    async def test_returns_201_on_first_request(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 201

    async def test_response_message_contains_amount_and_currency(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.json()["message"] == "Charged 100 GHS"

    async def test_response_contains_transaction_id(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 50, "currency": "USD"},
            headers={"Idempotency-Key": key},
        )
        assert "transaction_id" in response.json()

    async def test_response_contains_processed_at(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 50, "currency": "USD"},
            headers={"Idempotency-Key": key},
        )
        assert "processed_at" in response.json()

    async def test_no_cache_hit_header_on_first_request(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.headers.get("x-cache-hit") is None

    async def test_currency_is_normalised_to_uppercase(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 20, "currency": "ghs"},
            headers={"Idempotency-Key": key},
        )
        assert response.json()["message"] == "Charged 20 GHS"


class TestDuplicateRequest:
    async def test_returns_200_on_duplicate(self, client: AsyncClient):
        key = await get_ticket(client)
        await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 200

    async def test_cache_hit_header_is_true_on_duplicate(self, client: AsyncClient):
        key = await get_ticket(client)
        await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.headers.get("x-cache-hit") == "true"

    async def test_duplicate_returns_same_response_body(self, client: AsyncClient):
        key = await get_ticket(client)
        first = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        second = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert first.json() == second.json()

    async def test_duplicate_returns_same_transaction_id(self, client: AsyncClient):
        key = await get_ticket(client)
        first = await client.post(
            "/process-payment",
            json={"amount": 75, "currency": "EUR"},
            headers={"Idempotency-Key": key},
        )
        second = await client.post(
            "/process-payment",
            json={"amount": 75, "currency": "EUR"},
            headers={"Idempotency-Key": key},
        )
        assert first.json()["transaction_id"] == second.json()["transaction_id"]


class TestConflictDetection:
    async def test_returns_409_on_body_mismatch(self, client: AsyncClient):
        key = await get_ticket(client)
        await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        response = await client.post(
            "/process-payment",
            json={"amount": 500, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 409

    async def test_conflict_error_message_is_correct(self, client: AsyncClient):
        key = await get_ticket(client)
        await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "USD"},
            headers={"Idempotency-Key": key},
        )
        assert "different request body" in response.json()["error"]


class TestInvalidTicket:
    async def test_returns_400_for_unrecognised_key(self, client: AsyncClient):
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": "some-random-key"},
        )
        assert response.status_code == 400

    async def test_error_message_directs_user_to_generate_ticket(self, client: AsyncClient):
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": "made-up-123"},
        )
        assert "generate-ticket" in response.json()["error"].lower() or \
               "generate-ticket" in response.json()["hint"].lower()

    async def test_returns_422_when_idempotency_key_is_missing(self, client: AsyncClient):
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
        )
        assert response.status_code == 422

    async def test_returns_400_when_idempotency_key_is_empty(self, client: AsyncClient):
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": ""},
        )
        assert response.status_code == 400

    async def test_returns_400_when_key_exceeds_max_length(self, client: AsyncClient):
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": "x" * 256},
        )
        assert response.status_code == 400


class TestPayloadValidation:
    async def test_returns_422_on_negative_amount(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": -50, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 422

    async def test_returns_422_on_zero_amount(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 0, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 422

    async def test_returns_422_on_unsupported_currency(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "XYZ"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 422

    async def test_returns_422_when_amount_is_missing(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 422

    async def test_returns_422_when_currency_is_missing(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.post(
            "/process-payment",
            json={"amount": 100},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 422


class TestPaymentStatus:
    async def test_returns_pending_for_issued_but_unused_ticket(self, client: AsyncClient):
        key = await get_ticket(client)
        response = await client.get(f"/payment-status/{key}")
        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"

    async def test_returns_processing_while_payment_is_in_flight(self, client: AsyncClient):
        key = await get_ticket(client)
        asyncio.create_task(
            client.post(
                "/process-payment",
                json={"amount": 100, "currency": "GHS"},
                headers={"Idempotency-Key": key},
            )
        )
        await asyncio.sleep(0.1)
        response = await client.get(f"/payment-status/{key}")
        assert response.status_code == 200
        assert response.json()["status"] in ("PROCESSING", "COMPLETED")

    async def test_returns_completed_after_payment_finishes(self, client: AsyncClient):
        key = await get_ticket(client)
        await client.post(
            "/process-payment",
            json={"amount": 100, "currency": "GHS"},
            headers={"Idempotency-Key": key},
        )
        response = await client.get(f"/payment-status/{key}")
        assert response.status_code == 200
        assert response.json()["status"] == "COMPLETED"

    async def test_completed_status_includes_payment_details(self, client: AsyncClient):
        key = await get_ticket(client)
        await client.post(
            "/process-payment",
            json={"amount": 250, "currency": "NGN"},
            headers={"Idempotency-Key": key},
        )
        response = await client.get(f"/payment-status/{key}")
        assert "payment" in response.json()
        assert response.json()["payment"]["message"] == "Charged 250 NGN"

    async def test_returns_404_for_unknown_ticket(self, client: AsyncClient):
        response = await client.get("/payment-status/order-unknown")
        assert response.status_code == 404


class TestRaceCondition:
    async def test_concurrent_requests_process_payment_exactly_once(self, client: AsyncClient):
        key = await get_ticket(client)
        payload = {"amount": 200, "currency": "NGN"}

        results = await asyncio.gather(
            client.post("/process-payment", json=payload, headers={"Idempotency-Key": key}),
            client.post("/process-payment", json=payload, headers={"Idempotency-Key": key}),
        )

        status_codes = sorted(r.status_code for r in results)
        assert status_codes == [200, 201]

    async def test_concurrent_requests_return_identical_response_bodies(self, client: AsyncClient):
        key = await get_ticket(client)
        payload = {"amount": 200, "currency": "NGN"}

        results = await asyncio.gather(
            client.post("/process-payment", json=payload, headers={"Idempotency-Key": key}),
            client.post("/process-payment", json=payload, headers={"Idempotency-Key": key}),
        )

        assert results[0].json() == results[1].json()

    async def test_concurrent_requests_share_the_same_transaction_id(self, client: AsyncClient):
        key = await get_ticket(client)
        payload = {"amount": 150, "currency": "GBP"}

        results = await asyncio.gather(
            client.post("/process-payment", json=payload, headers={"Idempotency-Key": key}),
            client.post("/process-payment", json=payload, headers={"Idempotency-Key": key}),
        )

        ids = {r.json()["transaction_id"] for r in results}
        assert len(ids) == 1
