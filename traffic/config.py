"""
traffic/config.py —— 流量子模块配置与阈值
"""

from typing import Dict, List, Set

# ============================================================
# 1. 状态空间（必须与 core/fusion.py 对齐）
# ============================================================
TRAFFIC_STATE_SPACE: List[str] = [
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

# ============================================================
# 2. App 包名映射（与 logcat/service.py 的 packages 对齐）
# ============================================================
APP_PACKAGE_MAP: Dict[str, str] = {
    "虎牙直播": "com.duowan.kiwi",
    "虎牙": "com.duowan.kiwi",
    "斗鱼直播": "air.tv.douyu.android",
    "斗鱼": "air.tv.douyu.android",
    "抖音": "com.ss.android.ugc.aweme",
    "快手": "com.smile.gifmaker",
    "B站": "tv.danmaku.bili",
    "哔哩哔哩": "tv.danmaku.bili",
    "腾讯视频": "com.tencent.qqlive",
}

# ============================================================
# 3. 带宽/速率阈值（字节/秒）
# ============================================================
# 判定播放中的下行速率下限（约 200KB/s）
RATE_PLAYING_MIN: int = 200 * 1024

# 判定缓冲的下行速率上限（约 50KB/s）
RATE_BUFFERING_MAX: int = 50 * 1024

# 判定网络问题的下行速率上限（约 10KB/s，持续 3 秒以上）
RATE_NETWORK_ISSUE_MAX: int = 10 * 1024

# 速率骤降比例（当前速率 < 前次速率 * (1 - RATE_DROP_RATIO)）
RATE_DROP_RATIO: float = 0.80

# 播放初始化突发流量下限（峰值 > 1MB/s 且之前静默）
RATE_BURST_MIN: int = 1024 * 1024

# ============================================================
# 4. WiFi / 信号阈值
# ============================================================
# RSSI 弱网阈值 (dBm)
RSSI_WEAK_THRESHOLD: int = -85

# RSSI 正常阈值 (dBm)
RSSI_NORMAL_THRESHOLD: int = -70

# ============================================================
# 5. 采集参数
# ============================================================
# Stats 采样周期（秒）
STATS_SAMPLE_INTERVAL: float = 1.0

# PID 刷新周期（秒）
PID_REFRESH_INTERVAL: float = 5.0

# PCAP 动态 BPF IP 列表刷新周期（秒）
PCAP_IP_REFRESH_INTERVAL: float = 5.0

# 历史样本最大保留数
MAX_HISTORY_SAMPLES: int = 500

# ============================================================
# 6. Ground Truth 评估参数
# ============================================================
# 三模态对齐窗口半宽（秒）
GROUND_TRUTH_WINDOW_SEC: float = 2.0

# 最低统计窗口数才输出准确率
MIN_SAMPLES_FOR_ACCURACY: int = 10

# ============================================================
# 7. 视频 CDN / 域名特征（用于辅助识别，非必须）
# ============================================================
VIDEO_CDN_KEYWORDS: Set[str] = {
    "live", "video", "stream", "cdn", "pull", "push",
    "hls", "dash", "flv", "mp4", "m3u8", "mpd",
    "tiktok", "douyin", "huya", "douyu", "bili", "qqlive",
    "akamaized", "cloudfront", "fastly", "quic",
}
