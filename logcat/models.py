"""
models.py —— logcat 模块数据模型
"""

from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class LogEvent:
    timestamp: str
    level: str
    tag: str
    pid: str
    tid: str
    message: str
    template: str = ""
    event_type: str = "generic"
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectorState:
    state: str
    confidence: float
    reason: str
    counters: Dict[str, int] = field(default_factory=dict)
