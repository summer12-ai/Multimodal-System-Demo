"""
dashboard.py —— 主控台统一监控视图
"""

import os
from datetime import datetime
from typing import Dict, Any, List


def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _safe(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def render_dashboard(
    runtime_sec: int,
    target_app: str,
    fusion_state: Dict[str, Any],
    ocr_snapshot: Dict[str, Any],
    log_snapshot: Dict[str, Any],
    recent_events: List[str],
):
    """
    按统一模板输出主控监控视图。
    """
    _clear_screen()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ocr_latest = ocr_snapshot.get("latest_result") or {}
    log_state = log_snapshot.get("state") or {}

    print("=" * 88)
    print("ADB 多模态状态识别主控 Demo")
    print("=" * 88)
    print(f"时间: {now} | 运行时长: {runtime_sec}s | 目标 App: {target_app}")
    print(
        f"综合状态: {fusion_state.get('final_state', fusion_state.get('fused_state', 'UNKNOWN'))}  "
        f"(conf={fusion_state.get('final_confidence', fusion_state.get('confidence', 0.0)):.2f})"
    )
    print(
        f"rule={fusion_state.get('rule_state', '')}:{fusion_state.get('rule_confidence', 0.0):.2f} | "
        f"llm={fusion_state.get('llm_state', 'N/A')}:{fusion_state.get('llm_confidence', 0.0):.2f}"
    )
    print("-" * 88)
    print("[OCR 子模块]")
    print(
        f"frames={ocr_snapshot.get('frames', 0)} | errors={ocr_snapshot.get('errors', 0)} | "
        f"resolution={_safe(ocr_latest.get('分辨率', ''))} | lag={_safe(ocr_latest.get('是否卡顿', ''))} | "
        f"fps={_safe(ocr_latest.get('帧率', ''))}"
    )
    print("-" * 88)
    print("[Logcat 子模块]")
    print(
        f"events={log_snapshot.get('events', 0)} | filtered={log_snapshot.get('filtered_out', 0)} | "
        f"errors={log_snapshot.get('errors', 0)} | "
        f"state={_safe(log_state.get('state', 'UNKNOWN'))} | conf={float(log_state.get('confidence', 0.0)):.2f}"
    )
    print(
        f"allowed_pids={_safe(log_snapshot.get('allowed_pids', []))} | "
        f"filter_keywords={_safe((log_snapshot.get('filter_rule') or {}).get('keywords', []))}"
    )
    print(f"state_reason={_safe(log_state.get('reason', ''))}")
    llm = log_snapshot.get("llm") or {}
    if llm:
        print(f"llm_evidence={_safe(llm.get('evidence', []))}")
    print("-" * 88)
    print("[最近事件摘要]")
    if not recent_events:
        print("  (empty)")
    else:
        for item in recent_events[-8:]:
            print(f"  - {item}")
    print("=" * 88)
