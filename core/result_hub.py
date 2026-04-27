"""
result_hub.py —— 统一结果输出中心
"""

import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any


class UnifiedResultHub:
    """
    统一写出 OCR 结果、logcat 事件、融合状态。
    """

    def __init__(self, results_root: Path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = results_root / f"run_{ts}"
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.ocr_jsonl = (self.run_dir / "ocr_events.jsonl").open("a", encoding="utf-8")
        self.log_jsonl = (self.run_dir / "log_events.jsonl").open("a", encoding="utf-8")
        self.detector_jsonl = (self.run_dir / "detector_events.jsonl").open("a", encoding="utf-8")
        self.fusion_jsonl = (self.run_dir / "fused_states.jsonl").open("a", encoding="utf-8")
        self.fusion_csv_fh = (self.run_dir / "fused_states.csv").open("a", encoding="utf-8", newline="")
        self.fusion_csv = csv.DictWriter(
            self.fusion_csv_fh,
            fieldnames=[
                "timestamp",
                "target_app",
                "rule_state",
                "rule_confidence",
                "llm_state",
                "llm_confidence",
                "final_state",
                "final_confidence",
                "ocr_resolution",
                "ocr_is_lag",
                "ocr_fps",
                "ocr_errors",
                "log_state",
                "log_errors",
            ],
        )
        self.fusion_csv.writeheader()

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _write_jsonl(fh, payload: Dict[str, Any]):
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        fh.flush()

    def append_ocr(self, row: Dict[str, Any]):
        payload = {"timestamp": self._now(), "type": "ocr", "data": row}
        self._write_jsonl(self.ocr_jsonl, payload)

    def append_log(self, event: Dict[str, Any]):
        payload = {"timestamp": self._now(), "type": "logcat", "data": event}
        self._write_jsonl(self.log_jsonl, payload)

    def append_detector(self, detector_snapshot: Dict[str, Any]):
        payload = {"timestamp": self._now(), "type": "detector", "data": detector_snapshot}
        self._write_jsonl(self.detector_jsonl, payload)

    def append_fusion(self, fusion: Dict[str, Any]):
        payload = {"timestamp": self._now(), "type": "fusion", "data": fusion}
        self._write_jsonl(self.fusion_jsonl, payload)
        self.fusion_csv.writerow(
            {
                "timestamp": payload["timestamp"],
                "target_app": fusion.get("target_app", ""),
                "rule_state": fusion.get("rule_state", ""),
                "rule_confidence": fusion.get("rule_confidence", 0.0),
                "llm_state": fusion.get("llm_state", ""),
                "llm_confidence": fusion.get("llm_confidence", 0.0),
                "final_state": fusion.get("final_state", fusion.get("fused_state", "")),
                "final_confidence": fusion.get("final_confidence", fusion.get("confidence", 0.0)),
                "ocr_resolution": (fusion.get("ocr") or {}).get("resolution", ""),
                "ocr_is_lag": (fusion.get("ocr") or {}).get("is_lag", ""),
                "ocr_fps": (fusion.get("ocr") or {}).get("fps", ""),
                "ocr_errors": (fusion.get("ocr") or {}).get("errors", 0),
                "log_state": (fusion.get("logcat") or {}).get("state", ""),
                "log_errors": (fusion.get("logcat") or {}).get("errors", 0),
            }
        )
        self.fusion_csv_fh.flush()

    def close(self):
        self.ocr_jsonl.close()
        self.log_jsonl.close()
        self.detector_jsonl.close()
        self.fusion_jsonl.close()
        self.fusion_csv_fh.close()
