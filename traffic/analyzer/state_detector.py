"""
traffic/analyzer/state_detector.py —— 基于时间窗的状态机识别器

与 logcat/detector.py 完全同构，输入是流量样本，输出 TrafficState。
"""

from typing import Dict, Any

from ..models import TrafficState, NetStatsEntry
from ..config import (
    RATE_PLAYING_MIN,
    RATE_BUFFERING_MAX,
    RATE_NETWORK_ISSUE_MAX,
    RATE_BURST_MIN,
    RATE_DROP_RATIO,
    RSSI_WEAK_THRESHOLD,
    TRAFFIC_STATE_SPACE,
)
from .bandwidth_analyzer import BandwidthAnalyzer


class TrafficStateDetector:
    """
    基于短时间窗统计 + 启发式规则状态机的候选状态检测器。
    """

    def __init__(self, window_seconds: float = 12.0):
        self.window_seconds = window_seconds
        self.bw = BandwidthAnalyzer(window_seconds=window_seconds)
        self.current_state = TrafficState("NORMAL_OR_NO_STRONG_EVIDENCE", 0.55, "init")
        self._prev_state_name = "NORMAL_OR_NO_STRONG_EVIDENCE"

    def update(self, stats: NetStatsEntry, protocol_meta: Dict[str, Any]) -> TrafficState:
        """
        根据最新网络统计和协议元数据，更新状态机。
        """
        ts = stats.timestamp
        rx_rate = stats.rx_rate
        rssi = stats.rssi
        active_conn = stats.active_connections

        self.bw.add(ts, rx_rate)
        self._prev_state_name = self.current_state.state

        # 提取协议层强信号
        protocol = protocol_meta.get("protocol")
        resolution = (
            protocol_meta.get("hls_resolution")
            or protocol_meta.get("dash_resolution")
            or ""
        )
        bandwidth = (
            protocol_meta.get("hls_bandwidth")
            or protocol_meta.get("dash_bandwidth")
            or 0
        )
        framerate = protocol_meta.get("hls_framerate", 0.0)

        # 默认状态
        state = "NORMAL_OR_NO_STRONG_EVIDENCE"
        confidence = 0.55
        reason = "steady_traffic"
        counters: Dict[str, int] = {
            "avg_rate": int(self.bw.avg_rate()),
            "max_rate": int(self.bw.max_rate()),
            "latest_rate": int(rx_rate),
            "active_connections": active_conn,
        }

        # -------------------------------------------------
        # 优先级 1: APP_INTERNAL_ERROR（应用层协议异常）
        # -------------------------------------------------
        # 严格条件：活跃连接突然归零 + 此前有播放流量 + RSSI 正常 + 持续 5 秒无流量
        if (
            active_conn == 0
            and self.bw.max_rate() > RATE_PLAYING_MIN
            and rssi is not None
            and rssi > RSSI_WEAK_THRESHOLD
            and self.bw.is_sustained_low(RATE_NETWORK_ISSUE_MAX, duration_sec=5.0)
        ):
            state = "APP_INTERNAL_ERROR"
            confidence = 0.75
            reason = "connections_dropped_unexpectedly"

        # -------------------------------------------------
        # 优先级 2: NETWORK_ISSUE（网络层异常）
        # -------------------------------------------------
        # 2a: RSSI 极差且低流量 — 真弱网/断网
        elif (
            rssi is not None
            and rssi < RSSI_WEAK_THRESHOLD
            and rx_rate < RATE_NETWORK_ISSUE_MAX
        ):
            state = "NETWORK_ISSUE"
            confidence = 0.90
            reason = f"rssi_{rssi}_and_low_rate"

        # 2b: 持续零流量 — 需要区分"真断网"和"后台/暂停"
        # 只有同时满足 RSSI 弱或连接数为 0 时才判定为 NETWORK_ISSUE
        elif self.bw.is_sustained_low(RATE_NETWORK_ISSUE_MAX, duration_sec=3.0):
            if (rssi is not None and rssi < -75) or active_conn == 0:
                state = "NETWORK_ISSUE"
                confidence = 0.88
                reason = "sustained_zero_rate_3s"
            else:
                # 信号正常且连接仍在，可能只是暂停/后台，降级为 NORMAL
                state = "NORMAL_OR_NO_STRONG_EVIDENCE"
                confidence = 0.55
                reason = "zero_rate_but_signal_ok_and_conn_alive"

        # -------------------------------------------------
        # 优先级 3: BUFFERING（缓冲中）
        # -------------------------------------------------
        elif (
            rx_rate < RATE_BUFFERING_MAX
            and self.bw.previous_rate() > RATE_PLAYING_MIN
            and self.bw.is_drop(RATE_DROP_RATIO)
        ):
            state = "BUFFERING"
            confidence = 0.85
            reason = "rate_drop_from_playing_to_buffering"

        elif self.bw.is_sustained_low(RATE_BUFFERING_MAX, duration_sec=2.0) and self.bw.max_rate() > RATE_PLAYING_MIN:
            state = "BUFFERING"
            confidence = 0.78
            reason = "sustained_low_rate_after_playing"

        # -------------------------------------------------
        # 优先级 4: PLAY_INIT（播放启动）
        # -------------------------------------------------
        elif self.bw.is_burst(RATE_BURST_MIN):
            state = "PLAY_INIT"
            confidence = 0.82
            reason = "silence_to_high_rate_burst"

        # -------------------------------------------------
        # 优先级 5: PLAYING（稳定播放）—— 协议层强信号
        # -------------------------------------------------
        elif protocol in {"HLS", "DASH", "FLV"} and rx_rate >= RATE_PLAYING_MIN:
            state = "PLAYING"
            confidence = 0.90
            reason = f"{protocol}_streaming_{resolution}"
            if bandwidth:
                counters["bitrate_bps"] = bandwidth
            if framerate:
                counters["framerate"] = int(framerate)

        elif rx_rate >= RATE_PLAYING_MIN and self.bw.trend() in {"stable", "rising"}:
            state = "PLAYING"
            confidence = 0.75
            reason = "stable_high_rate"

        # -------------------------------------------------
        # 优先级 6: RECOVERED（从异常恢复）
        # -------------------------------------------------
        elif self.bw.is_recovering(self._prev_state_name):
            state = "RECOVERED"
            confidence = 0.80
            reason = f"recovered_from_{self._prev_state_name}"

        # -------------------------------------------------
        # 优先级 7: PAGE_SWITCH（页面/房间切换）
        # -------------------------------------------------
        elif (
            active_conn > 0
            and rx_rate < RATE_PLAYING_MIN
            and self.bw.is_drop(0.5)
            and self.bw.trend() == "rising"
        ):
            # 老流量断掉，新流量尚未达到播放级别
            state = "PAGE_SWITCH"
            confidence = 0.60
            reason = "rate_drop_with_rising_trend"

        # -------------------------------------------------
        # 保底：若协议层解析到分辨率或检测到 FLV 但速率低，仍以协议为准降级为 BUFFERING
        # -------------------------------------------------
        if (resolution or protocol == "FLV") and state == "NORMAL_OR_NO_STRONG_EVIDENCE":
            state = "BUFFERING"
            confidence = 0.65
            reason = f"protocol_detected_{protocol}_but_low_rate"
            if resolution:
                counters["detected_resolution"] = resolution

        self.current_state = TrafficState(
            state=state,
            confidence=round(confidence, 3),
            reason=reason,
            counters=counters,
        )
        return self.current_state
