"""Persist in-flight VCALM exchanges in Askar."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.vcalm_exchange import VcalmExchange
from app.plugins.askar import AskarStorage, AskarStorageKeys

ACTIVE_KEY = "_active"
EXCHANGE_TTL_MINUTES = 15


class VcalmExchangeStore:
    def __init__(self, wallet_id: str):
        self.wallet_id = wallet_id
        self.askar = AskarStorage.for_wallet(wallet_id)

    async def abandon_active(self) -> None:
        active_id = await self.askar.fetch(AskarStorageKeys.VCALM_EXCHANGES, ACTIVE_KEY)
        if active_id:
            await self.askar.delete(AskarStorageKeys.VCALM_EXCHANGES, active_id)
            await self.askar.delete(AskarStorageKeys.VCALM_EXCHANGES, ACTIVE_KEY)

    async def save(self, exchange: VcalmExchange) -> None:
        await self.abandon_active()
        await self._write(exchange)

    async def update(self, exchange: VcalmExchange) -> None:
        await self._write(exchange)

    async def persist(self, exchange: VcalmExchange) -> None:
        existing = await self.askar.fetch(
            AskarStorageKeys.VCALM_EXCHANGES, exchange.id
        )
        if existing:
            await self.update(exchange)
        else:
            await self.save(exchange)

    async def _write(self, exchange: VcalmExchange) -> None:
        await self._write_key(exchange.id, exchange.model_dump())
        await self._write_key(ACTIVE_KEY, exchange.id)

    async def _write_key(self, key: str, value) -> None:
        existing = await self.askar.fetch(AskarStorageKeys.VCALM_EXCHANGES, key)
        if existing is not None:
            await self.askar.update(AskarStorageKeys.VCALM_EXCHANGES, key, value)
        else:
            await self.askar.store(AskarStorageKeys.VCALM_EXCHANGES, key, value)

    async def load(self, exchange_id: str) -> VcalmExchange | None:
        raw = await self.askar.fetch(AskarStorageKeys.VCALM_EXCHANGES, exchange_id)
        if not raw:
            return None
        exchange = VcalmExchange.model_validate(raw)
        if self._is_expired(exchange):
            await self.delete(exchange_id)
            return None
        if exchange.wallet_id != self.wallet_id:
            return None
        return exchange

    async def delete(self, exchange_id: str) -> None:
        await self.askar.delete(AskarStorageKeys.VCALM_EXCHANGES, exchange_id)
        active_id = await self.askar.fetch(AskarStorageKeys.VCALM_EXCHANGES, ACTIVE_KEY)
        if active_id == exchange_id:
            await self.askar.delete(AskarStorageKeys.VCALM_EXCHANGES, ACTIVE_KEY)

    @staticmethod
    def _is_expired(exchange: VcalmExchange) -> bool:
        try:
            expires = datetime.fromisoformat(exchange.expires_at)
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires

    @staticmethod
    def new_expiry() -> str:
        return (
            datetime.now(timezone.utc) + timedelta(minutes=EXCHANGE_TTL_MINUTES)
        ).isoformat()
