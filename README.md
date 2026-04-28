# ADB Multi-Modal State Recognition Demo

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)]()

A master-controlled multi-module demo for real-time Android app state recognition via **OCR + Logcat + Traffic** fusion.

## Overview

This project unifies three sensing modalities under a single orchestrator:

- **OCR Module**: ADB screenshot + PaddleOCR offline recognition. Extracts resolution, lag hints, and FPS from UI text.
- **Logcat Module**: Real-time `adb logcat` collection with structured parsing, template normalization, time-window rule detection, and optional LLM semantic enhancement.
- **Traffic Module**: Uses root privileges to collect network stats (`dumpsys netstats`, `/proc/<pid>/net/tcp`) and packet-level captures (`tcpdump`). Identifies playback states, buffering, and network issues. Serves as **Ground Truth** to evaluate OCR/Logcat accuracy.

The master layer fuses outputs from all three modules, renders a unified dashboard, and archives structured results.

---

## Directory Structure

```
.
├── main.py                  # Entry point
├── config.py                # Project-level configuration
├── core/
│   ├── orchestrator.py      # Master scheduler (OCR + Logcat + Traffic)
│   ├── fusion.py            # Multi-modal state fusion rules
│   ├── dashboard.py         # Unified terminal monitor
│   └── result_hub.py        # Unified output (JSONL + CSV)
├── ocr/                     # OCR submodule
│   ├── service.py
│   ├── adb_controller.py
│   ├── analyzer.py
│   ├── config.py
│   ├── image_utils.py
│   ├── ocr_engine.py
│   └── result_manager.py
├── logcat/                  # Logcat submodule
│   ├── service.py
│   ├── collector.py
│   ├── parser.py
│   ├── normalizer.py
│   ├── detector.py
│   ├── llm_analyzer.py
│   ├── models.py
│   └── prompt_templates.py
├── traffic/                 # Traffic submodule
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
├── results/                 # Auto-created run_YYYYMMDD_HHMMSS/
├── requirements.txt
└── README.md
```

---

## Requirements

- **OS**: Windows 10/11 (Linux/macOS also supported)
- **Python**: 3.10 ~ 3.12
- **ADB**: Installed and in `PATH`
- **Android Device**: USB debugging enabled
- **Root** (for Traffic packet capture): Device must be rooted, or use `--disable-traffic-pcap` to keep only the stats layer.

### Dependencies

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

| Package | Version | Purpose |
|---------|---------|---------|
| `paddlepaddle` | `==3.2.2` | OCR inference engine |
| `paddleocr` | `>=2.7.0` | Offline OCR |
| `Pillow` | `>=10.0.0` | Image processing |
| `openpyxl` | `>=3.1.0` | Excel export |
| `numpy` | `>=1.24.0` | Numerical utilities |

> **Note**: `paddlepaddle==3.2.2` is pinned because 3.3.x has oneDNN compatibility issues on Windows CPU.

---

## Quick Start

### 1. Run with default settings (continuous mode)

```bash
python main.py --target-app 虎牙直播
```

### 2. Run for a limited duration (e.g., 1 minute)

```bash
python main.py --target-app 虎牙直播 --duration-min 1
```

### 3. Disable Traffic module (if device is not rooted)

```bash
python main.py --target-app 虎牙直播 --disable-traffic
```

### 4. Use LLM semantic enhancement (optional)

```bash
# OpenAI
export OPENAI_API_KEY="your-key"
python main.py --target-app 虎牙直播 --enable-llm-log-analysis

# Alibaba Qwen
export DASHSCOPE_API_KEY="your-key"
python main.py --target-app 虎牙直播 --enable-llm-log-analysis --llm-provider qwen --llm-model qwen-plus
```

---

## CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--target-app` | `虎牙直播` | Target app name (for OCR prior) |
| `--duration-min` | `None` | Run duration in minutes (infinite if not set) |
| `--device-id` | `None` | ADB device serial (for multi-device) |
| `--refresh-sec` | `1.0` | Dashboard refresh interval |
| `--log-window-sec` | `12` | Logcat time window length |
| `--enable-llm-log-analysis` | `False` | Enable LLM semantic enhancement |
| `--llm-provider` | `openai` | LLM provider (`openai` or `qwen`) |
| `--llm-model` | `gpt-4.1-mini` | LLM model name |
| `--llm-api-key` | `""` | LLM API key (falls back to env var) |
| `--llm-base-url` | `""` | Custom chat/completions endpoint |
| `--llm-timeout-sec` | `10` | LLM request timeout |
| `--llm-min-interval-sec` | `10` | Minimum LLM call interval |
| `--disable-traffic` | `False` | Disable Traffic module |
| `--traffic-package` | `None` | Target package name (e.g., `com.duowan.kiwi`) |
| `--disable-traffic-pcap` | `False` | Disable tcpdump, keep stats layer only |
| `--traffic-window-sec` | `12.0` | Traffic state machine window |

---

## Outputs

Each run creates a timestamped directory under `results/run_YYYYMMDD_HHMMSS/`:

| File | Description |
|------|-------------|
| `ocr_events.jsonl` | OCR recognition results |
| `log_events.jsonl` | Raw logcat events |
| `detector_events.jsonl` | Logcat detector snapshots |
| `traffic_events.jsonl` | Traffic module snapshots |
| `ground_truth_eval.jsonl` | Ground truth alignment evaluations |
| `fused_states.jsonl` | Fusion state decisions |
| `fused_states.csv` | Fusion state table (CSV) |

The terminal dashboard displays real-time summaries for all modules and the fused state.

---

## Architecture

### State Fusion Rules

Traffic acts as the physical-layer ground truth. When Traffic confidence >= 0.90, its state overrides OCR/Logcat decisions.

### Traffic Module Pipeline

1. **Stats Layer**: Periodic `dumpsys netstats --uid <uid>`, `/proc/<pid>/net/tcp`, `dumpsys wifi` for cumulative bytes, active connections, and RSSI.
2. **Packet Layer**: `adb shell tcpdump` on the active interface (`wlan0` preferred) to capture packet sizes, TCP flags, and HTTP headers.
3. **Content Layer**: Sniff HLS M3U8 (`#EXT-X-STREAM-INF`), DASH MPD (`<Representation>`), and FLV headers from tcpdump `-A` ASCII payloads.
4. **State Machine**: Bandwidth time-series analysis (burst/drop/sustained low) + protocol metadata → structured state output.
5. **Ground Truth**: Aligns OCR/Logcat results with Traffic in a 2-second sliding window, reporting per-modality accuracy and confusion matrices.

---

## Known Limitations

- Log pattern recognition is heuristic and keyword-dependent; OEM/ROM differences may affect recall.
- Traffic protocol deep-parsing (HLS/DASH payload) relies on tcpdump ASCII output; TLS/QUIC encrypted streams cannot be parsed.
- OCR + Logcat fusion rules are first-generation heuristic rules; data-driven calibration is future work.
- Traffic `pcap` collector may miss packets during interface auto-detection retries (device-specific).

---

## License

MIT License — feel free to use, modify, and distribute.
