import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.rate_limiter import rate_limiter
from app.routes import router
from app.storage import store
from app.ticket_store import ticket_store

logger = logging.getLogger("idempotency_gateway")

CLEANUP_INTERVAL_SECONDS = 3600
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        expired_count = await store.cleanup_expired()
        stale_ip_count = rate_limiter.cleanup_stale_ips()
        expired_ticket_count = ticket_store.cleanup_expired()
        logger.info(
            "Periodic cleanup: removed %d expired keys, %d stale IP windows, %d expired tickets",
            expired_count,
            stale_ip_count,
            expired_ticket_count,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.connect(REDIS_URL)
    cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info(
        "Idempotency Gateway started. Redis connected. Background cleanup scheduled every %ds.",
        CLEANUP_INTERVAL_SECONDS,
    )
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await store.close()
    logger.info("Idempotency Gateway shut down.")


app = FastAPI(
    title="Idempotency Gateway",
    description="A payment idempotency layer that guarantees exactly-once processing for FinSafe Transactions Ltd.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router)
