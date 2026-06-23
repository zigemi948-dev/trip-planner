from __future__ import annotations

import re
from typing import Any

from app.graph.state import IntentConstraints
from app.services.llm_service import LLMUnavailableError, complete_json, llm_is_enabled


CITY_ALIASES = {
    "上海": "Shanghai",
    "北京": "Beijing",
    "杭州": "Hangzhou",
    "苏州": "Suzhou",
    "南京": "Nanjing",
    "广州": "Guangzhou",
    "深圳": "Shenzhen",
    "成都": "Chengdu",
    "西安": "Xi'an",
    "重庆": "Chongqing",
    "shanghai": "Shanghai",
    "beijing": "Beijing",
    "hangzhou": "Hangzhou",
    "suzhou": "Suzhou",
    "nanjing": "Nanjing",
    "guangzhou": "Guangzhou",
    "shenzhen": "Shenzhen",
    "chengdu": "Chengdu",
    "chongqing": "Chongqing",
    "tokyo": "Tokyo",
    "osaka": "Osaka",
    "kyoto": "Kyoto",
    "seoul": "Seoul",
}

PREFERENCE_KEYWORDS = {
    "museum": {"museum", "museums", "博物馆", "展馆", "历史"},
    "food": {"food", "foods", "restaurant", "restaurants", "美食", "小吃", "餐厅", "夜市"},
    "landmark": {"landmark", "landmarks", "地标", "塔", "建筑", "打卡"},
    "gallery": {"gallery", "galleries", "art", "艺术", "美术馆", "画廊"},
    "garden": {"garden", "gardens", "park", "parks", "园林", "花园", "公园"},
    "library": {"library", "libraries", "书店", "图书馆"},
    "shopping": {"shopping", "mall", "malls", "购物", "商场"},
    "family": {"family", "kids", "亲子", "儿童", "孩子"},
}

CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def parse_trip_intent(user_query: str) -> IntentConstraints:
    """Normalize natural language into deterministic planning constraints."""
    if llm_is_enabled():
        try:
            return _parse_with_llm(user_query)
        except LLMUnavailableError:
            # The deterministic parser keeps the app useful when a remote model
            # is unavailable, rate-limited, or returns invalid JSON.
            pass

    return _parse_with_rules(user_query)


def _parse_with_llm(user_query: str) -> IntentConstraints:
    payload = complete_json(
        "You convert travel planning requests into strict JSON. "
        "Return only keys: user_query, destination, days, budget_limit, preferences, time_window_baseline. "
        "Never invent coordinates or routes.",
        user_query,
    )
    payload["user_query"] = user_query
    return IntentConstraints.model_validate(_clean_payload(payload))


def _parse_with_rules(user_query: str) -> IntentConstraints:
    normalized = user_query.strip()
    lower = normalized.lower()
    payload: dict[str, Any] = {
        "user_query": normalized,
        "destination": _extract_destination(normalized, lower),
        "days": _extract_days(normalized, lower),
        "budget_limit": _extract_budget(normalized, lower),
        "preferences": _extract_preferences(normalized, lower),
    }
    time_window = _extract_time_window(normalized)
    if time_window:
        payload["time_window_baseline"] = time_window
    return IntentConstraints.model_validate(payload)


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    if isinstance(cleaned.get("preferences"), str):
        cleaned["preferences"] = [
            item.strip()
            for item in cleaned["preferences"].split(",")
            if item.strip()
        ]
    return cleaned


def _extract_destination(text: str, lower: str) -> str:
    for alias, destination in CITY_ALIASES.items():
        if alias in text or alias in lower:
            return destination

    match = re.search(r"(?:to|in|for)\s+([A-Z][A-Za-z\s'-]{2,24})", text)
    if match:
        return match.group(1).strip()

    return "Shanghai"


def _extract_days(text: str, lower: str) -> int:
    match = re.search(r"(\d{1,2})\s*(?:day|days|天|日)", lower)
    if match:
        return _clamp_int(int(match.group(1)), 1, 14)

    match = re.search(r"([一二两三四五六七八九十])\s*(?:天|日)", text)
    if match:
        return CHINESE_NUMBERS[match.group(1)]

    return 2


def _extract_budget(text: str, lower: str) -> float:
    patterns = [
        r"(?:budget|under|within|less than)\D{0,12}(\d{2,6})",
        r"(?:预算|不超过|控制在|以内)\D{0,12}(\d{2,6})",
        r"(\d{2,6})\s*(?:rmb|cny|yuan|元|块)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower if "budget" in pattern or "under" in pattern else text)
        if match:
            return float(match.group(1))
    return 800.0


def _extract_preferences(text: str, lower: str) -> list[str]:
    preferences: list[str] = []
    for preference, keywords in PREFERENCE_KEYWORDS.items():
        if any(keyword in lower or keyword in text for keyword in keywords):
            preferences.append(preference)
    return preferences or ["museum", "food", "landmark"]


def _extract_time_window(text: str) -> tuple[str, str] | None:
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(?:-|~|到|至)\s*(\d{1,2})(?::(\d{2}))?", text)
    if not match:
        return None
    start_hour = _clamp_int(int(match.group(1)), 0, 23)
    start_minute = _clamp_int(int(match.group(2) or 0), 0, 59)
    end_hour = _clamp_int(int(match.group(3)), 0, 23)
    end_minute = _clamp_int(int(match.group(4) or 0), 0, 59)
    return (f"{start_hour:02d}:{start_minute:02d}", f"{end_hour:02d}:{end_minute:02d}")


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
