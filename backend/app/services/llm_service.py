from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings


class LLMUnavailableError(RuntimeError):
    """Raised when optional LLM completion cannot be used."""


def llm_is_enabled() -> bool:
    """Return true only when the optional LLM layer is explicitly configured."""
    return settings.llm_enabled and bool(settings.llm_api_key)


def complete_text(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    """Call an OpenAI-compatible chat completion endpoint and return text."""
    if not llm_is_enabled():
        raise LLMUnavailableError("LLM is not enabled")

    payload = {
        "model": settings.llm_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = Request(
        settings.llm_base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.llm_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise LLMUnavailableError(str(exc)) from exc

    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise LLMUnavailableError("LLM response had no choices")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise LLMUnavailableError("LLM response was empty")
    return content.strip()


def complete_json(system_prompt: str, user_prompt: str) -> dict:
    """Call the LLM and parse a JSON object from its response."""
    text = complete_text(system_prompt, user_prompt, temperature=0.0)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMUnavailableError("LLM response did not contain JSON")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMUnavailableError("LLM response JSON was invalid") from exc
    if not isinstance(payload, dict):
        raise LLMUnavailableError("LLM response JSON was not an object")
    return payload
