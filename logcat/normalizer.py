"""
normalizer.py —— 日志模板归一化与事件类型映射
"""

import re
from .models import LogEvent


RE_NUMBER = re.compile(r"\b\d+\b")
RE_HEX = re.compile(r"0x[0-9a-fA-F]+")
RE_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
RE_UUID = re.compile(r"\b[0-9a-fA-F]{8,}\b")


EVENT_KEYWORDS = {
    "page_switch": ["onresume", "onpause", "fragment", "activity", "navigate", "router", "enter page"],
    "play_init": ["prepare", "prepared", "start play", "player init", "open stream", "start decoder"],
    "buffering": ["buffer", "rebuffer", "stalled", "loading", "wait data", "卡顿", "缓冲"],
    "retry": ["retry", "reconnect", "重试", "重连"],
    "timeout": ["timeout", "timed out", "超时"],
    "network_issue": ["network", "socket", "dns", "unreachable", "no route", "weak net", "弱网"],
    # internal_error 仅保留高置信关键词，避免被普通 "error code" 等文案误触发
    "internal_error": [
        "exception",
        "fatal",
        "crash",
        "nullpointer",
        "illegalstate",
        "segmentation fault",
        "anr in",
        "java.lang.",
    ],
    "recovered": ["resume play", "buffer end", "network recovered", "恢复", "playing"],
}


def _normalize_message(msg: str) -> str:
    out = msg.lower()
    out = RE_IP.sub("<IP>", out)
    out = RE_HEX.sub("<HEX>", out)
    out = RE_UUID.sub("<ID>", out)
    out = RE_NUMBER.sub("<NUM>", out)
    return out


def _map_event_type(template: str) -> str:
    for event_type, words in EVENT_KEYWORDS.items():
        for word in words:
            if word in template:
                return event_type
    return "generic"


def normalize_event(event: LogEvent) -> LogEvent:
    template = _normalize_message(event.message)
    event.template = template
    event.event_type = _map_event_type(template)
    return event
