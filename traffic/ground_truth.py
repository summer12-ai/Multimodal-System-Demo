"""
traffic/ground_truth.py —— 三模态对齐与准确率评估引擎

以 Traffic 为物理层基准，对齐 OCR（UI 文字）和 Logcat（日志语义），
输出各模态准确率、混淆矩阵、冲突诊断。
"""

import time
from collections import Counter
from typing import Dict, Any, List, Optional, Deque
from collections import deque

from .models import GroundTruthResult
from .config import GROUND_TRUTH_WINDOW_SEC, MIN_SAMPLES_FOR_ACCURACY


class GroundTruthEngine:
    """
    维护三模态历史记录，执行滑动窗口对齐与评估。
    """

    def __init__(self):
        self._results: Deque[GroundTruthResult] = deque(maxlen=500)

    @staticmethod
    def _normalize_state(state: str) -> str:
        """
        统一各模态的异构状态命名。
        Logcat detector 用 'NORMAL'，Traffic 用 'NORMAL_OR_NO_STRONG_EVIDENCE'，
        比较前必须先归一化。
        """
        mapping = {
            "NORMAL": "NORMAL_OR_NO_STRONG_EVIDENCE",
            "IDLE": "UNKNOWN",
            "INTERNAL_ERROR": "APP_INTERNAL_ERROR",
        }
        return mapping.get(state, state)

    @staticmethod
    def _majority_vote(states: List[str], exclude: Optional[set] = None) -> str:
        """取最频繁的非 UNKNOWN / NORMAL 状态；若都没有则取 NORMAL。"""
        # 先归一化所有状态
        normalized = [GroundTruthEngine._normalize_state(s) for s in states]
        exclude = exclude or {"UNKNOWN"}
        filtered = [s for s in normalized if s not in exclude]
        if not filtered:
            # fallback
            c = Counter(normalized)
            return c.most_common(1)[0][0] if c else "UNKNOWN"
        c = Counter(filtered)
        return c.most_common(1)[0][0]

    def evaluate(
        self,
        traffic_history: List[Dict[str, Any]],
        ocr_history: List[Dict[str, Any]],
        log_history: List[Dict[str, Any]],
    ) -> GroundTruthResult:
        """
        以 traffic 最新时刻为锚点，在前后窗口内对齐 OCR/Log 结果。
        """
        if not traffic_history:
            return GroundTruthResult(
                timestamp=time.time(),
                traffic_state="UNKNOWN",
                ocr_state="UNKNOWN",
                log_state="UNKNOWN",
                ocr_match=False,
                log_match=False,
                three_way_agree=False,
                ocr_false_positive=False,
                ocr_false_negative=False,
                log_false_positive=False,
                log_false_negative=False,
                discrepancy=False,
                details="no_traffic_data",
            )

        anchor = traffic_history[-1]
        anchor_ts = anchor.get("timestamp", time.time())
        traffic_state = anchor.get("state", "UNKNOWN")

        # 窗口内 OCR 投票
        ocr_window = [
            h for h in ocr_history
            if anchor_ts - GROUND_TRUTH_WINDOW_SEC <= h.get("timestamp", 0) <= anchor_ts + GROUND_TRUTH_WINDOW_SEC
        ]
        ocr_states = [h.get("state", "UNKNOWN") for h in ocr_window] if ocr_window else ["UNKNOWN"]
        ocr_vote = self._majority_vote(ocr_states)

        # 窗口内 Logcat 投票
        log_window = [
            h for h in log_history
            if anchor_ts - GROUND_TRUTH_WINDOW_SEC <= h.get("timestamp", 0) <= anchor_ts + GROUND_TRUTH_WINDOW_SEC
        ]
        log_states = [h.get("state", "UNKNOWN") for h in log_window] if log_window else ["UNKNOWN"]
        log_vote = self._majority_vote(log_states)

        # 对比（统一归一化后比较）
        norm_traffic = self._normalize_state(traffic_state)
        norm_ocr = self._normalize_state(ocr_vote)
        norm_log = self._normalize_state(log_vote)

        ocr_match = norm_traffic == norm_ocr
        log_match = norm_traffic == norm_log
        three_way = norm_traffic == norm_ocr == norm_log

        # 细分指标定义（以 Traffic 为真值）
        ocr_fp = (norm_ocr != "NORMAL_OR_NO_STRONG_EVIDENCE" and norm_traffic == "NORMAL_OR_NO_STRONG_EVIDENCE")
        ocr_fn = (norm_ocr == "NORMAL_OR_NO_STRONG_EVIDENCE" and norm_traffic not in {"NORMAL_OR_NO_STRONG_EVIDENCE", "UNKNOWN"})
        log_fp = (norm_log != "NORMAL_OR_NO_STRONG_EVIDENCE" and norm_traffic == "NORMAL_OR_NO_STRONG_EVIDENCE")
        log_fn = (norm_log == "NORMAL_OR_NO_STRONG_EVIDENCE" and norm_traffic not in {"NORMAL_OR_NO_STRONG_EVIDENCE", "UNKNOWN"})

        discrepancy = norm_traffic not in {norm_ocr, norm_log}
        recommended = traffic_state if (discrepancy and traffic_state != "UNKNOWN") else None

        details_parts = []
        if not ocr_match:
            details_parts.append(f"ocr={ocr_vote}_vs_traffic={traffic_state}")
        if not log_match:
            details_parts.append(f"log={log_vote}_vs_traffic={traffic_state}")
        if three_way:
            details_parts.append("all_agree")

        result = GroundTruthResult(
            timestamp=anchor_ts,
            traffic_state=traffic_state,
            ocr_state=ocr_vote,
            log_state=log_vote,
            ocr_match=ocr_match,
            log_match=log_match,
            three_way_agree=three_way,
            ocr_false_positive=ocr_fp,
            ocr_false_negative=ocr_fn,
            log_false_positive=log_fp,
            log_false_negative=log_fn,
            discrepancy=discrepancy,
            recommended_fusion=recommended,
            details="; ".join(details_parts) if details_parts else "aligned",
        )

        self._results.append(result)
        return result

    def get_accuracy_report(self) -> Dict[str, Any]:
        """基于历史评估结果生成整体准确率报告"""
        if len(self._results) < MIN_SAMPLES_FOR_ACCURACY:
            return {"status": "insufficient_samples", "min_required": MIN_SAMPLES_FOR_ACCURACY}

        total = len(self._results)
        ocr_matches = sum(1 for r in self._results if r.ocr_match)
        log_matches = sum(1 for r in self._results if r.log_match)
        three_way = sum(1 for r in self._results if r.three_way_agree)

        ocr_fp = sum(1 for r in self._results if r.ocr_false_positive)
        ocr_fn = sum(1 for r in self._results if r.ocr_false_negative)
        log_fp = sum(1 for r in self._results if r.log_false_positive)
        log_fn = sum(1 for r in self._results if r.log_false_negative)

        # 按状态统计
        state_counter: Dict[str, Dict[str, int]] = {}
        for r in self._results:
            st = r.traffic_state
            if st not in state_counter:
                state_counter[st] = {"total": 0, "ocr_match": 0, "log_match": 0}
            state_counter[st]["total"] += 1
            if r.ocr_match:
                state_counter[st]["ocr_match"] += 1
            if r.log_match:
                state_counter[st]["log_match"] += 1

        per_state = {}
        for st, c in state_counter.items():
            per_state[st] = {
                "traffic_count": c["total"],
                "ocr_accuracy": round(c["ocr_match"] / c["total"], 3) if c["total"] else 0,
                "log_accuracy": round(c["log_match"] / c["total"], 3) if c["total"] else 0,
            }

        return {
            "status": "ok",
            "total_windows": total,
            "overall": {
                "ocr_accuracy": round(ocr_matches / total, 3),
                "log_accuracy": round(log_matches / total, 3),
                "three_way_agreement": round(three_way / total, 3),
                "ocr_false_positive_rate": round(ocr_fp / total, 3),
                "ocr_false_negative_rate": round(ocr_fn / total, 3),
                "log_false_positive_rate": round(log_fp / total, 3),
                "log_false_negative_rate": round(log_fn / total, 3),
            },
            "per_state": per_state,
        }
