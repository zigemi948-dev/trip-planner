from __future__ import annotations

import ssl
from functools import lru_cache
from urllib.request import Request, urlopen

from app.core.config import settings


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
