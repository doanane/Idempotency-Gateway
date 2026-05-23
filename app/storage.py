import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as aioredis

KEY_TTL_SECONDS = 24 * 3600
REDIS_KEY_PREFIX = "idempotency:"

STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"


@dataclass
class StorageRecord:
    idempotency_key: str
    body_hash: str
    status: str
    response: Optional[dict]
    created_at: datetime
    expires_at: datetime


class IdempotencyStore:
    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._events: dict[str, asyncio.Event] = {}

    def connect(self, redis_url: str = "redis://localhost:6379") -> None:
        if self._redis is None:
            self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def _redis_key(self, key: str) -> str:
        return f"{REDIS_KEY_PREFIX}{key}"

    def hash_body(self, body: dict) -> str:
        serialised = json.dumps(body, sort_keys=True)
        return hashlib.sha256(serialised.encode()).hexdigest()

    async def get(self, key: str) -> Optional[StorageRecord]:
        raw = await self._redis.get(self._redis_key(key))
        if raw is None:
            return None
        data = json.loads(raw)
        return StorageRecord(
            idempotency_key=data["idempotency_key"],
            body_hash=data["body_hash"],
            status=data["status"],
            response=data.get("response"),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )

    async def mark_processing(self, key: str, body_hash: str) -> Optional[asyncio.Event]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=KEY_TTL_SECONDS)
        record = {
            "idempotency_key": key,
            "body_hash": body_hash,
            "status": STATUS_PROCESSING,
            "response": None,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        claimed = await self._redis.set(
            self._redis_key(key),
            json.dumps(record),
            ex=KEY_TTL_SECONDS,
            nx=True,
        )
        if not claimed:
            return None
        event = asyncio.Event()
        self._events[key] = event
        return event

    async def mark_completed(self, key: str, response: dict) -> None:
        raw = await self._redis.get(self._redis_key(key))
        if raw is None:
            return
        data = json.loads(raw)
        data["status"] = STATUS_COMPLETED
        data["response"] = response
        ttl = await self._redis.ttl(self._redis_key(key))
        remaining = ttl if ttl > 0 else KEY_TTL_SECONDS
        await self._redis.set(self._redis_key(key), json.dumps(data), ex=remaining)
        event = self._events.pop(key, None)
        if event:
            event.set()

    def get_event(self, key: str) -> Optional[asyncio.Event]:
        return self._events.get(key)

    async def cleanup_expired(self) -> int:
        return 0


store = IdempotencyStore()
