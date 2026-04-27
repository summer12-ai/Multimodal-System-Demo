"""
prompt_templates.py —— LLM 分析 prompt 模板与输出约束
"""

from typing import Dict, Any
import json


LLM_STATE_SPACE = [
    "NORMAL_OR_NO_STRONG_EVIDENCE",
    "PAGE_SWITCH",
    "PLAY_INIT",
    "PLAYING",
    "BUFFERING",
    "NETWORK_ISSUE",
    "RENDER_ISSUE",
    "APP_INTERNAL_ERROR",
    "RECOVERED",
    "UNKNOWN",
]


def build_system_prompt() -> str:
    return (
        "You are an Android logcat semantic analyzer. "
        "You analyze only compressed time-window summaries, never full raw logs. "
        "Logcat is a semi-structured event stream, not natural-language text. "
        "generic events are weak evidence and cannot alone prove app internal errors. "
        "System/vendor/unrelated SDK tags should be downgraded. "
        "Do not fabricate evidence. Return JSON only."
    )


def build_user_prompt(window_summary: Dict[str, Any]) -> str:
    schema = {
        "state": "one of fixed states",
        "confidence": "float in [0,1]",
        "judgement_type": "strong|weak",
        "evidence": ["short strings"],
        "noise_tags": ["tag names likely to be noise"],
        "rule_suggestions": ["short actionable suggestions"],
    }
    return (
        "Analyze this Android logcat window summary and return strict JSON.\n"
        f"Allowed states: {LLM_STATE_SPACE}\n"
        "If there is no strong app-level evidence, output NORMAL_OR_NO_STRONG_EVIDENCE or UNKNOWN.\n"
        "JSON schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n"
        "Window summary:\n"
        f"{json.dumps(window_summary, ensure_ascii=False)}"
    )
