"""
orchestrator.py —— 顶层主控调度器
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

from .dashboard import render_dashboard
from .fusion import fuse_states
from .result_hub import UnifiedResultHub
from logcat import LogcatModuleService
from ocr import OcrModuleService


@dataclass
class OrchestratorConfig:
    target_app: str
    results_root: Path
    duration_minutes: Optional[int] = None
    device_id: Optional[str] = None
    dashboard_refresh_seconds: float = 1.0
    log_window_seconds: int = 12
    enable_llm_log_analysis: bool = False
    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout_sec: int = 10
    llm_min_interval_sec: int = 10


class MasterOrchestrator:
    """
    统一调度 OCR 子模块与日志子模块，输出融合状态与统一结果。
    """

    def __init__(self, cfg: OrchestratorConfig):
        self.cfg = cfg
        self.ocr_service = OcrModuleService(target_app=cfg.target_app, device_id=cfg.device_id)
        self.log_service = LogcatModuleService(
            device_id=cfg.device_id,
            window_seconds=cfg.log_window_seconds,
            target_app=cfg.target_app,
            enable_llm=cfg.enable_llm_log_analysis,
            llm_provider=cfg.llm_provider,
            llm_model=cfg.llm_model,
            llm_api_key=cfg.llm_api_key,
            llm_base_url=cfg.llm_base_url,
            llm_timeout_sec=cfg.llm_timeout_sec,
            llm_min_interval_sec=cfg.llm_min_interval_sec,
        )
        self.result_hub = UnifiedResultHub(results_root=cfg.results_root)
        self._recent_events: List[str] = []

    def _collect_recent_events(self, ocr_snapshot, log_snapshot, fusion):
        events = []
        ocr_latest = ocr_snapshot.get("latest_result") or {}
        if ocr_latest:
            events.append(
                f"OCR {ocr_latest.get('时间戳', '')} lag={ocr_latest.get('是否卡顿', '')} "
                f"res={ocr_latest.get('分辨率', '')} fps={ocr_latest.get('帧率', '')}"
            )
        if log_snapshot.get("latest_event"):
            le = log_snapshot["latest_event"]
            events.append(f"LOG {le.get('timestamp', '')} {le.get('event_type', '')} {le.get('tag', '')}")
        events.append(
            f"FUSION state={fusion.get('final_state', fusion.get('fused_state', ''))} "
            f"conf={fusion.get('final_confidence', fusion.get('confidence', 0.0)):.2f}"
        )
        self._recent_events.extend(events)
        self._recent_events = self._recent_events[-20:]

    def run(self):
        start = time.time()
        self.ocr_service.start()
        self.log_service.start()
        try:
            while True:
                runtime = int(time.time() - start)
                if self.cfg.duration_minutes and runtime >= self.cfg.duration_minutes * 60:
                    break

                ocr_snapshot = self.ocr_service.get_snapshot()
                log_snapshot = self.log_service.get_snapshot()
                ocr_latest = ocr_snapshot.get("latest_result") or {}
                llm_result = self.log_service.analyze_window_with_llm(
                    ocr_hint={
                        "lag": (ocr_latest.get("是否卡顿", "否") == "是"),
                        "resolution": ocr_latest.get("分辨率", ""),
                        "fps": ocr_latest.get("帧率", ""),
                    }
                )
                if llm_result:
                    log_snapshot["llm"] = llm_result
                fusion = fuse_states(ocr_snapshot, log_snapshot, target_app=self.cfg.target_app)

                if ocr_snapshot.get("latest_result"):
                    self.result_hub.append_ocr(ocr_snapshot["latest_result"])
                if log_snapshot.get("latest_event"):
                    self.result_hub.append_log(log_snapshot["latest_event"])
                self.result_hub.append_detector(
                    {
                        "state": log_snapshot.get("state", {}),
                        "events": log_snapshot.get("events", 0),
                        "filtered_out": log_snapshot.get("filtered_out", 0),
                        "errors": log_snapshot.get("errors", 0),
                        "allowed_pids": log_snapshot.get("allowed_pids", []),
                        "filter_rule": log_snapshot.get("filter_rule", {}),
                        "evidence_events": log_snapshot.get("evidence_events", []),
                        "llm": log_snapshot.get("llm", {}),
                    }
                )
                self.result_hub.append_fusion(fusion)

                self._collect_recent_events(ocr_snapshot, log_snapshot, fusion)
                render_dashboard(
                    runtime_sec=runtime,
                    target_app=self.cfg.target_app,
                    fusion_state=fusion,
                    ocr_snapshot=ocr_snapshot,
                    log_snapshot=log_snapshot,
                    recent_events=self._recent_events,
                )
                time.sleep(self.cfg.dashboard_refresh_seconds)
        finally:
            self.log_service.stop()
            self.ocr_service.stop()
            self.result_hub.close()
