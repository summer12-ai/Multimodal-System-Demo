# ADB 多模态状态识别主控 Demo

## 项目定位

本项目是一个主控型多模块 Demo，顶层由主控层统一调度：

- OCR 子模块：ADB 截图 + PaddleOCR 离线识别，可提取分辨率、卡顿提示、fps。
- Logcat 子模块：实时采集 `adb logcat`，做结构化解析、模板归一化、时间窗规则识别。
- 主控层：统一生命周期管理、状态融合、监控输出、结果归档。

核心目标是把 OCR 作为下属能力模块，而不是整个工程主题。

---

## 目录结构

```text
demo/
├── main.py                  # 主控入口
├── config.py                # 主控级配置
├── core/
│   ├── orchestrator.py      # 主控调度器（统一拉起 OCR + Logcat）
│   ├── fusion.py            # 多模态状态融合规则
│   ├── dashboard.py         # 统一终端监控视图
│   └── result_hub.py        # 统一结果输出（JSONL + CSV）
├── ocr/                     # OCR 子模块（保留既有核心能力，新增 service 适配）
│   ├── service.py
│   ├── adb_controller.py
│   ├── analyzer.py
│   ├── config.py
│   ├── image_utils.py
│   ├── ocr_engine.py
│   └── result_manager.py
├── logcat/                  # 日志子模块
│   ├── collector.py         # adb logcat 持续采集
│   ├── parser.py            # threadtime 结构化解析
│   ├── normalizer.py        # message 模板归一化
│   ├── detector.py          # 时间窗统计 + 启发式状态机
│   ├── models.py
│   └── service.py
├── results/                 # 统一输出目录（运行后自动创建 run_xxx）
├── requirements.txt
└── README.md
```

---

## 环境要求

- 操作系统：Windows 10/11（Linux/macOS 也可运行）
- Python：3.10 ~ 3.12
- ADB：已安装并加入 `PATH`
- Android 设备：开启 USB 调试
- 依赖：`paddlepaddle==3.2.2`、`paddleocr`、`Pillow`、`openpyxl`、`numpy`

安装方式：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 运行方式

### 1) 直接运行（持续模式）

```bash
python main.py --target-app 虎牙直播
```

### 2) 限时运行（例如 2 分钟）

```bash
python main.py --target-app 虎牙直播 --duration-min 1 
```

### 3) 常用参数

- `--device-id`：多设备场景下指定序列号
- `--refresh-sec`：主控看板刷新周期（默认 1.0 秒）
- `--log-window-sec`：日志时间窗长度（默认 12 秒）
- `--enable-llm-log-analysis`：开启 LLM 日志语义增强（默认关闭）
- `--llm-provider / --llm-model / --llm-api-key`：LLM 配置项
- `--llm-base-url`：自定义 chat/completions endpoint（不传时按 provider 自动推断）

---

## 主控输出与结果归档

主控终端视图会统一展示：

- OCR 最新摘要：分辨率 / 是否卡顿 / fps / 处理帧数 / 错误数
- Logcat 摘要：最新候选状态 / 置信度 / 时间窗统计
- 融合状态：主控层综合判断结果（含置信度）
- 最近事件摘要：OCR 事件 + 日志事件 + 融合事件

每次运行会在 `results/run_YYYYMMDD_HHMMSS/` 下输出：

- `ocr_events.jsonl`
- `log_events.jsonl`
- `detector_events.jsonl`
- `fused_states.jsonl`
- `fused_states.csv`

LLM 开启后，`fused_states` 会额外包含：

- `rule_state / rule_confidence`
- `llm_state / llm_confidence`
- `final_state / final_confidence`
- `llm_evidence / llm_noise_tags / llm_rule_suggestions`

### Qwen（阿里云百炼）示例

```bash
# PowerShell
$env:DASHSCOPE_API_KEY="你的DashScopeKey"
python main.py --target-app 虎牙直播 --duration-min 2 --enable-llm-log-analysis --llm-provider qwen --llm-model qwen-plus
```

默认会使用 DashScope OpenAI 兼容 endpoint。若你的环境有特殊网关，可通过 `--llm-base-url` 覆盖。

---

## 日志模块算法路线

日志子模块采用“规则 + 模板 + 时间窗 + 状态机”：

1. 采集：`adb logcat -v threadtime` 持续读取日志流。
2. 解析：提取时间、级别、tag、pid、tid、message。
3. 归一化：将 message 中数字、IP、十六进制等替换为占位符，形成模板文本。
4. 分类：基于关键词把模板映射为 `page_switch / play_init / buffering / retry / timeout / network_issue / internal_error / recovered` 等事件类型。
5. 时间窗统计：在固定秒数窗口内统计事件频次。
6. 状态机判定：输出候选业务状态（如 `BUFFERING`、`NETWORK_ISSUE`、`INTERNAL_ERROR`、`RECOVERED`）。

---

## 当前版本局限

- 日志模式识别为启发式规则，依赖关键词覆盖度。
- 不同厂商/ROM/业务 App 的日志格式差异可能导致召回不足。
- OCR 与日志融合规则是第一版，尚未做数据驱动标定。
- 目前未接入 ML/LLM 语义分类器，后续可在 `logcat/normalizer.py` 和 `core/fusion.py` 扩展。

---

## 说明

- `ocr/` 目录被视为已完成子模块，当前只做了主控接入所需的最小适配（新增 `service.py` + 包内导入修正）。
- 根目录旧版 OCR 脚本文件已移除，统一以 `ocr/` 子模块作为唯一 OCR 实现入口。
