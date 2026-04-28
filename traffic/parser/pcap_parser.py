"""
traffic/parser/pcap_parser.py —— tcpdump 文本输出解析与 payload 嗅探

保持与 collector/pcap_collector.py 中的正则一致，作为独立工具函数供外部使用。
"""

import re
from typing import Optional, Dict, Any, List


# tcpdump 文本行正则（标准 -q 输出格式）
# Android -i any 输出带接口方向前缀：wlan0 Out / wlan0 In
# 格式示例：
#   1777391896.194391 wlan0 Out IP 10.11.35.189.44012 > 111.206.139.28.8080: tcp 2
#   1777392150.832321 IP 10.11.35.189.55573 > 172.19.2.1.53: UDP, length 33
#   1777391638.253158 wlan0 Out IP 10.11.35.189 > 10.11.35.254: ICMP echo request, id 588, seq 1, length 8
TCPDUMP_LINE_RE = re.compile(
    r"^(\d+\.\d+)\s+"
    r"(?:\S+\s+(?:Out|In)\s+)?"  # 可选: wlan0 Out / wlan0 In (仅在 -i any 时出现)
    r"IP\s+"
    r"(\S+)\s+>\s+(\S+):\s+"
    r"(.*?)"                        # 非贪婪匹配协议信息
    r"(?:,\s+length\s+|\s+)"       # 分隔: 直接空格 或 ", length "
    r"(\d+)$",                      # 包大小
    re.IGNORECASE,
)

# 带 flags 的格式（某些 tcpdump 版本在 -q 下仍会输出 flags）
TCPDUMP_FLAGS_RE = re.compile(
    r"^(\d+\.\d+)\s+"
    r"(?:\S+\s+(?:Out|In)\s+)?"
    r"IP\s+"
    r"(\S+)\s+>\s+(\S+):\s+"
    r"Flags\s+\[(\S+)\],.*"
    r"(\d+)",
    re.IGNORECASE,
)


def parse_tcpdump_line(line: str) -> Optional[Dict[str, Any]]:
    """
    解析 tcpdump 单行文本，返回结构化字典。
    """
    line = line.strip()
    if not line or line.startswith("tcpdump:"):
        return None

    m = TCPDUMP_LINE_RE.match(line)
    if m:
        ts, src, dst, info, length = m.groups()
        # 从协议信息中提取协议名称（第一个单词），清理标点
        proto = info.split()[0] if info.split() else "unknown"
        proto = proto.strip(",").strip().upper()
        return {
            "timestamp": float(ts),
            "src": src,
            "dst": dst,
            "proto": proto,
            "length": int(length),
            "flags": "",
        }

    m = TCPDUMP_FLAGS_RE.match(line)
    if m:
        ts, src, dst, flags, length = m.groups()
        return {
            "timestamp": float(ts),
            "src": src,
            "dst": dst,
            "proto": "TCP",
            "length": int(length),
            "flags": flags,
        }

    return None


def extract_payload_meta(payload_lines: List[str]) -> Dict[str, Any]:
    """
    从 tcpdump -A 的 payload 文本行中提取协议元数据。
    与 collector/pcap_collector._inspect_payload 逻辑一致。
    """
    meta: Dict[str, Any] = {}
    text = "\n".join(payload_lines)

    # HTTP Host
    hosts = re.findall(r"[Hh]ost:\s*([^\r\n]+)", text)
    if hosts:
        meta["http_hosts"] = list(set(h.strip() for h in hosts))

    # HTTP Content-Type（用于区分 FLV/HLS/DASH）
    m_ct = re.search(r"[Cc]ontent-[Tt]ype:\s*([^\r\n]+)", text)
    if m_ct:
        ct = m_ct.group(1).strip().lower()
        meta["content_type"] = ct
        if "flv" in ct:
            meta["protocol"] = "FLV"
        elif "mpegurl" in ct or "m3u8" in ct:
            meta["protocol"] = "HLS"
        elif "mpd" in ct or "dash" in ct:
            meta["protocol"] = "DASH"

    # HTTP 状态码
    m_status = re.search(r"HTTP/\d\.\d\s+(\d{3})", text)
    if m_status:
        meta["http_status"] = int(m_status.group(1))

    # HLS M3U8（文本特征）
    bw = re.search(r"#EXT-X-STREAM-INF:.*BANDWIDTH=(\d+)", text)
    res = re.search(r"#EXT-X-STREAM-INF:.*RESOLUTION=(\d+x\d+)", text)
    fps = re.search(r"#EXT-X-STREAM-INF:.*FRAME-RATE=([\d.]+)", text)
    if bw or res:
        meta["protocol"] = "HLS"
        if bw:
            meta["hls_bandwidth"] = int(bw.group(1))
        if res:
            meta["hls_resolution"] = res.group(1)
        if fps:
            meta["hls_framerate"] = float(fps.group(1))

    # DASH MPD
    if "<MPD" in text or "<Representation" in text:
        meta["protocol"] = "DASH"
        bw2 = re.search(r'bandwidth="(\d+)"', text)
        res2 = re.search(r'width="(\d+)".*?height="(\d+)"', text)
        if bw2:
            meta["dash_bandwidth"] = int(bw2.group(1))
        if res2:
            meta["dash_resolution"] = f"{res2.group(1)}x{res2.group(2)}"

    # FLV
    if text.startswith("FLV") or "\x46\x4C\x56" in text[:10]:
        meta["protocol"] = "FLV"

    return meta
