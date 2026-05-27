"""
service.py —— 本地时延子模块主控适配层

对主控层暴露 start/stop/get_snapshot/evaluate 标准接口，
内部循环采集 SurfaceFlinger / gfxinfo 数据并生成标注样本。
"""

import threading
import time
from collections import deque
from typing import Dict, Any, Optional, Deque

from .collector import LocalLatencyCollector
from .analyzer import LocalLatencyAnalyzer
from .models import LocalLatencySnapshot, LocalLatencyLabel

# 默认采集间隔：秒（SurfaceFlinger 最多缓存 128 帧，
# 在 60fps 下约 2.1 秒刷新一轮，建议采集间隔 1-2 秒）
DEFAULT_COLLECT_INTERVAL = 1.5


class LocalLatencyModuleService:
    """
    本地时延采集与标注服务。
    """

    def __init__(
        self,
        package_name: str,
        device_id: Optional[str] = None,
        collect_interval: float = DEFAULT_COLLECT_INTERVAL,
    ):
        self.package_name = package_name
        self.device_id = device_id
        self.collect_interval = collect_interval

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.collector = LocalLatencyCollector(
            package_name=package_name,
            device_id=device_id,
        )
        self.analyzer = LocalLatencyAnalyzer()

        # 状态缓存
        self._latest_snapshot: Optional[LocalLatencySnapshot] = None
        self._latest_label: Optional[LocalLatencyLabel] = None
        self._history: Deque[Dict[str, Any]] = deque(maxlen=200)
        self._label_history: Deque[Dict[str, Any]] = deque(maxlen=200)

        self.errors = 0
        self.total_collects = 0

    # ----------------------- 生命周期 -----------------------

    def start(self):
        if self._running:
            return
        # 预解析 surface，若失败则后续循环中继续重试
        try:
            self.collector.resolve_surface_name()
        except Exception:
            pass
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    # ----------------------- 采集循环 -----------------------

    def _run_loop(self):
        while self._running:
            try:
                self._collect_once()
            except Exception:
                with self._lock:
                    self.errors += 1
            time.sleep(self.collect_interval)

    def _collect_once(self):
        # 1) SurfaceFlinger --latency
        refresh_period_ns, raw_records = self.collector.collect_surfaceflinger_latency()

        sf_records = []
        if refresh_period_ns is not None and raw_records:
            sf_records = self.analyzer.analyze_surfaceflinger(refresh_period_ns, raw_records)

        # 2) gfxinfo framestats（降低频率：每 3 次采集执行 1 次，避免 ADB 开销过大）
        gfx_records = []
        if self.total_collects % 3 == 0:
            raw_gfx = self.collector.collect_gfxinfo_framestats()
            gfx_records = self.analyzer.analyze_gfxinfo(raw_gfx)

        # 3) 构建聚合快照
        surface_name = self.collector.resolve_surface_name() or self.package_name
        snapshot = self.analyzer.build_snapshot(
            surface_name=surface_name,
            refresh_period_ns=refresh_period_ns or 16_666_667,  # 默认 60fps
            sf_records=sf_records,
            gfx_records=gfx_records,
        )

        with self._lock:
            self._latest_snapshot = snapshot
            self._history.append(snapshot.to_dict())
            self.total_collects += 1

    # ----------------------- 快照 / 标注获取 -----------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """主控层每轮调用，获取本地时延聚合快照。"""
        with self._lock:
            snap = self._latest_snapshot
            if snap is None:
                return {
                    "events": self.total_collects,
                    "errors": self.errors,
                    "latest_result": {},
                    "history": list(self._history),
                }
            return {
                "events": self.total_collects,
                "errors": self.errors,
                "latest_result": snap.to_dict(),
                "history": list(self._history),
            }

    def generate_label(
        self,
        ocr_latest: Optional[Dict[str, Any]] = None,
        network_state: str = "",
    ) -> Optional[LocalLatencyLabel]:
        """
        基于最新快照与 OCR/Traffic 信息生成标注样本。
        主控层可在每轮融合后调用此方法构建训练数据。
        """
        with self._lock:
            snap = self._latest_snapshot
            if snap is None:
                return None
            label = self.analyzer.build_label(snap, ocr_latest=ocr_latest, network_state=network_state)
            self._latest_label = label
            self._label_history.append(label.to_dict())
            return label

    def get_label_history(self) -> Deque[Dict[str, Any]]:
        with self._lock:
            return deque(self._label_history)

    def get_latest_label(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._latest_label.to_dict() if self._latest_label else None
