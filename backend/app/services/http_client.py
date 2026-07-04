from __future__ import annotations

import logging
import ssl
import time
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker — thread-safe state machine for Amap / MCP calls
# ---------------------------------------------------------------------------
import threading


class CircuitBreakerState:
    """Simple circuit breaker to stop hammering a failing external service."""

    CLOSED = "closed"  # normal operation
    OPEN = "open"  # failing, fast-fail
    HALF_OPEN = "half_open"  # testing recovery

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout_seconds: int = 30,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                logger.warning(
                    "Circuit breaker [%s] OPEN after %d failures",
                    self.name,
                    self._failure_count,
                )

    def record_success(self) -> None:
        with self._lock:
            if self._state != self.CLOSED:
                logger.info("Circuit breaker [%s] CLOSED (recovered)", self.name)
            self._state = self.CLOSED
            self._failure_count = 0

    def is_open(self) -> bool:
        with self._lock:
            if self._state == self.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.reset_timeout_seconds:
                    self._state = self.HALF_OPEN
                    logger.info("Circuit breaker [%s] HALF_OPEN (probing)", self.name)
                    return False
                return True
            return False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    @property
    def last_failure_time(self) -> float:
        with self._lock:
            return self._last_failure_time


# Global circuit breakers for external integrations
amap_circuit_breaker = CircuitBreakerState(
    name="amap",
    failure_threshold=settings.amap_circuit_breaker_threshold,
    reset_timeout_seconds=settings.amap_circuit_breaker_reset_seconds,
)

mcp_circuit_breaker = CircuitBreakerState(
    name="mcp",
    failure_threshold=settings.amap_circuit_breaker_threshold,
    reset_timeout_seconds=settings.amap_circuit_breaker_reset_seconds,
)

llm_circuit_breaker = CircuitBreakerState(
    name="llm",
    failure_threshold=3,
    reset_timeout_seconds=60,
)


# ---------------------------------------------------------------------------
# SSL context
# ---------------------------------------------------------------------------
@lru_cache(maxsize=8)
def _ssl_context(verify: bool, ca_file: str) -> ssl.SSLContext:
    if not verify:
        return ssl._create_unverified_context()
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def open_url(url_or_request: str | Request, timeout: int):
    """Open an HTTP(S) URL with the app's shared SSL policy."""
    context = _ssl_context(settings.ssl_verify, settings.ssl_ca_file)
    return urlopen(url_or_request, timeout=timeout, context=context)


def explain_network_error(exc: BaseException) -> str:
    message = str(exc)
    lowered = message.lower()
    if "asn1" in lowered or "ssl" in lowered or "certificate" in lowered:
        return (
            f"{message}. HTTPS certificate/SSL validation failed. "
            "Check proxy or CA configuration; set TRIP_SSL_CA_FILE to a valid CA bundle, "
            "or set TRIP_SSL_VERIFY=false for local development only."
        )
    return message


# ---------------------------------------------------------------------------
# Retry helper with exponential backoff
# ---------------------------------------------------------------------------
def retry_on_error(
    fn,
    max_attempts: int = 3,
    base_delay_ms: int = 500,
    circuit_breaker: CircuitBreakerState | None = None,
    logger_name: str = "http_client",
):
    """Execute *fn* with exponential backoff retry and circuit-breaker awareness.

    Returns the first successful result.
    Raises the last exception if all attempts fail.
    """
    _log = logging.getLogger(logger_name)
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        # Fast-fail if circuit breaker is open (if provided)
        if circuit_breaker is not None and circuit_breaker.is_open():
            raise CircuitBreakerOpenError(
                f"Circuit breaker [{circuit_breaker.name}] is OPEN. "
                f"Skipping call to avoid overloading the failing service."
            )

        try:
            result = fn()
            if circuit_breaker is not None:
                circuit_breaker.record_success()
            return result
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if circuit_breaker is not None:
                circuit_breaker.record_failure()
            if attempt < max_attempts:
                delay = (base_delay_ms / 1000.0) * (2 ** (attempt - 1))
                _log.warning(
                    "Attempt %d/%d failed for %s. Retrying in %.2fs... Error: %s",
                    attempt,
                    max_attempts,
                    getattr(fn, "__name__", "callable"),
                    delay,
                    exc,
                )
                time.sleep(delay)
            else:
                _log.error(
                    "All %d attempts failed for %s. Last error: %s",
                    max_attempts,
                    getattr(fn, "__name__", "callable"),
                    exc,
                )

    # Should not reach here, but keep mypy happy
    if last_exc is not None:
        raise last_exc  # type: ignore[misc]
    raise RuntimeError("Unexpected: retry loop ended without exception")


class CircuitBreakerOpenError(RuntimeError):
    """Raised when attempting a call while the circuit breaker is open."""