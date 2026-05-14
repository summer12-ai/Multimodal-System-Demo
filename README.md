# ADB 多模态状态识别演示

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)]()

一个主控多模块演示项目，通过 **OCR + Logcat + Traffic** 融合实现实时 Android 应用状态识别。

## 概述

本项目将三种感知模态统一在单一编排器下：

- **OCR 模块**：ADB 截图 + PaddleOCR 离线识别。从 UI 文本中提取分辨率、卡顿提示、FPS 及游戏时延（ms）。
- **Logcat 模块**：实时 `adb logcat` 采集，支持结构化解析、模板归一化、时间窗口规则检测，以及可选的 LLM 语义增强。
- **Traffic 模块**：使用 root 权限采集网络统计信息（`dumpsys netstats`、`/proc/<pid>/net/tcp`）和数据包级捕获（`tcpdump`）。识别播放状态、缓冲和网络问题。作为评估 OCR/Logcat 准确性的**基准真值（Ground Truth）**。

主控层融合所有三个模块的输出，渲染统一监控面板，并归档结构化结果。

---

## 目录结构

```
.
├── main.py                  # 入口文件
├── config.py                # 项目级配置
├── core/
│   ├── orchestrator.py      # 主调度器（OCR + Logcat + Traffic）
│   ├── fusion.py            # 多模态状态融合规则
│   ├── dashboard.py         # 统一终端监控
│   └── result_hub.py        # 统一输出（JSONL + CSV）
├── ocr/                     # OCR 子模块
│   ├── service.py
│   ├── adb_controller.py
│   ├── analyzer.py
│   ├── config.py
│   ├── image_utils.py
│   ├── ocr_engine.py
│   └── result_manager.py
├── logcat/                  # Logcat 子模块
│   ├── service.py
│   ├── collector.py
│   ├── parser.py
│   ├── normalizer.py
│   ├── detector.py
│   ├── llm_analyzer.py
│   ├── models.py
│   └── prompt_templates.py
├── traffic/                 # Traffic 子模块
│   ├── service.py
│   ├── ground_truth.py
│   ├── config.py
│   ├── models.py
│   ├── collector/
│   │   ├── stats_collector.py
│   │   └── pcap_collector.py
│   ├── parser/
│   │   ├── netstats_parser.py
│   │   └── pcap_parser.py
│   └── analyzer/
│       ├── bandwidth_analyzer.py
│       └── state_detector.py
├── results/                 # 自动创建 run_YYYYMMDD_HHMMSS/
├── requirements.txt
└── README.md
```

---

## 环境要求

- **操作系统**：Windows 10/11（也支持 Linux/macOS）
- **Python**：3.10 ~ 3.12
- **ADB**：已安装并配置到 `PATH`
- **Android 设备**：已开启 USB 调试
- **Root**（用于 Traffic 数据包捕获）：设备必须已 root，或使用 `--disable-traffic-pcap` 仅保留统计层

### 依赖安装

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

| 包名 | 版本 | 用途 |
|---------|---------|---------|
| `paddlepaddle` | `==3.2.2` | OCR 推理引擎 |
| `paddleocr` | `>=2.7.0` | 离线 OCR |
| `Pillow` | `>=10.0.0` | 图像处理 |
| `openpyxl` | `>=3.1.0` | Excel 导出 |
| `numpy` | `>=1.24.0` | 数值工具 |

> **注意**：`paddlepaddle==3.2.2` 被固定是因为 3.3.x 在 Windows CPU 上存在 oneDNN 兼容性问题。

---

## 快速开始

### 1. 使用默认设置运行（持续模式）

```bash
python main.py --target-app 虎牙直播
```

### 2. 限时运行（例如 1 分钟）

```bash
python main.py --target-app 虎牙直播 --duration-min 1
```

### 3. 禁用 Traffic 模块（如果设备未 root）

```bash
python main.py --target-app 虎牙直播 --disable-traffic
```

### 4. 使用 LLM 语义增强（可选）

```bash
# OpenAI
export OPENAI_API_KEY="your-key"
python main.py --target-app 虎牙直播 --enable-llm-log-analysis

# 阿里云通义千问
export DASHSCOPE_API_KEY="your-key"
python main.py --target-app 虎牙直播 --enable-llm-log-analysis --llm-provider qwen --llm-model qwen-plus
```

---

## 命令行参数

| 参数 | 默认值 | 说明 |
|----------|---------|-------------|
| `--target-app` | `虎牙直播` | 目标应用名称（用于 OCR 优先） |
| `--duration-min` | `None` | 运行时长（分钟），未设置则无限运行 |
| `--device-id` | `None` | ADB 设备序列号（多设备时使用） |
| `--refresh-sec` | `1.0` | 监控面板刷新间隔 |
| `--log-window-sec` | `12` | Logcat 时间窗口长度 |
| `--enable-llm-log-analysis` | `False` | 启用 LLM 语义增强 |
| `--llm-provider` | `openai` | LLM 提供商（`openai` 或 `qwen`） |
| `--llm-model` | `gpt-4.1-mini` | LLM 模型名称 |
| `--llm-api-key` | `""` | LLM API 密钥（回退到环境变量） |
| `--llm-base-url` | `""` | 自定义 chat/completions 端点 |
| `--llm-timeout-sec` | `10` | LLM 请求超时时间 |
| `--llm-min-interval-sec` | `10` | LLM 调用最小间隔 |
| `--disable-traffic` | `False` | 禁用 Traffic 模块 |
| `--traffic-package` | `None` | 目标包名（例如 `com.duowan.kiwi`） |
| `--disable-traffic-pcap` | `False` | 禁用 tcpdump，仅保留统计层 |
| `--traffic-window-sec` | `12.0` | Traffic 状态机窗口 |

---

## 输出

每次运行会在 `results/run_YYYYMMDD_HHMMSS/` 下创建带时间戳的目录：

| 文件 | 说明 |
|------|-------------|
| `ocr_events.jsonl` | OCR 识别结果 |
| `log_events.jsonl` | 原始 logcat 事件 |
| `detector_events.jsonl` | Logcat 检测器快照 |
| `traffic_events.jsonl` | Traffic 模块快照 |
| `ground_truth_eval.jsonl` | 基准真值对齐评估 |
| `fused_states.jsonl` | 融合状态决策 |
| `fused_states.csv` | 融合状态表（CSV） |

终端监控面板会实时显示所有模块和融合状态的摘要。

---

## 架构

### OCR 模块增强：游戏时延识别

针对（云）游戏类应用，OCR 模块新增网络延迟识别能力：

- **支持游戏**：王者荣耀、和平精英、英雄联盟手游、暗区突围等 20+ 款主流手游。
- **检测区域**：左上角、右上角、左下角、底部中央等游戏 UI 常见延迟显示位置。
- **匹配格式**：`20ms`、`Ping:25`、`Latency: 30`、`延迟: 60`、`网络 45` 等。
- **实现方式**：先对全图 OCR 识别，若未命中，则对重点检测区域裁剪拼接后二次识别，提升小字/半透明 UI 的识别率。

### 状态融合规则

Traffic 作为物理层基准真值。当 Traffic 置信度 >= 0.90 时，其状态将覆盖 OCR/Logcat 的决策。

### Traffic 模块流水线

1. **统计层**：周期性执行 `dumpsys netstats --uid <uid>`、`/proc/<pid>/net/tcp`、`dumpsys wifi`，获取累积字节数、活动连接和 RSSI。
2. **数据包层**：在活动接口上（优先 `wlan0`）执行 `adb shell tcpdump`，捕获数据包大小、TCP 标志和 HTTP 头。
3. **内容层**：从 tcpdump `-A` ASCII 负载中嗅探 HLS M3U8（`#EXT-X-STREAM-INF`）、DASH MPD（`<Representation>`）和 FLV 头。
4. **状态机**：带宽时序分析（突发/下降/持续低）+ 协议元数据 → 结构化状态输出。
5. **基准真值**：在 2 秒滑动窗口中对齐 OCR/Logcat 结果与 Traffic，报告各模态准确率和混淆矩阵。

---

## 已知限制

- 日志模式识别基于启发式和关键词；OEM/ROM 差异可能影响召回率。
- Traffic 协议深度解析（HLS/DASH 负载）依赖 tcpdump ASCII 输出；TLS/QUIC 加密流无法解析。
- OCR + Logcat 融合规则是第一代启发式规则；数据驱动校准是未来工作。
- Traffic `pcap` 采集器在接口自动检测重试期间可能丢包（设备相关）。

---

## 许可证

MIT 许可证 — 可自由使用、修改和分发。
