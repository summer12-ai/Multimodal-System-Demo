"""
fusion.py —— 主控层多模态状态融合逻辑
"""

from typing import Dict, Any


def _map_rule_state(rule_state: str) -> str:
    mapping = {
        "NORMAL": "NORMAL_OR_NO_STRONG_EVIDENCE",
        "IDLE": "UNKNOWN",
        "PAGE_SWITCH": "PAGE_SWITCH",
        "PLAY_INIT": "PLAY_INIT",
        "BUFFERING": "BUFFERING",
        "NETWORK_ISSUE": "NETWORK_ISSUE",
        "INTERNAL_ERROR": "APP_INTERNAL_ERROR",
        "RECOVERED": "RECOVERED",
    }
    return mapping.get(rule_state, "UNKNOWN")


def fuse_states(ocr_snapshot: Dict[str, Any], log_snapshot: Dict[str, Any], traffic_snapshot: Dict[str, Any], target_app: str) -> Dict[str, Any]:
    """
    将 OCR 与 logcat 的最新状态融合为一个候选业务状态。
    当前为规则/启发式版本，后续可替换为统计模型或学习模型。
    """
    ocr_lag = (ocr_snapshot.get("latest_result") or {}).get("是否卡顿", "否")
    ocr_resolution = (ocr_snapshot.get("latest_result") or {}).get("分辨率", "")
    ocr_fps = (ocr_snapshot.get("latest_result") or {}).get("帧率", "")
    rule_state_raw = (log_snapshot.get("state") or {}).get("state", "UNKNOWN")
    rule_state = _map_rule_state(rule_state_raw)
    rule_conf = float((log_snapshot.get("state") or {}).get("confidence", 0.0))
    llm = log_snapshot.get("llm") or {}
    llm_state = llm.get("state", "UNKNOWN")
    llm_conf = float(llm.get("confidence", 0.0)) if llm else 0.0

    # Traffic 层输入
    tr_state = (traffic_snapshot.get("state") or {}).get("state", "UNKNOWN") if traffic_snapshot else "UNKNOWN"
    tr_conf = float((traffic_snapshot.get("state") or {}).get("confidence", 0.0)) if traffic_snapshot else 0.0
    tr_proto = traffic_snapshot.get("protocol", {}) if traffic_snapshot else {}
    tr_resolution = tr_proto.get("hls_resolution") or tr_proto.get("dash_resolution") or ""
    tr_bitrate = tr_proto.get("hls_bandwidth") or tr_proto.get("dash_bandwidth") or 0

    final_state = "NORMAL_OR_NO_STRONG_EVIDENCE"
    reasons = []
    final_conf = 0.55

    # Ground Truth 优先：Traffic 高置信度时权重最高
    if tr_conf >= 0.90 and tr_state != "UNKNOWN":
        final_state = tr_state
        final_conf = tr_conf
        reasons.append("traffic_ground_truth_high_confidence")
        if tr_state != rule_state:
            reasons.append(f"traffic_overrides_rule_{rule_state}")
        if tr_state != llm_state and llm_state not in {"UNKNOWN", "NORMAL_OR_NO_STRONG_EVIDENCE"}:
            reasons.append("traffic_overrides_llm")
    elif rule_state == "APP_INTERNAL_ERROR":
        final_state = "APP_INTERNAL_ERROR"
        final_conf = max(0.85, rule_conf)
        reasons.append("rule_detected_internal_error")
        if llm_state in {"NORMAL_OR_NO_STRONG_EVIDENCE", "UNKNOWN"} and llm_conf >= 0.55:
            final_conf = min(final_conf, 0.55)
            final_state = "UNKNOWN"
            reasons.append("llm_disagrees_rule_downgrade")
    elif ocr_lag == "是" and rule_state in {"BUFFERING", "NETWORK_ISSUE"}:
        final_state = "NETWORK_ISSUE"
        final_conf = max(0.8, rule_conf)
        reasons.append("ocr_lag_plus_rule_network_signal")
        if llm_state in {"BUFFERING", "NETWORK_ISSUE"}:
            final_conf = min(0.95, max(final_conf, llm_conf + 0.1))
            reasons.append("llm_supports_network_abnormal")
    elif ocr_lag == "是":
        final_state = "BUFFERING"
        final_conf = 0.7
        reasons.append("ocr_lag_signal")
    elif rule_state in {"PLAY_INIT", "PAGE_SWITCH"}:
        final_state = rule_state
        final_conf = max(0.65, rule_conf)
        reasons.append(f"rule_state_{rule_state.lower()}")
    elif rule_state == "RECOVERED":
        final_state = "RECOVERED"
        final_conf = max(0.7, rule_conf)
        reasons.append("rule_recovered_signal")
    elif llm_state not in {"UNKNOWN", "NORMAL_OR_NO_STRONG_EVIDENCE"} and llm_conf >= 0.65:
        # 保守使用 LLM：仅在规则没有强信号时作为弱引导
        final_state = llm_state
        final_conf = min(0.75, llm_conf)
        reasons.append("llm_weak_guidance")

    # Traffic 中等置信度作为辅助验证
    if tr_state not in {"UNKNOWN", "NORMAL_OR_NO_STRONG_EVIDENCE"} and tr_conf >= 0.75 and final_state == "NORMAL_OR_NO_STRONG_EVIDENCE":
        final_state = tr_state
        final_conf = min(0.85, tr_conf)
        reasons.append("traffic_supports_abnormal")

    # 规则与 LLM 一致时小幅加分
    if llm_state == rule_state and llm_state not in {"UNKNOWN", "NORMAL_OR_NO_STRONG_EVIDENCE"}:
        final_conf = min(0.97, max(final_conf, (rule_conf + llm_conf) / 2 + 0.08))
        reasons.append("rule_llm_agree_boost")

    # Traffic 与规则一致时加分
    if tr_state == rule_state and tr_state not in {"UNKNOWN", "NORMAL_OR_NO_STRONG_EVIDENCE"}:
        final_conf = min(0.97, max(final_conf, (tr_conf + rule_conf) / 2 + 0.08))
        reasons.append("traffic_rule_agree_boost")

    return {
        "target_app": target_app,
        "rule_state": rule_state,
        "rule_confidence": round(rule_conf, 3),
        "llm_state": llm_state,
        "llm_confidence": round(llm_conf, 3),
        "traffic_state": tr_state,
        "traffic_confidence": round(tr_conf, 3),
        "final_state": final_state,
        "final_confidence": round(final_conf, 3),
        # 兼容旧字段
        "fused_state": final_state,
        "confidence": round(final_conf, 3),
        "reasons": reasons,
        "llm_evidence": llm.get("evidence", []),
        "llm_noise_tags": llm.get("noise_tags", []),
        "llm_rule_suggestions": llm.get("rule_suggestions", []),
        "ocr": {
            "resolution": ocr_resolution,
            "is_lag": ocr_lag,
            "fps": ocr_fps,
            "frames": ocr_snapshot.get("frames", 0),
            "errors": ocr_snapshot.get("errors", 0),
        },
        "logcat": {
            "state": rule_state_raw,
            "events": log_snapshot.get("events", 0),
            "errors": log_snapshot.get("errors", 0),
        },
        "traffic": {
            "state": tr_state,
            "confidence": tr_conf,
            "resolution": tr_resolution,
            "bitrate_bps": tr_bitrate,
            "rx_rate": (traffic_snapshot.get("net_stats") or {}).get("rx_rate", 0.0) if traffic_snapshot else 0.0,
            "rssi": (traffic_snapshot.get("net_stats") or {}).get("rssi") if traffic_snapshot else None,
            "errors": traffic_snapshot.get("errors", 0) if traffic_snapshot else 0,
        },
    }
