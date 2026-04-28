"""
traffic/analyzer/bandwidth_analyzer.py —— 带宽时间序列分析工具

提供速率趋势、突发检测、骤降检测等基础算法，供 StateDetector 调用。
"""

from collections import deque
from typing import Deque, List, Optional, Tuple


class BandwidthAnalyzer:
    """
    维护一个滑动窗口的速率样本，计算趋势和异常。
    """

    def __init__(self, window_seconds: float = 12.0):
        self.window_seconds = window_seconds
        self.samples: Deque[Tuple[float, float]] = deque()  # [(timestamp, rx_rate), ...]

    def add(self, timestamp: float, rx_rate: float):
        self.samples.append((timestamp, rx_rate))
        self._evict_old(timestamp)

    def _evict_old(self, now: float):
        while self.samples and (now - self.samples[0][0]) > self.window_seconds:
            self.samples.popleft()

    def avg_rate(self) -> float:
        if not self.samples:
            return 0.0
        return sum(s[1] for s in self.samples) / len(self.samples)

    def max_rate(self) -> float:
        if not self.samples:
            return 0.0
        return max(s[1] for s in self.samples)

    def min_rate(self) -> float:
        if not self.samples:
            return 0.0
        return min(s[1] for s in self.samples)

    def latest_rate(self) -> float:
        if not self.samples:
            return 0.0
        return self.samples[-1][1]

    def previous_rate(self) -> float:
        """倒数第二个样本的速率"""
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-2][1]

    def trend(self) -> str:
        """返回 'rising' / 'falling' / 'stable' / 'unknown' """
        if len(self.samples) < 3:
            return "unknown"
        rates = [s[1] for s in self.samples]
        first_avg = sum(rates[:3]) / 3
        last_avg = sum(rates[-3:]) / 3
        diff = last_avg - first_avg
        threshold = max(self.avg_rate() * 0.1, 10 * 1024)
        if diff > threshold:
            return "rising"
        if diff < -threshold:
            return "falling"
        return "stable"

    def is_burst(self, threshold: int, prev_silence_threshold: int = 50 * 1024) -> bool:
        """
        检测播放初始化突发：
        - 当前速率 > threshold
        - 此前 2 秒内速率 < prev_silence_threshold
        """
        if len(self.samples) < 2:
            return False
        latest = self.samples[-1][1]
        if latest < threshold:
            return False
        # 找 2 秒前的样本
        now = self.samples[-1][0]
        for ts, rate in reversed(list(self.samples)[:-1]):
            if (now - ts) >= 2.0:
                return rate < prev_silence_threshold
        return False

    def is_drop(self, ratio: float = 0.80, abs_threshold: int = 50 * 1024) -> bool:
        """
        检测骤降：当前速率 < 前次速率 * (1 - ratio)，且前次速率 > abs_threshold
        """
        if len(self.samples) < 2:
            return False
        prev = self.previous_rate()
        curr = self.latest_rate()
        if prev < abs_threshold:
            return False
        return curr < prev * (1.0 - ratio)

    def is_sustained_low(self, threshold: int, duration_sec: float = 3.0) -> bool:
        """连续 duration_sec 秒内速率均低于 threshold"""
        if not self.samples:
            return False
        now = self.samples[-1][0]
        relevant = [s for s in self.samples if (now - s[0]) <= duration_sec]
        if not relevant:
            return False
        return all(s[1] < threshold for s in relevant)

    def is_recovering(self, prev_state: str, threshold: int = 200 * 1024) -> bool:
        """从 BUFFERING/NETWORK_ISSUE 恢复到稳定速率"""
        if prev_state not in {"BUFFERING", "NETWORK_ISSUE"}:
            return False
        return self.latest_rate() >= threshold and self.trend() == "rising"
