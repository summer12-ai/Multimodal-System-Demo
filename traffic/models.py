"""
traffic/models.py —— 流量子模块数据模型
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List


@dataclass
class NetStatsEntry:
    """单次网络采样记录"""
    timestamp: float  # time.time()
    uid: int
    pid: str
    rx_bytes: int = 0          # 累计接收字节（本次采样值）
    tx_bytes: int = 0          # 累计发送字节（本次采样值）
    rx_bytes_delta: int = 0    # 距离上次采样的增量
    tx_bytes_delta: int = 0
    rx_rate: float = 0.0       # 接收速率 bytes/sec
    tx_rate: float = 0.0       # 发送速率 bytes/sec
    active_connections: int = 0
    rssi: Optional[int] = None  # WiFi RSSI (dBm)
    link_speed: Optional[int] = None  # Mbps


@dataclass
class PacketMeta:
    """单包元数据（从 tcpdump 文本输出解析）"""
    timestamp: float
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    proto: str          # "TCP" / "UDP"
    length: int
    flags: str = ""     # TCP flags, e.g. "[S.]", "[P.]", "[R]"
    payload_preview: str = ""  # 前 256 字节 ASCII 预览


@dataclass
class TrafficSample:
    """单个时间点的综合流量样本"""
    timestamp: float
    stats: NetStatsEntry
    protocol_meta: Dict[str, Any] = field(default_factory=dict)
    # protocol_meta 可能包含:
    #   "hls_resolution": "1920x1080",
    #   "hls_bandwidth": 3500000,
    #   "dash_resolution": "1920x1080",
    #   "dash_bandwidth": 3500000,
    #   "snis": ["cdn.huya.com"],
    #   "http_hosts": ["cdn.huya.com"],


@dataclass
class TrafficState:
    """状态机输出"""
    state: str
    confidence: float
    reason: str
    counters: Dict[str, int] = field(default_factory=dict)


@dataclass
class GroundTruthResult:
    """三模态对齐评估结果"""
    timestamp: float
    traffic_state: str
    ocr_state: str
    log_state: str

    ocr_match: bool
    log_match: bool
    three_way_agree: bool

    ocr_false_positive: bool
    ocr_false_negative: bool
    log_false_positive: bool
    log_false_negative: bool

    discrepancy: bool
    recommended_fusion: Optional[str] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "traffic_state": self.traffic_state,
            "ocr_state": self.ocr_state,
            "log_state": self.log_state,
            "ocr_match": self.ocr_match,
            "log_match": self.log_match,
            "three_way_agree": self.three_way_agree,
            "ocr_false_positive": self.ocr_false_positive,
            "ocr_false_negative": self.ocr_false_negative,
            "log_false_positive": self.log_false_positive,
            "log_false_negative": self.log_false_negative,
            "discrepancy": self.discrepancy,
            "recommended_fusion": self.recommended_fusion,
            "details": self.details,
        }
