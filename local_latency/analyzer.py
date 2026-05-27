"""
analyzer.py —— 本地时延数据分析器

将原始 SurfaceFlinger / gfxinfo 数据解析为结构化指标，
并生成可用于机器学习训练的高质量标注样本。
"""

import time
import statistics
from typing import List, Dict, Any, Optional, Tuple

from .models import FrameLatencyRecord, GfxInfoFramestats, LocalLatencySnapshot, LocalLatencyLabel


class LocalLatencyAnalyzer:
    """
    解析 SurfaceFlinger 三元组和 gfxinfo CSV，计算帧延迟、jank、FPS 等核心指标。
    """

    # 状态判定阈值（基于报告中的行业经验值）
    JANK_THRESHOLD = 1               # 单窗口内出现 jank 即视为潜在异常
    HIGH_LATENCY_MS = 33.3           # 帧延迟 > 33.3ms 视为高时延（<30fps 体验）
    STUTTER_JANK_RATIO = 0.15        # jank 帧占比 > 15% 视为 stutter
    FREEZE_FPS_THRESHOLD = 5.0       # FPS < 5 视为画面冻结

    def __init__(self):
        self._last_jank_indicator: Optional[int] = None

    # ----------------------- SurfaceFlinger 解析 -----------------------

    def analyze_surfaceflinger(
        self,
        refresh_period_ns: int,
        raw_records: List[Tuple[int, int, int]],
    ) -> List[FrameLatencyRecord]:
        """
        将原始三元组解析为 FrameLatencyRecord，并计算 jank。
        """
        records: List[FrameLatencyRecord] = []
        for a, b, c in raw_records:
            rec = FrameLatencyRecord(
                a_ns=a,
                b_ns=b,
                c_ns=c,
                refresh_period_ns=refresh_period_ns,
            )
            rec.compute_metrics()
            # jank 判定：与前一帧的 jank_indicator 发生变化时
            if self._last_jank_indicator is not None:
                rec.is_jank = rec.jank_indicator != self._last_jank_indicator
            else:
                rec.is_jank = False
            self._last_jank_indicator = rec.jank_indicator
            records.append(rec)
        return records

    # ----------------------- gfxinfo 解析 -----------------------

    @staticmethod
    def analyze_gfxinfo(raw_rows: List[dict]) -> List[GfxInfoFramestats]:
        records = []
        for row in raw_rows:
            try:
                iv = int(row.get("IntendedVsync", 0))
                vs = int(row.get("Vsync", 0))
                fc = int(row.get("FrameCompleted", 0))
                if iv > 0 and fc > iv:
                    records.append(GfxInfoFramestats(intended_vsync=iv, vsync=vs, frame_completed=fc, raw=row))
            except (ValueError, TypeError):
                continue
        return records

    # ----------------------- 聚合快照 -----------------------

    def build_snapshot(
        self,
        surface_name: str,
        refresh_period_ns: int,
        sf_records: List[FrameLatencyRecord],
        gfx_records: List[GfxInfoFramestats],
    ) -> LocalLatencySnapshot:
        """
        从帧级记录构建聚合快照，包含 FPS、jank 计数、分位延迟等。
        若 SurfaceFlinger 无数据，退化为使用 gfxinfo framestats 数据。
        """
        snap = LocalLatencySnapshot(
            timestamp=time.time(),
            surface_name=surface_name,
            refresh_period_ns=refresh_period_ns,
            frame_records=sf_records,
            gfx_records=gfx_records,
        )

        # 优先使用 SurfaceFlinger 数据；若为空，退化为 gfxinfo
        if sf_records:
            latencies_ms = [r.frame_latency_ns / 1_000_000.0 for r in sf_records]
            productions_ms = [r.frame_production_ns / 1_000_000.0 for r in sf_records]
            submissions_ms = [r.sf_submission_ns / 1_000_000.0 for r in sf_records]

            snap.avg_frame_latency_ms = statistics.mean(latencies_ms)
            snap.max_frame_latency_ms = max(latencies_ms)
            snap.p95_frame_latency_ms = self._percentile(latencies_ms, 95)
            snap.avg_frame_production_ms = statistics.mean(productions_ms) if productions_ms else 0.0
            snap.avg_sf_submission_ms = statistics.mean(submissions_ms) if submissions_ms else 0.0
            snap.jank_count = sum(1 for r in sf_records if r.is_jank)

            # FPS 估算：基于时间跨度 / 帧数
            if len(sf_records) >= 2:
                first_a = sf_records[0].a_ns
                last_a = sf_records[-1].a_ns
                duration_sec = (last_a - first_a) / 1_000_000_000.0
                if duration_sec > 0:
                    snap.fps = len(sf_records) / duration_sec

            snap.state, snap.confidence = self._classify_state(snap, latencies_ms)
        elif gfx_records:
            frame_times = [g.frame_time_ms for g in gfx_records]
            snap.avg_frame_latency_ms = statistics.mean(frame_times)
            snap.max_frame_latency_ms = max(frame_times)
            snap.p95_frame_latency_ms = self._percentile(frame_times, 95)
            snap.avg_frame_production_ms = 0.0
            snap.avg_sf_submission_ms = 0.0
            # gfxinfo 不直接提供 jank 计数，用 95th/50th 比值作为启发式
            p50 = self._percentile(frame_times, 50)
            snap.jank_count = sum(1 for ft in frame_times if p50 > 0 and ft / p50 > 1.5)

            # FPS 估算：gfxinfo 通常为最近 120 帧，按平均帧时间反推
            avg_ft = statistics.mean(frame_times)
            if avg_ft > 0:
                snap.fps = 1000.0 / avg_ft

            snap.state, snap.confidence = self._classify_state(snap, frame_times)
        else:
            snap.state = "UNKNOWN"
            snap.confidence = 0.0
        return snap

    @staticmethod
    def _percentile(values: List[float], p: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        k = (len(s) - 1) * p / 100.0
        f = int(k)
        c = min(f + 1, len(s) - 1)
        if f == c:
            return s[f]
        return s[f] * (c - k) + s[c] * (k - f)

    def _classify_state(self, snap: LocalLatencySnapshot, latencies_ms: List[float]) -> Tuple[str, float]:
        """
        根据聚合指标判定本地时延状态。
        Returns: (state, confidence)
        """
        total = len(latencies_ms)
        if total == 0:
            return "UNKNOWN", 0.0

        jank_ratio = snap.jank_count / total if total > 0 else 0.0

        # 冻结判定
        if snap.fps > 0 and snap.fps < self.FREEZE_FPS_THRESHOLD:
            return "FREEZE", 0.95

        # 严重 stutter 判定
        if jank_ratio >= self.STUTTER_JANK_RATIO or snap.jank_count >= 3:
            conf = min(0.95, 0.7 + jank_ratio)
            return "STUTTER", round(conf, 3)

        # 高延迟判定（帧延迟持续超过阈值）
        high_latency_frames = sum(1 for v in latencies_ms if v > self.HIGH_LATENCY_MS)
        if high_latency_frames / total >= 0.3:
            return "HIGH_LATENCY", round(0.75 + 0.2 * (high_latency_frames / total), 3)

        # 轻微异常
        if snap.jank_count > 0 or snap.max_frame_latency_ms > self.HIGH_LATENCY_MS:
            return "LOCAL_LAG", 0.65

        return "NORMAL", 0.85

    # ----------------------- 标注样本生成 -----------------------

    def build_label(
        self,
        snapshot: LocalLatencySnapshot,
        ocr_latest: Optional[Dict[str, Any]] = None,
        network_state: str = "",
    ) -> LocalLatencyLabel:
        """
        将快照与 OCR/Traffic 信息融合，生成带标注的训练样本。
        """
        ocr_is_lag = False
        ocr_fps = ""
        if ocr_latest:
            ocr_is_lag = (ocr_latest.get("是否卡顿", "否") == "是")
            ocr_fps = ocr_latest.get("帧率", "")

        # 标注置信度：SurfaceFlinger 数据越完整置信度越高
        confidence = snapshot.confidence
        frame_count = len(snapshot.frame_records)
        if frame_count >= 60:
            confidence = min(0.98, confidence + 0.05)
        elif frame_count < 10:
            confidence = max(0.3, confidence - 0.2)

        # 若 OCR 也检测到卡顿，提升 LOCAL_LAG/STUTTER 置信度
        if ocr_is_lag and snapshot.state in ("LOCAL_LAG", "STUTTER", "HIGH_LATENCY", "FREEZE"):
            confidence = min(0.98, confidence + 0.08)

        label_state = snapshot.state
        if label_state == "NORMAL" and ocr_is_lag:
            # OCR 认为卡顿但 SF 认为正常：降级为轻微 LOCAL_LAG，降低置信度
            label_state = "LOCAL_LAG"
            confidence = max(0.5, confidence - 0.15)

        # 本地时延估计值：采用 p95 帧延迟作为保守估计
        local_latency_ms = snapshot.p95_frame_latency_ms

        return LocalLatencyLabel(
            timestamp=snapshot.timestamp,
            label_state=label_state,
            local_latency_ms=local_latency_ms,
            jank_count_window=snapshot.jank_count,
            avg_frame_latency_ms=snapshot.avg_frame_latency_ms,
            max_frame_latency_ms=snapshot.max_frame_latency_ms,
            p95_frame_latency_ms=snapshot.p95_frame_latency_ms,
            fps=snapshot.fps,
            ocr_is_lag=ocr_is_lag,
            ocr_fps=ocr_fps,
            network_state=network_state,
            confidence=round(confidence, 3),
            raw_features={
                "refresh_period_ns": snapshot.refresh_period_ns,
                "frame_count": frame_count,
                "avg_frame_production_ms": snapshot.avg_frame_production_ms,
                "avg_sf_submission_ms": snapshot.avg_sf_submission_ms,
                "gfx_frame_count": len(snapshot.gfx_records),
            },
        )
