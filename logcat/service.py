"""
service.py —— 日志子模块统一服务接口
"""

import subprocess
import threading
import time
from collections import deque, Counter
from typing import Dict, Any, Deque, Optional, List, Set

from .collector import LogcatCollector
from .detector import WindowStateDetector
from .llm_analyzer import LLMSemanticAnalyzer
from .models import LogEvent
from .normalizer import normalize_event
from .parser import parse_line


APP_LOG_FILTER_RULES: Dict[str, Dict[str, List[str]]] = {
    "虎牙直播": {
        "packages": ["com.duowan.kiwi"],
        "keywords": ["kiwi", "huya", "yy"],
    },
    "虎牙": {
        "packages": ["com.duowan.kiwi"],
        "keywords": ["kiwi", "huya", "yy"],
    },
    "斗鱼直播": {
        "packages": ["air.tv.douyu.android"],
        "keywords": ["douyu"],
    },
    "斗鱼": {
        "packages": ["air.tv.douyu.android"],
        "keywords": ["douyu"],
    },
    "抖音": {
        "packages": ["com.ss.android.ugc.aweme"],
        "keywords": ["aweme", "douyin", "ttplayer"],
    },
    "快手": {
        "packages": ["com.smile.gifmaker"],
        "keywords": ["gifmaker", "kuaishou"],
    },
    "B站": {
        "packages": ["tv.danmaku.bili"],
        "keywords": ["bili", "danmaku"],
    },
    "哔哩哔哩": {
        "packages": ["tv.danmaku.bili"],
        "keywords": ["bili", "danmaku"],
    },
    "腾讯视频": {
        "packages": ["com.tencent.qqlive"],
        "keywords": ["qqlive", "tenvideo", "tencentvideo"],
    },
}


class LogcatModuleService:
    """
    对主控层暴露 start/stop/snapshot 的日志子模块服务。
    """

    def __init__(
        self,
        device_id: Optional[str] = None,
        window_seconds: int = 12,
        target_app: str = "",
        enable_llm: bool = False,
        llm_provider: str = "openai",
        llm_model: str = "gpt-4.1-mini",
        llm_api_key: str = "",
        llm_base_url: str = "",
        llm_timeout_sec: int = 10,
        llm_min_interval_sec: int = 10,
    ):
        self.detector = WindowStateDetector(window_seconds=window_seconds)
        self.events: Deque[LogEvent] = deque(maxlen=200)
        self.recent_lines: Deque[str] = deque(maxlen=20)
        self.evidence_events: Deque[Dict[str, str]] = deque(maxlen=120)
        self.errors = 0
        self.total_events = 0
        self.filtered_out = 0
        self._lock = threading.Lock()
        self._collector = LogcatCollector(on_line=self._on_line, device_id=device_id)
        self._latest_state = self.detector.current_state
        self._latest_event: Optional[LogEvent] = None
        self.device_id = device_id
        self.target_app = target_app
        self.filter_rule = self._resolve_filter_rule(target_app)
        self._allowed_pids: Set[str] = set()
        self._last_pid_refresh_at = 0.0
        self._pid_refresh_seconds = 8.0
        self.enable_llm = enable_llm
        self.llm_min_interval_sec = max(1, llm_min_interval_sec)
        self._last_llm_at = 0.0
        self._last_llm_result: Optional[Dict[str, Any]] = None
        self._llm_analyzer = LLMSemanticAnalyzer(
            provider=llm_provider,
            model=llm_model,
            api_key=llm_api_key,
            base_url=llm_base_url,
            timeout_sec=llm_timeout_sec,
        )

    @staticmethod
    def _resolve_filter_rule(target_app: str) -> Dict[str, List[str]]:
        if not target_app:
            return {"packages": [], "keywords": []}
        if target_app in APP_LOG_FILTER_RULES:
            return APP_LOG_FILTER_RULES[target_app]

        # 未命中预置映射时，用目标 app 名称作为弱关键词兜底
        return {"packages": [], "keywords": [target_app.lower()]}

    def _adb_base_cmd(self) -> List[str]:
        cmd = ["adb"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        return cmd

    def _refresh_pids(self):
        self._last_pid_refresh_at = time.time()
        packages = self.filter_rule.get("packages", [])
        new_pids: Set[str] = set()
        if not packages:
            self._allowed_pids = new_pids
            return

        for pkg in packages:
            cmd = self._adb_base_cmd() + ["shell", "pidof", pkg]
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=3,
                )
                if res.returncode == 0 and res.stdout.strip():
                    for pid in res.stdout.strip().split():
                        if pid.isdigit():
                            new_pids.add(pid)
            except Exception:
                continue
        self._allowed_pids = new_pids

    def _ensure_pid_cache_fresh(self):
        if (time.time() - self._last_pid_refresh_at) >= self._pid_refresh_seconds:
            self._refresh_pids()

    def _should_keep_event(self, event: LogEvent) -> bool:
        """
        过滤策略：
        1) 有可用 PID 时：仅保留目标 App 进程日志。
        2) 无 PID 时：用 target_app 对应关键词做回退过滤。
        """
        self._ensure_pid_cache_fresh()
        if self._allowed_pids:
            return event.pid in self._allowed_pids

        keywords = [k.lower() for k in self.filter_rule.get("keywords", []) if k]
        packages = [p.lower() for p in self.filter_rule.get("packages", []) if p]
        haystack = f"{event.tag} {event.message} {event.template}".lower()
        for kw in keywords + packages:
            if kw in haystack:
                return True
        return False

    def _on_line(self, line: str):
        with self._lock:
            if line.startswith("LOGCAT_COLLECTOR_ERROR::"):
                self.errors += 1
                self.recent_lines.append(line)
                return
            try:
                event = parse_line(line)
                event = normalize_event(event)
                if not self._should_keep_event(event):
                    self.filtered_out += 1
                    return
                self.events.append(event)
                self.total_events += 1
                self._latest_event = event
                self._latest_state = self.detector.update(event)
                self.recent_lines.append(f"{event.event_type} | {event.tag} | {event.message[:80]}")
                if event.event_type != "generic" or event.level in {"E", "F"}:
                    self.evidence_events.append(
                        {
                            "timestamp": event.timestamp,
                            "level": event.level,
                            "tag": event.tag,
                            "event_type": event.event_type,
                            "message": event.message[:200],
                        }
                    )
            except Exception as e:
                self.errors += 1
                self.recent_lines.append(f"parse_error::{e}")

    def _build_window_summary(
        self,
        ocr_hint: Dict[str, Any],
        state_snapshot: Dict[str, Any],
        detector_events_snapshot: List[LogEvent],
        evidence_events_snapshot: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        counters = dict((state_snapshot.get("counters") or {}))
        event_counts = {
            "buffering": counters.get("buffering", 0),
            "retry": counters.get("retry", 0),
            "timeout": counters.get("timeout", 0),
            "network_issue": counters.get("network_issue", 0),
            "internal_error": counters.get("internal_error", 0),
            "render_issue": counters.get("render_issue", 0),
            "generic": counters.get("generic", 0),
            "severe_internal_error": counters.get("severe_internal_error", 0),
        }
        tag_counts = Counter(e.tag for e in detector_events_snapshot)
        top_tags = [[k, v] for k, v in tag_counts.most_common(8)]
        important_events = []
        for e in evidence_events_snapshot[-12:]:
            important_events.append(
                {
                    "time": e.get("timestamp", ""),
                    "level": e.get("level", ""),
                    "tag": e.get("tag", ""),
                    "event_type": e.get("event_type", ""),
                    "template": e.get("message", ""),
                }
            )

        return {
            "target_app": self.target_app,
            "window_sec": self.detector.window_seconds,
            "rule_state": state_snapshot.get("state", "UNKNOWN"),
            "rule_confidence": state_snapshot.get("confidence", 0.0),
            "rule_reason": state_snapshot.get("reason", ""),
            "event_counts": event_counts,
            "top_tags": top_tags,
            "important_events": important_events,
            "ocr_hint": ocr_hint,
        }

    def analyze_window_with_llm(self, ocr_hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.enable_llm:
            return None
        if not self._llm_analyzer.available():
            return None
        now = time.time()
        if (now - self._last_llm_at) < self.llm_min_interval_sec and self._last_llm_result is not None:
            return self._last_llm_result

        with self._lock:
            state_snapshot = {
                "state": self._latest_state.state,
                "confidence": self._latest_state.confidence,
                "reason": self._latest_state.reason,
                "counters": dict(self._latest_state.counters or {}),
            }
            detector_events_snapshot = list(self.detector.events)
            evidence_events_snapshot = list(self.evidence_events)
        summary = self._build_window_summary(
            ocr_hint=ocr_hint,
            state_snapshot=state_snapshot,
            detector_events_snapshot=detector_events_snapshot,
            evidence_events_snapshot=evidence_events_snapshot,
        )
        result = self._llm_analyzer.analyze(summary)
        self._last_llm_at = now
        if result is not None:
            with self._lock:
                self._last_llm_result = {
                    **result,
                    "summary": summary,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
        return self._last_llm_result

    def start(self):
        self._refresh_pids()
        self._collector.start()

    def stop(self):
        self._collector.stop()

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            latest_event_dict = {}
            if self._latest_event is not None:
                latest_event_dict = {
                    "timestamp": self._latest_event.timestamp,
                    "level": self._latest_event.level,
                    "tag": self._latest_event.tag,
                    "event_type": self._latest_event.event_type,
                    "message": self._latest_event.message,
                    "template": self._latest_event.template,
                }
            return {
                "events": self.total_events,
                "errors": self.errors,
                "filtered_out": self.filtered_out,
                "filter_rule": self.filter_rule,
                "allowed_pids": sorted(self._allowed_pids),
                "latest_event": latest_event_dict,
                "state": {
                    "state": self._latest_state.state,
                    "confidence": self._latest_state.confidence,
                    "reason": self._latest_state.reason,
                    "counters": self._latest_state.counters,
                },
                "llm": self._last_llm_result or {},
                "evidence_events": list(self.evidence_events)[-20:],
                "recent_lines": list(self.recent_lines),
            }
