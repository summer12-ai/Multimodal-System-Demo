"""
traffic/collector/pcap_collector.py —— 包级采集器（基于 tcpdump 文本输出）

设计决策：
- 不依赖 scapy（避免 Windows npcap 安装问题），直接解析 tcpdump -nn -tt -q -l 文本输出。
- 通过 /proc/<pid>/net/tcp 动态获取 foreign IP，构建 tcpdump host filter。
- 同时以 -s 256 -A 模式捕获 payload 前 256 字节，供协议嗅探。
"""

import re
import subprocess
import threading
import time
from typing import Dict, Any, List, Optional, Callable, Deque
from collections import deque

from ..models import PacketMeta
from ..config import PCAP_IP_REFRESH_INTERVAL


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
# 1777391896.194391 wlan0 Out IP 192.168.1.2.54321 > 104.18.1.1.443: Flags [P.], seq ..., length 1428
TCPDUMP_FLAGS_RE = re.compile(
    r"^(\d+\.\d+)\s+"
    r"(?:\S+\s+(?:Out|In)\s+)?"
    r"IP\s+"
    r"(\S+)\s+>\s+(\S+):\s+"
    r"Flags\s+\[(\S+)\],.*"
    r"(\d+)",
    re.IGNORECASE,
)

# 辅助：过滤非 payload 的 summary 行
TCPDUMP_SUMMARY_RE = re.compile(
    r"^(\d+\s+packets captured|"
    r"\d+\s+packets received by filter|"
    r"\d+\s+packets dropped by kernel|"
    r"listening on)",
    re.IGNORECASE,
)

# payload 行检测（-A 输出中的 HTTP/TLS 特征）
HTTP_HOST_RE = re.compile(r"[Hh]ost:\s*([^\r\n]+)")
HLS_BANDWIDTH_RE = re.compile(r"#EXT-X-STREAM-INF:.*BANDWIDTH=(\d+)")
HLS_RESOLUTION_RE = re.compile(r"#EXT-X-STREAM-INF:.*RESOLUTION=(\d+x\d+)")
HLS_FRAMERATE_RE = re.compile(r"#EXT-X-STREAM-INF:.*FRAME-RATE=([\d.]+)")


class PcapCollector:
    """
    启动 adb shell tcpdump，实时解析文本输出生成 PacketMeta，
    并从 payload 中嗅探视频协议元数据。
    """

    def __init__(
        self,
        pid_provider: Callable[[], Optional[str]],
        device_id: Optional[str] = None,
        snaplen: int = 512,
        bpf_extra: str = "",
    ):
        self.pid_provider = pid_provider
        self.device_id = device_id
        self.snaplen = snaplen
        self.bpf_extra = bpf_extra

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None

        self._lock = threading.Lock()
        self._latest_protocol_meta: Dict[str, Any] = {}
        self._latest_protocol_meta_ts: float = 0.0
        self._packet_history: Deque[PacketMeta] = deque(maxlen=200)
        self._foreign_ips: List[str] = []
        self._last_ip_refresh: float = 0.0
        self.error_count: int = 0
        self._diag_messages: Deque[str] = deque(maxlen=20)

        # root / tcpdump 路径缓存
        self._adb_is_root: Optional[bool] = None
        self._tcpdump_path: Optional[str] = None

    # ----------------------- ADB / tcpdump 命令构建 -----------------------

    def _adb_base(self) -> List[str]:
        cmd = ["adb"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        return cmd

    def _refresh_foreign_ips(self):
        """根据 PID 读取 /proc/<pid>/net/tcp 和 /udp 提取 foreign IP"""
        pid = self.pid_provider()
        if not pid:
            return
        now = time.time()
        if (now - self._last_ip_refresh) < PCAP_IP_REFRESH_INTERVAL:
            return

        ips: set = set()
        for proto in ["tcp", "udp"]:
            cmd = self._adb_base() + ["shell", "cat", f"/proc/{pid}/net/{proto}"]
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=5,
                )
                if res.returncode != 0:
                    continue
            except Exception:
                continue

            for line in res.stdout.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                # 第二列是 local address，第三列是 rem address，格式: 0101A8C0:01BB
                rem = parts[2]
                if ":" in rem:
                    hex_ip, _ = rem.split(":")
                    try:
                        ip_int = int(hex_ip, 16)
                        # 跳过 0.0.0.0 和 127.x.x.x
                        if ip_int == 0 or (ip_int & 0xFF000000) == 0x7F000000:
                            continue
                        # /proc/net/tcp 中的 IP 是网络字节序（大端序）
                        ip_str = "{}.{}.{}.{}".format(
                            (ip_int >> 24) & 0xFF,
                            (ip_int >> 16) & 0xFF,
                            (ip_int >> 8) & 0xFF,
                            (ip_int >> 0) & 0xFF,
                        )
                        ips.add(ip_str)
                    except Exception:
                        continue

        self._foreign_ips = sorted(ips)
        self._last_ip_refresh = now

    def _is_adb_root(self) -> bool:
        """检测 adb shell 是否已经是 root 用户（无需 su），结果缓存"""
        if self._adb_is_root is not None:
            return self._adb_is_root
        try:
            res = subprocess.run(
                self._adb_base() + ["shell", "whoami"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=3,
            )
            self._adb_is_root = res.returncode == 0 and res.stdout.strip().lower() == "root"
        except Exception:
            self._adb_is_root = False
        return self._adb_is_root

    def _ensure_adb_root(self) -> bool:
        """尝试 adb root 重启 adbd 为 root 模式"""
        if self._is_adb_root():
            return True
        try:
            print("[PcapCollector] 尝试 adb root 提权...")
            res = subprocess.run(
                self._adb_base() + ["root"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
            )
            if res.returncode == 0:
                # 等待 adbd 重启（某些设备需要更长时间）
                time.sleep(3.0)
                self._adb_is_root = None  # 清空缓存重新检测
                if self._is_adb_root():
                    print("[PcapCollector] adb root 成功")
                    return True
            print(f"[PcapCollector] adb root 失败: {res.stderr or res.stdout}")
        except Exception as e:
            print(f"[PcapCollector] adb root 异常: {e}")
        return False

    def _resolve_tcpdump_path(self) -> Optional[str]:
        """探测设备上 tcpdump 的实际路径"""
        if self._tcpdump_path is not None:
            return self._tcpdump_path
        candidates = ["tcpdump", "/system/bin/tcpdump", "/system/xbin/tcpdump", "/data/local/tmp/tcpdump"]
        for cand in candidates:
            try:
                # 使用 test -x 检测可执行性，兼容所有 Android shell
                res = subprocess.run(
                    self._adb_base() + ["shell", f"test -x {cand} && echo ok"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=3,
                )
                if "ok" in res.stdout:
                    self._tcpdump_path = cand
                    return cand
            except Exception:
                continue
        print("[PcapCollector] 设备上未找到 tcpdump，pcap 采集将不可用")
        self._tcpdump_path = ""
        return None

    def _detect_interface(self) -> str:
        """检测设备上可用的主网络接口（优先 wlan0），失败返回 any"""
        try:
            res = subprocess.run(
                self._adb_base() + ["shell", "su -c 'ip link show'"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=3,
            )
            for line in res.stdout.splitlines():
                line = line.strip()
                # 只匹配接口主行（以数字开头），跳过 link/ether 等详情行
                if not line or not line[0].isdigit():
                    continue
                # 匹配形如 "27: wlan0: <BROADCAST..." 的行
                if "wlan" in line.lower() or "eth" in line.lower():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        iface = parts[1].strip().split()[0]
                        if iface and iface.isalnum():
                            return iface
        except Exception:
            pass
        return "any"

    def _build_cmd(self) -> List[str]:
        # 注意：不再依赖 foreign_ips 构建 host filter。
        # 实际测试表明 P2P/视频流量 foreign IP 动态变化，静态 host filter 会遗漏大量包。
        # 改为抓取接口全部流量，在 Python 层按协议特征过滤。
        self._refresh_foreign_ips()

        tcpdump_bin = self._resolve_tcpdump_path()
        if not tcpdump_bin:
            return []

        # 自动检测最佳接口（优先 wlan0，避免 -i any 的混杂模式警告）
        iface = self._detect_interface()

        # 构建 tcpdump 命令字符串
        parts = [tcpdump_bin, "-i", iface, "-nn", "-tt", "-q", "-l",
                 "-s", str(self.snaplen), "-A"]

        # BPF filter：仅保留用户自定义 bpf_extra，不再自动添加 host filter
        if self.bpf_extra:
            parts.append(self.bpf_extra)

        cmd_str = " ".join(parts)

        # 若 adb shell 本身已是 root，直接执行；否则通过 su 提权
        if self._is_adb_root():
            return self._adb_base() + ["shell", cmd_str]
        else:
            # 使用单字符串包裹，确保 su -c 正确接收完整命令
            return self._adb_base() + ["shell", f"su -c '{cmd_str}'"]

    # ----------------------- 解析逻辑 -----------------------

    @staticmethod
    def _parse_line(line: str) -> Optional[PacketMeta]:
        line = line.strip()
        if not line or line.startswith("tcpdump:"):
            return None
        if TCPDUMP_SUMMARY_RE.match(line):
            return None

        # 尝试标准格式
        m = TCPDUMP_LINE_RE.match(line)
        if m:
            ts, src, dst, info, length = m.groups()
            # 从协议信息中提取协议名称（第一个单词），清理标点
            proto = info.split()[0] if info.split() else "unknown"
            proto = proto.strip(",").strip().upper()
            return PcapCollector._build_meta(ts, src, dst, proto, length, "")

        # 尝试 flags 格式
        m = TCPDUMP_FLAGS_RE.match(line)
        if m:
            ts, src, dst, flags, length = m.groups()
            return PcapCollector._build_meta(ts, src, dst, "tcp", length, flags)

        return None

    @staticmethod
    def _build_meta(ts: str, src: str, dst: str, proto: str, length: str, flags: str) -> Optional[PacketMeta]:
        try:
            timestamp = float(ts)
            pkt_len = int(length)
        except Exception:
            return None

        src_ip, src_port = PcapCollector._split_ip_port(src)
        dst_ip, dst_port = PcapCollector._split_ip_port(dst)
        if src_ip is None or dst_ip is None:
            return None

        return PacketMeta(
            timestamp=timestamp,
            src_ip=src_ip,
            src_port=src_port or 0,
            dst_ip=dst_ip,
            dst_port=dst_port or 0,
            proto=proto.upper(),
            length=pkt_len,
            flags=flags,
        )

    @staticmethod
    def _split_ip_port(addr: str):
        """解析 192.168.1.1:443 或 [2001:db8::1]:443"""
        if addr.startswith("["):
            # IPv6
            m = re.match(r"\[([^]]+)\]:(\d+)", addr)
            if m:
                return m.group(1), int(m.group(2))
            return addr, 0
        if ":" in addr:
            # IPv4，取最后一个冒号作为端口分隔
            ip, port = addr.rsplit(":", 1)
            try:
                return ip, int(port)
            except Exception:
                return addr, 0
        return addr, 0

    def _inspect_payload(self, lines: List[str]) -> Dict[str, Any]:
        """从 payload 文本行中嗅探协议元数据"""
        meta: Dict[str, Any] = {}
        text = "\n".join(lines)

        # HTTP Host
        hosts = HTTP_HOST_RE.findall(text)
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
        bw = HLS_BANDWIDTH_RE.search(text)
        res = HLS_RESOLUTION_RE.search(text)
        fps = HLS_FRAMERATE_RE.search(text)
        if bw or res:
            meta["protocol"] = "HLS"
            if bw:
                meta["hls_bandwidth"] = int(bw.group(1))
            if res:
                meta["hls_resolution"] = res.group(1)
            if fps:
                meta["hls_framerate"] = float(fps.group(1))

        # DASH MPD（简单检测）
        if "<MPD" in text or "<Representation" in text:
            meta["protocol"] = "DASH"
            bw2 = re.search(r'bandwidth="(\d+)"', text)
            res2 = re.search(r'width="(\d+)".*?height="(\d+)"', text)
            if bw2:
                meta["dash_bandwidth"] = int(bw2.group(1))
            if res2:
                meta["dash_resolution"] = f"{res2.group(1)}x{res2.group(2)}"

        # FLV 头（二进制特征）
        if text.startswith("FLV") or "\x46\x4C\x56" in text[:10]:
            meta["protocol"] = "FLV"

        return meta

    # ----------------------- tcpdump 读取循环 -----------------------

    def _stderr_reader(self):
        """读取 tcpdump stderr，用于诊断启动失败原因"""
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            for line in self._proc.stderr:
                if self._stop_event.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                # 过滤已知无害的警告，避免污染控制台
                if "promiscuous mode" in line.lower():
                    continue
                with self._lock:
                    self._diag_messages.append(line)
                # 打印到控制台以便实时诊断
                print(f"[tcpdump stderr] {line}")
                # 关键错误词
                if any(k in line.lower() for k in ("not found", "permission", "unable", "denied", "invalid")):
                    with self._lock:
                        self.error_count += 1
        except Exception:
            pass

    def _run(self):
        cmd = self._build_cmd()
        if not cmd:
            print("[PcapCollector] 命令构建失败，tcpdump 不可用，pcap 线程退出")
            return
        print(f"[PcapCollector] 启动抓包: {' '.join(cmd[-2:])}")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
            )
            assert self._proc.stdout is not None

            # 启动 stderr 读取线程用于诊断
            stderr_thread = threading.Thread(target=self._stderr_reader, daemon=True)
            stderr_thread.start()

            pending_payload_lines: List[str] = []
            last_pkt: Optional[PacketMeta] = None

            for raw_line in self._proc.stdout:
                if self._stop_event.is_set():
                    break
                line = raw_line.rstrip("\n")

                pkt = self._parse_line(line)
                if pkt is not None:
                    # 上一个包的 payload 处理
                    if last_pkt is not None and pending_payload_lines:
                        pmeta = self._inspect_payload(pending_payload_lines)
                        if pmeta:
                            last_pkt.payload_preview = "\n".join(pending_payload_lines)[:256]
                            with self._lock:
                                self._latest_protocol_meta.update(pmeta)
                                self._latest_protocol_meta_ts = time.time()

                    last_pkt = pkt
                    pending_payload_lines = []
                    with self._lock:
                        self._packet_history.append(pkt)
                else:
                    # 可能是 payload 行
                    if line and not line.startswith("tcpdump:") and not TCPDUMP_SUMMARY_RE.match(line):
                        pending_payload_lines.append(line)

        except Exception as e:
            with self._lock:
                self.error_count += 1
        finally:
            self._stop_event.set()
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass

    # ----------------------- 生命周期 -----------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        # 尝试 adb root 提权（若尚未 root）
        self._ensure_adb_root()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if (
            self._thread
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=2.0)

    def get_latest_protocol_meta(self) -> Dict[str, Any]:
        with self._lock:
            # 协议元数据超过 10 秒自动过期，避免旧数据残留导致误判
            if time.time() - self._latest_protocol_meta_ts > 10.0:
                return {}
            return dict(self._latest_protocol_meta)

    def get_packet_stats(self) -> Dict[str, Any]:
        with self._lock:
            if not self._packet_history:
                return {"packets": 0, "total_bytes": 0, "tcp_flags": {}, "proto_counts": {}}
            total_bytes = sum(p.length for p in self._packet_history)
            flag_counter: Dict[str, int] = {}
            proto_counter: Dict[str, int] = {}
            for p in self._packet_history:
                if p.flags:
                    flag_counter[p.flags] = flag_counter.get(p.flags, 0) + 1
                if p.proto:
                    proto_counter[p.proto] = proto_counter.get(p.proto, 0) + 1
            return {
                "packets": len(self._packet_history),
                "total_bytes": total_bytes,
                "tcp_flags": flag_counter,
                "proto_counts": proto_counter,
            }
