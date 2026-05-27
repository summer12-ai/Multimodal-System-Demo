"""
models.py —— 本地时延数据模型
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class FrameLatencyRecord:
    """
    单帧时延记录，对应 SurfaceFlinger --latency 输出的一行三元组。
    
    时间戳含义（纳秒级）：
      A: 应用开始绘制该帧的时间
      B: SurfaceFlinger 将该帧提交给硬件显示前最后一个 vsync 的时间
      C: SurfaceFlinger 完成帧提交的时间
    """
    a_ns: int  # 应用开始绘制时间 (nanoseconds)
    b_ns: int  # SF 提交前最后一个 vsync 时间
    c_ns: int  # SF 提交完成时间
    refresh_period_ns: int  # 刷新周期 (nanoseconds)

    # 计算指标（由 analyzer 填充）
    frame_latency_ns: int = 0       # C - A, 从应用绘制到显示完成的总耗时
    frame_production_ns: int = 0    # B - A, 应用渲染一帧所需时间
    sf_submission_ns: int = 0       # C - B, SurfaceFlinger 合成耗时
    jank_indicator: int = 0         # ceil((C-A) / refresh_period)
    is_jank: bool = False           # 与前一帧相比 jank_indicator 是否变化

    def compute_metrics(self):
        """根据三元组计算核心时延指标。"""
        self.frame_latency_ns = self.c_ns - self.a_ns
        self.frame_production_ns = self.b_ns - self.a_ns
        self.sf_submission_ns = self.c_ns - self.b_ns
        if self.refresh_period_ns > 0:
            import math
            self.jank_indicator = math.ceil(self.frame_latency_ns / self.refresh_period_ns)


@dataclass
class GfxInfoFramestats:
    """
    gfxinfo framestats 单帧记录（Android 6.0+ CSV 输出）。
    关键列：IntendedVsync, Vsync, FrameCompleted 等 121 列时间戳。
    """
    intended_vsync: int
    vsync: int
    frame_completed: int
    # 简化模型：只保留最核心三列，原始全量数据存入 raw
    raw: Dict[str, str] = field(default_factory=dict)

    @property
    def frame_time_ms(self) -> float:
        """整帧耗时 (ms)"""
        return (self.frame_completed - self.intended_vsync) / 1_000_000.0


@dataclass
class LocalLatencySnapshot:
    """一次采集周期内的本地时延聚合快照。"""
    timestamp: float
    surface_name: str
    refresh_period_ns: int
    frame_records: List[FrameLatencyRecord] = field(default_factory=list)
    gfx_records: List[GfxInfoFramestats] = field(default_factory=list)

    # 聚合指标
    fps: float = 0.0
    jank_count: int = 0
    avg_frame_latency_ms: float = 0.0
    max_frame_latency_ms: float = 0.0
    p95_frame_latency_ms: float = 0.0
    avg_frame_production_ms: float = 0.0
    avg_sf_submission_ms: float = 0.0
    state: str = "UNKNOWN"          # NORMAL / HIGH_LATENCY / HIGH_JANK / STUTTER
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "surface_name": self.surface_name,
            "refresh_period_ns": self.refresh_period_ns,
            "fps": round(self.fps, 2),
            "jank_count": self.jank_count,
            "avg_frame_latency_ms": round(self.avg_frame_latency_ms, 3),
            "max_frame_latency_ms": round(self.max_frame_latency_ms, 3),
            "p95_frame_latency_ms": round(self.p95_frame_latency_ms, 3),
            "avg_frame_production_ms": round(self.avg_frame_production_ms, 3),
            "avg_sf_submission_ms": round(self.avg_sf_submission_ms, 3),
            "state": self.state,
            "confidence": round(self.confidence, 3),
            "frame_count": len(self.frame_records),
        }


@dataclass
class LocalLatencyLabel:
    """
    本地时延标注样本，可直接用于构建训练数据集。
    融合 SurfaceFlinger 帧时序、gfxinfo、OCR 卡顿检测等多源信号。
    """
    timestamp: float
    label_state: str            # 标注状态: NORMAL / LOCAL_LAG / STUTTER / FREEZE
    local_latency_ms: float     # 估计本地时延值 (ms)
    jank_count_window: int      # 时间窗口内 jank 次数
    avg_frame_latency_ms: float
    max_frame_latency_ms: float
    p95_frame_latency_ms: float
    fps: float
    ocr_is_lag: bool = False    # OCR 是否检测到卡顿（作为辅助标签）
    ocr_fps: str = ""           # OCR 识别的帧率显示
    network_state: str = ""     # 网络层状态（traffic 模块提供）
    confidence: float = 0.0     # 标注置信度
    raw_features: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "label_state": self.label_state,
            "local_latency_ms": round(self.local_latency_ms, 3),
            "jank_count_window": self.jank_count_window,
            "avg_frame_latency_ms": round(self.avg_frame_latency_ms, 3),
            "max_frame_latency_ms": round(self.max_frame_latency_ms, 3),
            "p95_frame_latency_ms": round(self.p95_frame_latency_ms, 3),
            "fps": round(self.fps, 2),
            "ocr_is_lag": self.ocr_is_lag,
            "ocr_fps": self.ocr_fps,
            "network_state": self.network_state,
            "confidence": round(self.confidence, 3),
            "raw_features": self.raw_features,
        }
