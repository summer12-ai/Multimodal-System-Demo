"""
traffic/collector/stats_collector.py —— 内核级网络统计采集

采集来源（均无需额外安装，依赖 adb + root）：
1. /proc/uid_stat/<uid>/tcp_rcv tcp_snd  （部分系统存在）
2. dumpsys netstats --uid <uid>          （Android 6+）
3. /proc/<pid>/net/tcp 与 /proc/<pid>/net/tcp6  （socket 四元组）
4. dumpsys wifi | grep RSSI              （信号强度）
"""

import re
import subprocess
import threading
import time
from typing import Dict, Any, List, Optional, Set

from ..models import NetStatsEntry
from ..config import STATS_SAMPLE_INTERVAL, PID_REFRESH_INTERVAL


class StatsCollector:
    """
    通过周期性 adb shell 命令采集目标 UID/PID 的网络统计。
    """

    def __init__(
        self,
        uid: int,
        package_name: str,
        device_id: Optional[str] = None,
    ):
        self.uid = uid
        self.package_name = package_name
        self.device_id = device_id

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 累计值（用于计算 delta）
        self._last_rx: int = 0
        self._last_tx: int = 0
        self._last_ts: float = 0.0

        # 最新结果
        self._latest: Optional[NetStatsEntry] = None
        self.sample_count: int = 0
        self.error_count: int = 0

        # PID 缓存
        self._cached_pid: Optional[str] = None
        self._last_pid_refresh: float = 0.0

    # ----------------------- ADB 基础命令 -----------------------

    def _adb_base(self) -> List[str]:
        cmd = ["adb"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        return cmd

    def _shell(self, cmd_args: List[str], timeout: int = 5) -> str:
        full = self._adb_base() + ["shell"] + cmd_args
        try:
            res = subprocess.run(
                full,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
            )
            if res.returncode != 0:
                return ""
            return res.stdout
        except Exception:
            return ""

    # ----------------------- PID 管理 -----------------------

    def _refresh_pid(self) -> Optional[str]:
        stdout = self._shell(["pidof", self.package_name])
        pid = stdout.strip().split()[0] if stdout.strip() else None
        if pid and pid.isdigit():
            self._cached_pid = pid
            self._last_pid_refresh = time.time()
            return pid
        return self._cached_pid

    def _ensure_pid_fresh(self):
        if (time.time() - self._last_pid_refresh) >= PID_REFRESH_INTERVAL:
            self._refresh_pid()

    # ----------------------- 数据源 -----------------------

    def _read_uid_stat(self) -> tuple:
        """
        尝试读取 /proc/uid_stat/<uid>/tcp_rcv 和 tcp_snd
        返回 (rx_bytes, tx_bytes)，失败返回 (-1, -1)
        """
        rx = self._shell(["cat", f"/proc/uid_stat/{self.uid}/tcp_rcv"])
        tx = self._shell(["cat", f"/proc/uid_stat/{self.uid}/tcp_snd"])
        try:
            return int(rx.strip()), int(tx.strip())
        except Exception:
            return -1, -1

    def _read_netstats(self) -> tuple:
        """
        解析 dumpsys netstats --uid <uid>
        返回 (rx_bytes, tx_bytes)，失败返回 (-1, -1)
        兼容带 --tag 和不带 --tag 的 Android 版本。
        """
        for tag_opt in [["--uid", str(self.uid), "--tag"], ["--uid", str(self.uid)]]:
            stdout = self._shell(["dumpsys", "netstats"] + tag_opt)
            if not stdout:
                continue
            total_rx = 0
            total_tx = 0
            # 示例行: st=16666 rb=1234567 rp=8901 tb=98765 tp=4321 op=0
            for line in stdout.splitlines():
                line = line.strip()
                if "rb=" in line and "tb=" in line:
                    m_rx = re.search(r"rb=(\d+)", line)
                    m_tx = re.search(r"tb=(\d+)", line)
                    if m_rx and m_tx:
                        total_rx += int(m_rx.group(1))
                        total_tx += int(m_tx.group(1))
            if total_rx > 0 or total_tx > 0:
                return total_rx, total_tx
        return -1, -1

    def _read_proc_pid_net(self) -> tuple:
        """
        读取 /proc/<pid>/net/tcp 和 /udp 中的 socket 数量（近似活跃连接数）。
        TCP 统计 ESTABLISHED 连接；UDP 统计所有条目（无状态，均视为活跃）。
        返回 (active_connections, 不用的占位)
        """
        pid = self._cached_pid
        if not pid:
            return 0, 0
        active = 0
        for proto in ["tcp", "udp"]:
            stdout = self._shell(["cat", f"/proc/{pid}/net/{proto}"])
            if not stdout:
                continue
            for line in stdout.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                try:
                    if proto == "tcp" and parts[3] == "01":
                        active += 1
                    elif proto == "udp":
                        active += 1
                except Exception:
                    continue
        return active, 0

    def _read_wifi_rssi(self) -> tuple:
        """返回 (rssi_dbm, link_speed_mbps)"""
        stdout = self._shell(["dumpsys", "wifi"])
        if not stdout:
            return None, None
        rssi = None
        speed = None
        # RSSI: -55 dBm  或  rssi=-55
        m_rssi = re.search(r"[Rr][Ss][Ss][Ii][:=-]\s*(-?\d+)", stdout)
        if m_rssi:
            rssi = int(m_rssi.group(1))
        # Link speed:  或  tx=866  rx=866
        m_speed = re.search(r"[Ll]ink\s*[Ss]peed[:=]\s*(\d+)", stdout)
        if not m_speed:
            m_speed = re.search(r"[Tt][Xx][:=-]\s*(\d+)", stdout)
        if m_speed:
            speed = int(m_speed.group(1))
        return rssi, speed

    # ----------------------- 主采样逻辑 -----------------------

    def _sample_once(self) -> Optional[NetStatsEntry]:
        self._ensure_pid_fresh()
        now = time.time()

        # 1. 读取累计流量（优先 netstats，/proc/uid_stat/ 已废弃）
        rx_total, tx_total = self._read_netstats()
        if rx_total < 0:
            rx_total, tx_total = self._read_uid_stat()
        if rx_total < 0:
            return None

        # 2. 读取活跃连接数
        active_conn, _ = self._read_proc_pid_net()

        # 3. 读取 WiFi 信号
        rssi, speed = self._read_wifi_rssi()

        # 4. 计算 delta 和速率
        rx_delta = 0
        tx_delta = 0
        rx_rate = 0.0
        tx_rate = 0.0
        if self._last_ts > 0:
            dt = now - self._last_ts
            if dt > 0:
                rx_delta = max(0, rx_total - self._last_rx)
                tx_delta = max(0, tx_total - self._last_tx)
                rx_rate = rx_delta / dt
                tx_rate = tx_delta / dt

        # 5. 更新累计值
        self._last_rx = rx_total
        self._last_tx = tx_total
        self._last_ts = now

        entry = NetStatsEntry(
            timestamp=now,
            uid=self.uid,
            pid=self._cached_pid or "",
            rx_bytes=rx_total,
            tx_bytes=tx_total,
            rx_bytes_delta=rx_delta,
            tx_bytes_delta=tx_delta,
            rx_rate=rx_rate,
            tx_rate=tx_rate,
            active_connections=active_conn,
            rssi=rssi,
            link_speed=speed,
        )

        with self._lock:
            self._latest = entry
            self.sample_count += 1
        return entry

    def _run_loop(self):
        while self._running:
            try:
                self._sample_once()
            except Exception:
                with self._lock:
                    self.error_count += 1
            time.sleep(STATS_SAMPLE_INTERVAL)

    # ----------------------- 生命周期 -----------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._refresh_pid()
        # 预热一次累计值
        self._sample_once()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def get_latest(self) -> Dict[str, Any]:
        with self._lock:
            if self._latest is None:
                return {
                    "timestamp": time.time(),
                    "uid": self.uid,
                    "pid": self._cached_pid or "",
                    "rx_bytes": 0,
                    "tx_bytes": 0,
                    "rx_rate": 0.0,
                    "tx_rate": 0.0,
                    "active_connections": 0,
                    "rssi": None,
                    "link_speed": None,
                }
            e = self._latest
            return {
                "timestamp": e.timestamp,
                "uid": e.uid,
                "pid": e.pid,
                "rx_bytes": e.rx_bytes,
                "tx_bytes": e.tx_bytes,
                "rx_rate": e.rx_rate,
                "tx_rate": e.tx_rate,
                "active_connections": e.active_connections,
                "rssi": e.rssi,
                "link_speed": e.link_speed,
            }
