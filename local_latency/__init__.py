"""
local_latency —— 本地时延（帧生成时延）采集与标注子模块

基于 SurfaceFlinger --latency / gfxinfo framestats / Perfetto FrameTimeline
非侵入式获取 Android 游戏帧级纳秒级时序数据，构建高质量本地时延标注数据集。
"""

from .service import LocalLatencyModuleService
from .models import FrameLatencyRecord, LocalLatencySnapshot, LocalLatencyLabel

__all__ = [
    "LocalLatencyModuleService",
    "FrameLatencyRecord",
    "LocalLatencySnapshot",
    "LocalLatencyLabel",
]
