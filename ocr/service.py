"""
service.py —— OCR 子模块主控适配层

架构：
- 采用「生产者-消费者」模型，截图线程与 OCR 推理线程解耦。
- 截图源通过 FrameSource 接口注入，支持可插拔后端（ADB / scrcpy / minicap）。
- queue.Queue(maxsize=3) 做背压控制：当 OCR 推理慢于截图时，自动丢弃旧帧，
  始终处理最新画面，避免内存无限增长。
"""

import queue
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, Deque
from collections import deque

from .frame_source import FrameSource
from .analyzer import Analyzer
from .config import CAPTURE_INTERVAL, get_category_by_app
from .ocr_engine import OCREngine


class OcrModuleService:
    """
    将既有 OCR 能力包装为可被主控层调度的服务接口。

    参数
    ----
    target_app : str
        目标应用名称，用于先验分类。
    frame_source : FrameSource
        截图源实例（如 ADBFrameSource），负责实际帧采集。
    """

    def __init__(self, target_app: str, frame_source: FrameSource):
        self.target_app = target_app
        self.frame_source = frame_source

        # 线程控制
        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._process_thread: Optional[threading.Thread] = None

        # 帧队列（背压：队列满时丢弃最旧帧）
        self._capture_queue: "queue.Queue[Tuple[float, Any]]" = queue.Queue(maxsize=3)

        # 结果存储
        self._lock = threading.Lock()
        self._latest_result: Optional[Dict[str, str]] = None
        self._recent_rows: Deque[Dict[str, Any]] = deque(maxlen=50)
        self.errors = 0
        self.frames = 0

        # 分辨率粘性策略
        self._last_resolution: str = ""
        self._last_resolution_seen_at: float = 0.0
        self._resolution_empty_streak: int = 0
        self._resolution_sticky_seconds = 90.0
        self._resolution_sticky_max_empty_streak = 120

        # OCR 引擎（在 start() 中延迟初始化，避免构造时阻塞）
        self.ocr_engine: Optional[OCREngine] = None
        self.analyzer: Optional[Analyzer] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return

        # 初始化截图源
        self.frame_source.start()

        # 初始化 OCR（耗时操作，仅在 process_loop 中使用）
        self.ocr_engine = OCREngine(use_gpu=False)
        self.analyzer = Analyzer(self.ocr_engine)
        self.analyzer.set_target_app(self.target_app)

        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._process_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._capture_thread.start()
        self._process_thread.start()

    def stop(self) -> None:
        self._running = False

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)
        if self._process_thread and self._process_thread.is_alive():
            self._process_thread.join(timeout=5.0)

        self.frame_source.stop()

    # ------------------------------------------------------------------
    # 生产者线程：持续从 FrameSource 取帧，放入队列
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """
        截图生产者线程。

        逻辑：
        1. 调用 frame_source.get_frame() 获取最新帧。
        2. 若队列已满，丢弃最旧帧（背压），放入新帧。
        3. 按 max_fps 做睡眠补偿，避免空转耗 CPU。
        """
        while self._running:
            loop_start = time.time()

            try:
                frame = self.frame_source.get_frame()
                if frame is None:
                    # 截图失败，短暂休息后重试
                    time.sleep(0.2)
                    continue

                ts, img = frame

                # 背压控制：队列满时丢弃旧帧
                if self._capture_queue.full():
                    try:
                        self._capture_queue.get_nowait()
                    except queue.Empty:
                        pass

                self._capture_queue.put_nowait((ts, img))

            except Exception as e:
                print(f"[OCR Capture] 截图异常: {e}")

            # 精确睡眠补偿（按 frame_source.max_fps 控制频率）
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, (1.0 / self.frame_source.max_fps) - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    # ------------------------------------------------------------------
    # 消费者线程：从队列取帧，执行 OCR 分析，存储结果
    # ------------------------------------------------------------------

    def _process_loop(self) -> None:
        """
        OCR 消费者线程。

        逻辑：
        1. 阻塞等待队列中的帧（timeout=1.0s，便于响应 stop）。
        2. 对每帧执行 analyze_frame()。
        3. 应用分辨率粘性策略。
        4. 加锁写入结果。
        """
        while self._running:
            try:
                ts, img = self._capture_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                # OCR 分析（瓶颈：200-800ms）
                analysis = self.analyzer.analyze_frame(img)

                row = {
                    "时间戳": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                    "业务大类": analysis.get("category", get_category_by_app(self.target_app)),
                    "具体软件": analysis.get("app", self.target_app),
                    "分辨率": analysis.get("resolution", ""),
                    "是否卡顿": analysis.get("is_lag", "否"),
                    "帧率": analysis.get("fps", ""),
                    "延迟": analysis.get("latency", ""),
                }
                row = self._apply_resolution_sticky(row)

                with self._lock:
                    self._latest_result = row
                    self._recent_rows.append(row)
                    self.frames += 1

            except Exception as e:
                print(f"[OCR Process] 分析异常: {e}")
                with self._lock:
                    self.errors += 1

    # ------------------------------------------------------------------
    # 分辨率粘性策略
    # ------------------------------------------------------------------

    def _apply_resolution_sticky(self, row: Dict[str, str]) -> Dict[str, str]:
        """
        若当前帧未识别出分辨率，且没有明显变化信号，则回填最近一次分辨率。

        明显变化信号：
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

    # ------------------------------------------------------------------
    # 查询接口（供主控层 / dashboard 调用）
    # ------------------------------------------------------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """返回当前统计快照；线程安全。"""
        with self._lock:
            return {
                "frames": self.frames,
                "errors": self.errors,
                "latest_result": self._latest_result or {},
                "recent_rows": list(self._recent_rows),
            }
