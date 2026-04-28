"""
llm_analyzer.py —— LLM 语义增强分析器（可选）
"""

import json
import urllib.request
from typing import Optional, Dict, Any

from .prompt_templates import build_system_prompt, build_user_prompt, LLM_STATE_SPACE


class LLMSemanticAnalyzer:
    """
    仅处理窗口摘要，不接触原始全量日志。
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4.1-mini",
        api_key: str = "",
        base_url: str = "",
        timeout_sec: int = 10,
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.strip()
        self.timeout_sec = timeout_sec

    def available(self) -> bool:
        return self.provider in {"openai", "qwen"} and bool(self.api_key)

    def _resolve_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.provider == "qwen":
            # DashScope OpenAI-compatible endpoint
            return "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        return "https://api.openai.com/v1/chat/completions"

    @staticmethod
    def _parse_content_json(content: str) -> Optional[Dict[str, Any]]:
        content = (content or "").strip()
        if not content:
            return None
        try:
            return json.loads(content)
        except Exception:
            pass
        # 容错：提取首尾大括号之间的 JSON
        left = content.find("{")
        right = content.rfind("}")
        if left >= 0 and right > left:
            try:
                return json.loads(content[left:right + 1])
            except Exception:
                return None
        return None

    @staticmethod
    def _normalize_output(obj: Dict[str, Any]) -> Dict[str, Any]:
        state = str(obj.get("state", "UNKNOWN"))
        if state not in LLM_STATE_SPACE:
            state = "UNKNOWN"
        try:
            confidence = float(obj.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        judgement_type = str(obj.get("judgement_type", "weak")).lower()
        if judgement_type not in {"strong", "weak"}:
            judgement_type = "weak"
        evidence = [str(x) for x in (obj.get("evidence") or [])][:8]
        noise_tags = [str(x) for x in (obj.get("noise_tags") or [])][:8]
        rule_suggestions = [str(x) for x in (obj.get("rule_suggestions") or [])][:8]
        return {
            "state": state,
            "confidence": round(confidence, 3),
            "judgement_type": judgement_type,
            "evidence": evidence,
            "noise_tags": noise_tags,
            "rule_suggestions": rule_suggestions,
        }

    def _call_chat(self, window_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            return None
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": build_user_prompt(window_summary)},
            ],
            "temperature": 0.1,
        }
        # DashScope 的兼容层并不保证与 OpenAI 完全一致，避免传入可能不兼容字段
        if self.provider == "openai":
            payload["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            url=self._resolve_base_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "{}")
            )
            result = self._parse_content_json(content)
            if result is None:
                return None
            return self._normalize_output(result)
        except Exception:
            return None

    def analyze(self, window_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.available():
            return None
        if self.provider in {"openai", "qwen"}:
            return self._call_chat(window_summary)
        return None
