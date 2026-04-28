"""
config.py —— 主控项目级配置
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ProjectConfig:
    target_app: str = "虎牙直播"
    duration_minutes: Optional[int] = None
    device_id: Optional[str] = None
    dashboard_refresh_seconds: float = 1.0
    log_window_seconds: int = 12
    results_root: Path = Path("results")
    enable_llm_log_analysis: bool = False
    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    llm_api_key: str = os.getenv("OPENAI_API_KEY", "")
    llm_base_url: str = ""
    llm_timeout_sec: int = 10
    llm_min_interval_sec: int = 10
    # Traffic 子模块配置
    enable_traffic: bool = True
    traffic_package_name: Optional[str] = None  # 如 com.duowan.kiwi，None 时自动映射
    enable_traffic_pcap: bool = True
    traffic_window_seconds: float = 12.0
