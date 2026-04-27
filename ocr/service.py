"""
service.py —— OCR 子模块主控适配层
"""

import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, Deque
from collections import deque

from .adb_controller import ADBController
from .analyzer import Analyzer
from .config import CAPTURE_INTERVAL, get_category_by_app
from .ocr_engine import OCREngine


class OcrModuleService:
    """
    将既有 OCR 能力包装为可被主控层调度的服务接口。
    """

    def __init__(self, target_app: str, device_id: Optional[str] = None):
        self.target_app = target_app
        self.device_id = device_id

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._latest_result: Optional[Dict[str, str]] = None
        self._recent_rows: Deque[Dict[str, Any]] = deque(maxlen=50)
        self.errors = 0
        self.frames = 0
        self._last_resolution: str = ""
        self._last_resolution_seen_at: float = 0.0
        self._resolution_empty_streak: int = 0

        self.adb = ADBController(device_id=device_id)
        self.ocr_engine = None
        self.analyzer = None

        # 分辨率粘性策略：
        # UI 清晰度标记常常是短暂浮层，识别不到时在短时间内回填上一次值。
        self._resolution_sticky_seconds = 90.0
        self._resolution_sticky_max_empty_streak = 120

    def start(self):
        if self._running:
            return
        if not self.adb.check_connection():
            raise RuntimeError("ADB connection failed")
        self.ocr_engine = OCREngine(use_gpu=False)
        self.analyzer = Analyzer(self.ocr_engine)
        self.analyzer.set_target_app(self.target_app)

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _apply_resolution_sticky(self, row: Dict[str, str]) -> Dict[str, str]:
        """
        若当前帧未识别出分辨率，且没有明显变化信号，则回填最近一次分辨率。
        明显变化信号（当前版本）：
        1) 检测到卡顿（可能触发自适应码率变化）
        2) 超过粘性时间窗
        3) 连续空值过多（避免无限持有旧值）
        """
        now = time.time()
        current_resolution = (row.get("分辨率") or "").strip()
        is_lag = (row.get("是否卡顿") or "否").strip()

        if current_resolution:
            self._last_resolution = current_resolution
            self._last_resolution_seen_at = now
            self._resolution_empty_streak = 0
            return row

        self._resolution_empty_streak += 1
        within_ttl = (now - self._last_resolution_seen_at) <= self._resolution_sticky_seconds
        streak_ok = self._resolution_empty_streak <= self._resolution_sticky_max_empty_streak
        stable_signal = is_lag != "是"

        if self._last_resolution and within_ttl and streak_ok and stable_signal:
            row["分辨率"] = self._last_resolution
        return row

    def _run_loop(self):
        while self._running:
            try:
                img = self.adb.get_screenshot()
                if img is None:
                    self.errors += 1
                    time.sleep(CAPTURE_INTERVAL)
                    continue

                analysis = self.analyzer.analyze_frame(img)
                row = {
                    "时间戳": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "业务大类": analysis.get("category", get_category_by_app(self.target_app)),
                    "具体软件": analysis.get("app", self.target_app),
                    "分辨率": analysis.get("resolution", ""),
                    "是否卡顿": analysis.get("is_lag", "否"),
                    "帧率": analysis.get("fps", ""),
                }
                row = self._apply_resolution_sticky(row)
                with self._lock:
                    self._latest_result = row
                    self._recent_rows.append(row)
                    self.frames += 1
            except Exception:
                with self._lock:
                    self.errors += 1
            time.sleep(CAPTURE_INTERVAL)

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "frames": self.frames,
                "errors": self.errors,
                "latest_result": self._latest_result or {},
                "recent_rows": list(self._recent_rows),
            }
