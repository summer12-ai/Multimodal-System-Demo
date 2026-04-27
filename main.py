"""
main.py —— 顶层主控入口

启动方式:
    python main.py --target-app 虎牙直播
"""

import argparse
import os

from config import ProjectConfig
from core import MasterOrchestrator, OrchestratorConfig


def parse_args():
    parser = argparse.ArgumentParser(description="ADB 多模态状态识别主控 Demo")
    parser.add_argument("--target-app", default="虎牙直播", help="目标业务 App 名称（用于 OCR 先验）")
    parser.add_argument("--duration-min", type=int, default=None, help="运行时长（分钟），不传则持续运行")
    parser.add_argument("--device-id", default=None, help="ADB 设备序列号（多设备场景）")
    parser.add_argument("--refresh-sec", type=float, default=1.0, help="主控 dashboard 刷新间隔（秒）")
    parser.add_argument("--log-window-sec", type=int, default=12, help="日志时间窗长度（秒）")
    parser.add_argument("--enable-llm-log-analysis", action="store_true", help="开启 LLM 日志语义增强（默认关闭）")
    parser.add_argument("--llm-provider", default="openai", help="LLM provider，默认 openai")
    parser.add_argument("--llm-model", default="gpt-4.1-mini", help="LLM 模型名")
    parser.add_argument("--llm-api-key", default="", help="LLM API key，未传则尝试环境变量 OPENAI_API_KEY")
    parser.add_argument("--llm-base-url", default="", help="LLM chat/completions endpoint，provider 默认值可自动推断")
    parser.add_argument("--llm-timeout-sec", type=int, default=10, help="LLM 请求超时秒数")
    parser.add_argument("--llm-min-interval-sec", type=int, default=10, help="LLM 最小调用间隔秒数")
    return parser.parse_args()


def main():
    args = parse_args()
    llm_api_key = args.llm_api_key
    if not llm_api_key:
        if args.llm_provider == "qwen":
            llm_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        else:
            llm_api_key = os.getenv("OPENAI_API_KEY", "")
    proj_cfg = ProjectConfig(
        target_app=args.target_app,
        duration_minutes=args.duration_min,
        device_id=args.device_id,
        dashboard_refresh_seconds=args.refresh_sec,
        log_window_seconds=args.log_window_sec,
        enable_llm_log_analysis=args.enable_llm_log_analysis,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_api_key=llm_api_key,
        llm_base_url=args.llm_base_url,
        llm_timeout_sec=args.llm_timeout_sec,
        llm_min_interval_sec=args.llm_min_interval_sec,
    )

    orch_cfg = OrchestratorConfig(
        target_app=proj_cfg.target_app,
        results_root=proj_cfg.results_root,
        duration_minutes=proj_cfg.duration_minutes,
        device_id=proj_cfg.device_id,
        dashboard_refresh_seconds=proj_cfg.dashboard_refresh_seconds,
        log_window_seconds=proj_cfg.log_window_seconds,
        enable_llm_log_analysis=proj_cfg.enable_llm_log_analysis,
        llm_provider=proj_cfg.llm_provider,
        llm_model=proj_cfg.llm_model,
        llm_api_key=proj_cfg.llm_api_key,
        llm_base_url=proj_cfg.llm_base_url,
        llm_timeout_sec=proj_cfg.llm_timeout_sec,
        llm_min_interval_sec=proj_cfg.llm_min_interval_sec,
    )

    orchestrator = MasterOrchestrator(orch_cfg)
    try:
        orchestrator.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
