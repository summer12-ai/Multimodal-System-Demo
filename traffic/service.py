"""
traffic/service.py —— 流量子模块统一服务接口

对主控层暴露 start/stop/get_snapshot/evaluate 标准接口。
"""

import subprocess
import threading
import time
from collections import deque
from typing import Dict, Any, Optional, Deque

from .collector import StatsCollector, PcapCollector
from .analyzer import TrafficStateDetector
from .ground_truth import GroundTruthEngine
from .config import APP_PACKAGE_MAP, MAX_HISTORY_SAMPLES
from .models import GroundTruthResult, NetStatsEntry


class TrafficModuleService:
    """
    流量子模块主控适配层。
    """

    def __init__(
        self,
        target_app: str,
        device_id: Optional[str] = None,
        package_name: Optional[str] = None,
        enable_pcap: bool = True,
        window_seconds: float = 12.0,
    ):
        self.target_app = target_app
        self.device_id = device_id

        # 解析包名
        self.package_name = package_name or self._resolve_package(target_app)
        self.uid = self._resolve_uid(self.package_name)

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 子组件
        self.stats_collector = StatsCollector(
            uid=self.uid,
            package_name=self.package_name,
            device_id=device_id,
        )
        self.pcap_collector: Optional[PcapCollector] = None
        if enable_pcap and self.uid > 0:
            self.pcap_collector = PcapCollector(
                pid_provider=lambda: self.stats_collector._cached_pid,
                device_id=device_id,
            )

        self.detector = TrafficStateDetector(window_seconds=window_seconds)
        self.ground_truth = GroundTruthEngine()

        # 历史缓存（用于窗口对齐）
        self._traffic_history: Deque[Dict[str, Any]] = deque(maxlen=MAX_HISTORY_SAMPLES)
        self._ocr_history: Deque[Dict[str, Any]] = deque(maxlen=MAX_HISTORY_SAMPLES)
        self._log_history: Deque[Dict[str, Any]] = deque(maxlen=MAX_HISTORY_SAMPLES)

        self.errors = 0
        self.total_updates = 0

    # ----------------------- 包名 / UID 解析 -----------------------

    @staticmethod
    def _resolve_package(target_app: str) -> str:
        return APP_PACKAGE_MAP.get(target_app, target_app)

    def _resolve_uid(self, package_name: str) -> int:
        if not package_name:
            return -1
        cmd = ["adb"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        # Android shell 中使用 grep，命令作为整体字符串传递
        cmd += ["shell", f"dumpsys package {package_name} | grep userId"]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
            )
            # 输出示例:    userId=10234
            for line in res.stdout.splitlines():
                if "userId=" in line:
                    uid_str = line.split("userId=")[-1].strip().split()[0]
                    # 去掉可能的 ) 和 , 后缀
                    uid_str = uid_str.replace(")", "").replace(",", "").strip()
                    return int(uid_str)
        except Exception:
            pass

        # fallback: pm list packages -U
        cmd2 = ["adb"]
        if self.device_id:
            cmd2 += ["-s", self.device_id]
        cmd2 += ["shell", "pm", "list", "packages", "-U"]
        try:
            res = subprocess.run(
                cmd2,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
            )
            for line in res.stdout.splitlines():
                if package_name in line:
                    # package:com.duowan.kiwi uid:10234
                    if "uid:" in line:
                        uid_str = line.split("uid:")[-1].strip()
                        return int(uid_str)
        except Exception:
            pass

        return -1

    # ----------------------- 生命周期 -----------------------

    def start(self):
        if self._running:
            return
        if self.uid < 0:
            raise RuntimeError(f"无法解析 {self.package_name} 的 UID，请检查包名是否正确")
        self._running = True
        self.stats_collector.start()
        if self.pcap_collector:
            self.pcap_collector.start()

    def stop(self):
        self._running = False
        self.stats_collector.stop()
        if self.pcap_collector:
            self.pcap_collector.stop()

    # ----------------------- 快照获取 -----------------------

    def get_snapshot(self) -> Dict[str, Any]:
        stats = self.stats_collector.get_latest()
        protocol_meta = (
            self.pcap_collector.get_latest_protocol_meta()
            if self.pcap_collector
            else {}
        )
        packet_stats = (
            self.pcap_collector.get_packet_stats()
            if self.pcap_collector
            else {"packets": 0, "total_bytes": 0, "tcp_flags": {}, "proto_counts": {}}
        )

        entry = NetStatsEntry(
            timestamp=stats.get("timestamp", time.time()),
            uid=stats.get("uid", self.uid),
            pid=stats.get("pid", ""),
            rx_bytes=stats.get("rx_bytes", 0),
            tx_bytes=stats.get("tx_bytes", 0),
            rx_bytes_delta=stats.get("rx_bytes_delta", 0),
            tx_bytes_delta=stats.get("tx_bytes_delta", 0),
            rx_rate=stats.get("rx_rate", 0.0),
            tx_rate=stats.get("tx_rate", 0.0),
            active_connections=stats.get("active_connections", 0),
            rssi=stats.get("rssi"),
            link_speed=stats.get("link_speed"),
        )

        # 状态机更新
        state = self.detector.update(entry, protocol_meta)

        snapshot = {
            "events": self.stats_collector.sample_count,
            "errors": self.stats_collector.error_count + (self.pcap_collector.error_count if self.pcap_collector else 0),
            "latest_event": {**stats, **protocol_meta, "packet_stats": packet_stats},
            "state": {
                "state": state.state,
                "confidence": state.confidence,
                "reason": state.reason,
                "counters": state.counters,
            },
            "net_stats": stats,
            "protocol": protocol_meta,
            "packet_stats": packet_stats,
            "uid": self.uid,
            "package_name": self.package_name,
            "accuracy": {},  # 由 evaluate 填充
        }

        with self._lock:
            self._traffic_history.append({
                "timestamp": entry.timestamp,
                "state": state.state,
                "confidence": state.confidence,
                "reason": state.reason,
            })
            self.total_updates += 1

        return snapshot

    # ----------------------- Ground Truth 评估 -----------------------

    def feed_ocr_log(self, ocr_snapshot: Dict[str, Any], log_snapshot: Dict[str, Any]):
        """
        每轮主循环调用，缓存 OCR/Log 结果用于时间窗口对齐。
        """
        now = time.time()

        ocr_state = self._extract_state_from_ocr(ocr_snapshot)
        log_state = self._extract_state_from_log(log_snapshot)

        with self._lock:
            self._ocr_history.append({"timestamp": now, "state": ocr_state})
            self._log_history.append({"timestamp": now, "state": log_state})

    @staticmethod
    def _extract_state_from_ocr(ocr_snapshot: Dict[str, Any]) -> str:
        """从 ocr snapshot 中提取可供对齐的状态名"""
        latest = (ocr_snapshot.get("latest_result") or {})
        is_lag = (latest.get("是否卡顿") or "否").strip()
        if is_lag == "是":
            return "BUFFERING"
        # 若无明显异常信号，返回 NORMAL
        return "NORMAL_OR_NO_STRONG_EVIDENCE"

    @staticmethod
    def _extract_state_from_log(log_snapshot: Dict[str, Any]) -> str:
        """从 log snapshot 中提取 detector 状态名"""
        st = (log_snapshot.get("state") or {}).get("state", "UNKNOWN")
        return st if st else "UNKNOWN"

    def evaluate(self) -> Dict[str, Any]:
        """生成当前最新的准确率评估"""
        with self._lock:
            traffic_hist = list(self._traffic_history)
            ocr_hist = list(self._ocr_history)
            log_hist = list(self._log_history)

        result = self.ground_truth.evaluate(traffic_hist, ocr_hist, log_hist)
        return result.to_dict() if hasattr(result, "to_dict") else self._gt_result_to_dict(result)

    def get_accuracy_report(self) -> Dict[str, Any]:
        """获取累计准确率报告"""
        return self.ground_truth.get_accuracy_report()

    @staticmethod
    def _gt_result_to_dict(r: GroundTruthResult) -> Dict[str, Any]:
        return {
            "timestamp": r.timestamp,
            "traffic_state": r.traffic_state,
            "ocr_state": r.ocr_state,
            "log_state": r.log_state,
            "ocr_match": r.ocr_match,
            "log_match": r.log_match,
            "three_way_agree": r.three_way_agree,
            "ocr_false_positive": r.ocr_false_positive,
            "ocr_false_negative": r.ocr_false_negative,
            "log_false_positive": r.log_false_positive,
            "log_false_negative": r.log_false_negative,
            "discrepancy": r.discrepancy,
            "recommended_fusion": r.recommended_fusion,
            "details": r.details,
        }
