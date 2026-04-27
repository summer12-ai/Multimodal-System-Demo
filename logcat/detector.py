"""
detector.py —— 基于时间窗与状态机的模式识别
"""

from collections import deque, Counter
from datetime import datetime
from typing import Deque

from .models import LogEvent, DetectorState


def _to_time(ts: str) -> datetime:
    # 输入格式 "MM-DD HH:MM:SS.mmm"
    now = datetime.now()
    try:
        return datetime.strptime(f"{now.year}-{ts}", "%Y-%m-%d %H:%M:%S.%f")
    except Exception:
        return now


class WindowStateDetector:
    """
    基于短时间窗统计 + 启发式规则状态机的候选状态检测器。
    """

    def __init__(self, window_seconds: int = 12):
        self.window_seconds = window_seconds
        self.events: Deque[LogEvent] = deque()
        self.current_state = DetectorState(state="IDLE", confidence=0.1, reason="init", counters={})

    def _evict_old(self):
        if not self.events:
            return
        newest_t = _to_time(self.events[-1].timestamp)
        while self.events:
            oldest_t = _to_time(self.events[0].timestamp)
            if (newest_t - oldest_t).total_seconds() <= self.window_seconds:
                break
            self.events.popleft()

    def _count(self) -> Counter:
        return Counter(e.event_type for e in self.events)

    @staticmethod
    def _is_severe_internal_error(event: LogEvent) -> bool:
        if event.event_type != "internal_error":
            return False
        template = (event.template or "").lower()
        severe_words = (
            "fatal",
            "crash",
            "nullpointer",
            "illegalstate",
            "segmentation fault",
            "anr in",
            "java.lang.",
        )
        if any(w in template for w in severe_words):
            return True
        return event.level in {"E", "F"}

    def _count_severe_internal_error(self) -> int:
        return sum(1 for e in self.events if self._is_severe_internal_error(e))

    def update(self, event: LogEvent) -> DetectorState:
        self.events.append(event)
        self._evict_old()
        c = self._count()
        severe_internal = self._count_severe_internal_error()

        state = "NORMAL"
        confidence = 0.55
        reason = "window_steady"

        # internal_error 收紧触发条件，减少误报：
        # 1) 严重错误 >=2，或
        # 2) internal_error >=4 且至少 1 条严重错误
        if severe_internal >= 2 or (c["internal_error"] >= 4 and severe_internal >= 1):
            state = "INTERNAL_ERROR"
            confidence = 0.92
            reason = (
                "severe_internal_error>=2_in_window"
                if severe_internal >= 2
                else "internal_error>=4_and_severe>=1_in_window"
            )
        elif c["timeout"] >= 2 or (c["network_issue"] + c["retry"]) >= 3:
            state = "NETWORK_ISSUE"
            confidence = 0.85
            reason = "timeout/retry/network_spike"
        elif c["buffering"] >= 2:
            state = "BUFFERING"
            confidence = 0.78
            reason = "buffering>=2_in_window"
        elif c["play_init"] >= 1:
            state = "PLAY_INIT"
            confidence = 0.72
            reason = "play_init_signal"
        elif c["page_switch"] >= 1:
            state = "PAGE_SWITCH"
            confidence = 0.68
            reason = "page_switch_signal"
        elif c["recovered"] >= 1 and self.current_state.state in {"BUFFERING", "NETWORK_ISSUE", "INTERNAL_ERROR"}:
            state = "RECOVERED"
            confidence = 0.75
            reason = "recovered_after_abnormal_state"

        self.current_state = DetectorState(
            state=state,
            confidence=confidence,
            reason=reason,
            counters={**dict(c), "severe_internal_error": severe_internal},
        )
        return self.current_state
