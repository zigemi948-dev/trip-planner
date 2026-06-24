from __future__ import annotations

import re
from typing import Any

from app.graph.state import IntentConstraints
from app.services.llm_service import LLMUnavailableError, complete_json, llm_is_enabled


CITY_ALIASES = {
    # Mainland China
    "上海": "Shanghai",
    "北京": "Beijing",
    "杭州": "Hangzhou",
    "苏州": "Suzhou",
    "南京": "Nanjing",
    "广州": "Guangzhou",
    "深圳": "Shenzhen",
    "成都": "Chengdu",
    "重庆": "Chongqing",
    "西安": "Xi'an",
    "厦门": "Xiamen",
    "青岛": "Qingdao",
    "大连": "Dalian",
    "天津": "Tianjin",
    "武汉": "Wuhan",
    "长沙": "Changsha",
    "郑州": "Zhengzhou",
    "济南": "Jinan",
    "合肥": "Hefei",
    "福州": "Fuzhou",
    "昆明": "Kunming",
    "丽江": "Lijiang",
    "大理": "Dali",
    "桂林": "Guilin",
    "阳朔": "Yangshuo",
    "三亚": "Sanya",
    "海口": "Haikou",
    "哈尔滨": "Harbin",
    "沈阳": "Shenyang",
    "长春": "Changchun",
    "乌鲁木齐": "Urumqi",
    "兰州": "Lanzhou",
    "银川": "Yinchuan",
    "西宁": "Xining",
    "拉萨": "Lhasa",
    "呼和浩特": "Hohhot",
    "贵阳": "Guiyang",
    "南宁": "Nanning",
    "宁波": "Ningbo",
    "无锡": "Wuxi",
    "佛山": "Foshan",
    "珠海": "Zhuhai",
    "澳门": "Macau",
    "香港": "Hong Kong",
    "台北": "Taipei",
    "高雄": "Kaohsiung",
    # International
    "东京": "Tokyo",
    "大阪": "Osaka",
    "京都": "Kyoto",
    "首尔": "Seoul",
    "釜山": "Busan",
    "曼谷": "Bangkok",
    "清迈": "Chiang Mai",
    "新加坡": "Singapore",
    "吉隆坡": "Kuala Lumpur",
    "槟城": "Penang",
    "巴厘岛": "Bali",
    "普吉": "Phuket",
    "河内": "Hanoi",
    "胡志明": "Ho Chi Minh City",
    "巴黎": "Paris",
    "伦敦": "London",
    "罗马": "Rome",
    "米兰": "Milan",
    "佛罗伦萨": "Florence",
    "巴塞罗那": "Barcelona",
    "马德里": "Madrid",
    "阿姆斯特丹": "Amsterdam",
    "苏黎世": "Zurich",
    "纽约": "New York",
    "洛杉矶": "Los Angeles",
    "旧金山": "San Francisco",
    "温哥华": "Vancouver",
    "悉尼": "Sydney",
    "墨尔本": "Melbourne",
    # English aliases
    "shanghai": "Shanghai",
    "beijing": "Beijing",
    "hangzhou": "Hangzhou",
    "suzhou": "Suzhou",
    "nanjing": "Nanjing",
    "guangzhou": "Guangzhou",
    "shenzhen": "Shenzhen",
    "chengdu": "Chengdu",
    "chongqing": "Chongqing",
    "xian": "Xi'an",
    "xi'an": "Xi'an",
    "xiamen": "Xiamen",
    "qingdao": "Qingdao",
    "sanya": "Sanya",
    "hong kong": "Hong Kong",
    "macau": "Macau",
    "taipei": "Taipei",
    "tokyo": "Tokyo",
    "osaka": "Osaka",
    "kyoto": "Kyoto",
    "seoul": "Seoul",
    "bangkok": "Bangkok",
    "singapore": "Singapore",
    "bali": "Bali",
    "phuket": "Phuket",
    "paris": "Paris",
    "london": "London",
    "rome": "Rome",
    "new york": "New York",
    "los angeles": "Los Angeles",
    "san francisco": "San Francisco",
    "sydney": "Sydney",
    "melbourne": "Melbourne",
}

PREFERENCE_KEYWORDS = {
    "museum": {
        "museum",
        "museums",
        "history",
        "historical",
        "heritage",
        "博物馆",
        "展馆",
        "历史",
        "文博",
        "文化遗产",
        "古迹",
    },
    "food": {
        "food",
        "foods",
        "restaurant",
        "restaurants",
        "snack",
        "cafe",
        "coffee",
        "美食",
        "小吃",
        "餐厅",
        "夜市",
        "咖啡",
        "甜品",
        "本帮菜",
        "火锅",
    },
    "landmark": {
        "landmark",
        "landmarks",
        "tower",
        "skyline",
        "viewpoint",
        "地标",
        "建筑",
        "打卡",
        "城市景观",
        "观景",
        "高楼",
        "天际线",
    },
    "gallery": {
        "gallery",
        "galleries",
        "art",
        "exhibition",
        "艺术",
        "美术馆",
        "画廊",
        "展览",
        "艺术区",
        "设计",
    },
    "garden": {
        "garden",
        "gardens",
        "park",
        "parks",
        "nature",
        "botanical",
        "园林",
        "花园",
        "公园",
        "自然",
        "绿地",
        "植物园",
    },
    "library": {
        "library",
        "libraries",
        "bookstore",
        "books",
        "图书馆",
        "书店",
        "阅读",
        "文学",
    },
    "shopping": {
        "shopping",
        "mall",
        "malls",
        "market",
        "outlet",
        "购物",
        "商场",
        "市集",
        "买买买",
        "奥莱",
        "免税店",
    },
    "family": {
        "family",
        "kids",
        "children",
        "parent-child",
        "亲子",
        "儿童",
        "孩子",
        "家庭",
        "乐园",
        "游乐场",
    },
    "nightlife": {
        "nightlife",
        "bar",
        "night view",
        "night market",
        "夜景",
        "酒吧",
        "夜游",
        "夜生活",
        "夜市",
        "灯光秀",
    },
    "indoor": {
        "indoor",
        "rainy day",
        "air conditioned",
        "室内",
        "避雨",
        "不晒",
        "空调",
        "轻松",
    },
    "outdoor": {
        "outdoor",
        "walk",
        "walking",
        "citywalk",
        "hiking",
        "户外",
        "散步",
        "徒步",
        "city walk",
        "citywalk",
        "骑行",
    },
    "beach": {
        "beach",
        "island",
        "sea",
        "coast",
        "海边",
        "海岛",
        "沙滩",
        "潜水",
        "看海",
        "冲浪",
    },
    "hot_spring": {
        "hot spring",
        "onsen",
        "spa",
        "温泉",
        "泡汤",
        "汤泉",
        "养生",
    },
    "skiing": {
        "ski",
        "skiing",
        "snow",
        "滑雪",
        "雪场",
        "冰雪",
        "看雪",
    },
    "relax": {
        "relax",
        "slow travel",
        "leisure",
        "chill",
        "放松",
        "慢节奏",
        "休闲",
        "度假",
        "不赶路",
    },
    "photography": {
        "photo",
        "photography",
        "instagram",
        "拍照",
        "摄影",
        "出片",
        "网红",
    },
}

CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "俩": 2,
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
        except (LLMUnavailableError, KeyError, TypeError, ValueError):
            pass

    return _parse_with_rules(user_query)


def _parse_with_llm(user_query: str) -> IntentConstraints:
    payload = complete_json(
        "You convert travel planning requests into strict JSON. "
        "Return only keys: user_query, destination, days, budget_limit, preferences, time_window_baseline. "
        "Normalize preferences into short English tags such as museum, food, landmark, gallery, garden, "
        "shopping, family, nightlife, indoor, outdoor, beach, hot_spring, skiing, relax, photography. "
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
            for item in re.split(r"[,，、/;；]+", cleaned["preferences"])
            if item.strip()
        ]
    return cleaned


def _extract_destination(text: str, lower: str) -> str:
    for alias, destination in CITY_ALIASES.items():
        if alias in text or alias in lower:
            return destination

    english_match = re.search(r"(?:to|in|for|visit|travel(?:ing)? to)\s+([A-Z][A-Za-z\s'-]{2,24})", text)
    if english_match:
        return english_match.group(1).strip()

    chinese_match = re.search(r"(?:去|到|在|玩|游)\s*([\u4e00-\u9fff]{2,8})", text)
    if chinese_match:
        return chinese_match.group(1).strip()

    return "Shanghai"


def _extract_days(text: str, lower: str) -> int:
    match = re.search(r"(\d{1,2})\s*(?:day|days|d|天|日|晚|night|nights)", lower)
    if match:
        return _clamp_int(int(match.group(1)), 1, 14)

    match = re.search(r"([一二两俩三四五六七八九十])\s*(?:天|日|晚)", text)
    if match:
        return CHINESE_NUMBERS[match.group(1)]

    return 2


def _extract_budget(text: str, lower: str) -> float:
    patterns = [
        r"(?:budget|under|within|less than|no more than|around|about)\D{0,16}(\d{2,6})",
        r"(?:预算|不超过|控制在|以内|低于|大概|左右|人均)\D{0,12}(\d{2,6})",
        r"(\d{2,6})\s*(?:rmb|cny|yuan|元|块|人民币)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower if pattern.startswith("(?:budget") else text)
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
    normalized = (
        text.replace("上午", "")
        .replace("早上", "")
        .replace("中午", "")
        .replace("晚上", "")
        .replace("下午", "")
        .replace("点半", ":30")
        .replace("点", ":00")
    )
    match = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(?:-|~|到|至|—|--)\s*(\d{1,2})(?::(\d{2}))?",
        normalized,
    )
    if not match:
        return None
    start_hour = _clamp_int(int(match.group(1)), 0, 23)
    start_minute = _clamp_int(int(match.group(2) or 0), 0, 59)
    end_hour = _clamp_int(int(match.group(3)), 0, 23)
    end_minute = _clamp_int(int(match.group(4) or 0), 0, 59)
    if end_hour <= 8 and start_hour >= 8:
        end_hour += 12
    return (f"{start_hour:02d}:{start_minute:02d}", f"{end_hour:02d}:{end_minute:02d}")


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
