from collections.abc import Hashable
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    expires_at: float | None


class MemoryCache(Generic[T]):
    """Small in-process cache used until Redis is wired in."""

    def __init__(self) -> None:
        self._items: dict[Hashable, CacheEntry[T]] = {}

    def get(self, key: Hashable) -> T | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= monotonic():
            self._items.pop(key, None)
            return None
        return entry.value

    def set(self, key: Hashable, value: T, ttl_seconds: int | None = None) -> None:
        expires_at = monotonic() + ttl_seconds if ttl_seconds else None
        self._items[key] = CacheEntry(value=value, expires_at=expires_at)

    def clear(self) -> None:
        self._items.clear()
