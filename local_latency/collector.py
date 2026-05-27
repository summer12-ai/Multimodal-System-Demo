"""
collector.py —— 本地时延数据采集器

非侵入式执行 ADB 命令获取：
  1. dumpsys SurfaceFlinger --latency <surface>
  2. dumpsys gfxinfo <pkg> framestats
  3. （可选）perfetto FrameTimeline（Android 12+）
"""

import subprocess
import re
import time
from typing import List, Optional, Tuple


class LocalLatencyCollector:
    """
    基于 ADB 的本地帧时序数据采集器。
    """

    def __init__(self, package_name: str, device_id: Optional[str] = None):
        self.package_name = package_name
        self.device_id = device_id
        self._cached_surface_name: Optional[str] = None
        self._surface_cache_ttl: float = 30.0
        self._surface_cached_at: float = 0.0

    # ----------------------- ADB 封装 -----------------------

    def _adb_cmd(self, shell_args: List[str], timeout: int = 10) -> str:
        cmd = ["adb"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        cmd += ["shell"] + shell_args
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
            )
            return res.stdout
        except Exception:
            return ""

    # ----------------------- Surface 名称解析 -----------------------

    def resolve_surface_name(self) -> Optional[str]:
        """
        通过 dumpsys SurfaceFlinger --list 查找目标包名对应的 Surface。
        兼容新旧 Android 格式：
          旧: SurfaceView[pkg/activity]#0
          新: RequestedLayerState{hash SurfaceView[pkg/activity]#123 parentId=...}
        """
        import re
        now = time.time()
        if self._cached_surface_name and (now - self._surface_cached_at) < self._surface_cache_ttl:
            return self._cached_surface_name

        stdout = self._adb_cmd(["dumpsys", "SurfaceFlinger", "--list"])
        # 优先匹配带 SurfaceView 且包含包名的行
        candidates = []
        for line in stdout.splitlines():
            line = line.strip()
            if self.package_name in line:
                candidates.append(line)

        # heuristic: 优先选 SurfaceView[...] 行，并提取纯 Surface 名称
        surface = None
        for c in candidates:
            if "SurfaceView[" in c and self.package_name in c:
                # 用正则提取 SurfaceView[...]#N 部分，去掉 RequestedLayerState{...} 前缀
                m = re.search(r"(SurfaceView\[[^\]]+\]#[0-9]+)", c)
                if m:
                    surface = m.group(1)
                    break
                # fallback: 若正则失败，直接去掉常见前缀
                if c.startswith("RequestedLayerState{"):
                    # 取第一个空格后到 } 之前的内容
                    inner = c[len("RequestedLayerState{"):]
                    if "}" in inner:
                        inner = inner[:inner.index("}")]
                    parts = inner.split()
                    if len(parts) >= 2:
                        surface = parts[1]  # hash 后的部分
                else:
                    surface = c
                break
        if not surface and candidates:
            surface = candidates[0]

        if surface:
            self._cached_surface_name = surface
            self._surface_cached_at = now
        return surface

    # ----------------------- SurfaceFlinger --latency -----------------------

    def collect_surfaceflinger_latency(self) -> Tuple[Optional[int], List[Tuple[int, int, int]]]:
        """
        采集 SurfaceFlinger 帧时序数据。
        
        Returns:
            (refresh_period_ns, list_of_(A, B, C))
        """
        surface = self.resolve_surface_name()
        if not surface:
            return None, []

        stdout = self._adb_cmd(
            ["dumpsys", "SurfaceFlinger", "--latency", surface],
            timeout=10,
        )
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        if not lines:
            return None, []

        # 第一行为 refresh_period
        try:
            refresh_period_ns = int(lines[0])
        except ValueError:
            return None, []

        records: List[Tuple[int, int, int]] = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 3:
                try:
                    a = int(parts[0])
                    b = int(parts[1])
                    c = int(parts[2])
                    # 过滤掉无效占位符（全 0 或极大异常值）
                    if a > 0 and b > 0 and c > 0 and c > a:
                        records.append((a, b, c))
                except ValueError:
                    continue

        return refresh_period_ns, records

    # ----------------------- gfxinfo framestats -----------------------

    def collect_gfxinfo_framestats(self) -> List[dict]:
        """
        采集 dumpsys gfxinfo <pkg> framestats 的 CSV 帧数据。
        兼容新旧 Android 格式：
          旧: ---PROFILEDATA--- ... ---PROFILEDATAEND---
          新: ---PROFILEDATA--- ... ---PROFILEDATA--- (第二个相同标记作为结束)
        
        Returns:
            每帧为一个 dict，键为 CSV 列名。
        """
        stdout = self._adb_cmd(
            ["dumpsys", "gfxinfo", self.package_name, "framestats"],
            timeout=10,
        )
        lines = stdout.splitlines()

        # 定位 CSV 数据区间
        profile_markers = [i for i, line in enumerate(lines) if "---PROFILEDATA---" in line]
        start_idx = -1
        end_idx = -1

        if len(profile_markers) >= 2:
            # 新格式：两个 ---PROFILEDATA--- 标记夹住 CSV
            start_idx = profile_markers[0] + 1
            end_idx = profile_markers[1]
        elif len(profile_markers) == 1:
            # 可能只有开始标记，尝试向后找 ---PROFILEDATAEND--- 或下一个 --- 开头行
            start_idx = profile_markers[0] + 1
            for i in range(start_idx, len(lines)):
                if "---PROFILEDATAEND---" in lines[i] or lines[i].strip().startswith("---"):
                    end_idx = i
                    break
            if end_idx < 0:
                # 取到文件末尾，但过滤掉后续的非 CSV 内容（如 View hierarchy）
                for i in range(start_idx, len(lines)):
                    stripped = lines[i].strip()
                    if stripped and not stripped[0].isdigit() and not stripped.startswith("Flags,"):
                        end_idx = i
                        break
                if end_idx < 0:
                    end_idx = len(lines)
        else:
            return []

        if start_idx < 0 or end_idx < 0 or end_idx <= start_idx:
            return []

        csv_lines = lines[start_idx:end_idx]
        if not csv_lines:
            return []

        # 找到 CSV header 行（以 Flags, 开头）
        header_idx = 0
        for i, line in enumerate(csv_lines):
            if line.strip().startswith("Flags,"):
                header_idx = i
                break

        header = [h.strip() for h in csv_lines[header_idx].split(",")]
        records = []
        for line in csv_lines[header_idx + 1:]:
            line = line.strip()
            if not line or not line[0].isdigit():
                continue
            vals = [v.strip() for v in line.split(",")]
            if len(vals) != len(header):
                continue
            row = dict(zip(header, vals))
            records.append(row)
        return records

    # ----------------------- 设备性能遥测（轻量） -----------------------

    def collect_cpu_gpu_mem(self) -> dict:
        """
        轻量级采集 CPU/GPU/内存指标，用于本地时延根因辅助标注。
        """
        result = {"cpu_usage": None, "gpu_busy": None, "mem_pss_mb": None}

        # CPU: top -n 1 -p <pid> 不太通用，改为 dumpsys cpuinfo 的整体占用
        cpu_out = self._adb_cmd(["dumpsys", "cpuinfo"], timeout=5)
        # 取第一行 Total 值，如 "Load: 2.5 / 3.1 / 2.8"
        for line in cpu_out.splitlines():
            if line.startswith("Load:"):
                nums = re.findall(r"[0-9]+\\.[0-9]+", line)
                if nums:
                    result["cpu_usage"] = float(nums[0])
                break

        # GPU: 尝试高通路径
        gpu_out = self._adb_cmd(
            ["cat", "/sys/class/kgsl/kgsl-3d0/gpu_busy_percentage"],
            timeout=3,
        ).strip()
        if gpu_out and gpu_out.replace("%", "").isdigit():
            result["gpu_busy"] = int(gpu_out.replace("%", ""))
        else:
            # 尝试联发科路径
            gpu_out2 = self._adb_cmd(
                ["cat", "/sys/class/misc/mali0/device/utilization"],
                timeout=3,
            ).strip()
            if gpu_out2 and gpu_out2.isdigit():
                result["gpu_busy"] = int(gpu_out2)

        # Memory: dumpsys meminfo <pkg>
        mem_out = self._adb_cmd(["dumpsys", "meminfo", self.package_name], timeout=5)
        for line in mem_out.splitlines():
            if "TOTAL PSS:" in line or "TOTAL" in line:
                nums = re.findall(r"[0-9,]+", line)
                if nums:
                    pss_str = nums[0].replace(",", "").replace(" ", "")
                    try:
                        result["mem_pss_mb"] = int(pss_str)
                    except ValueError:
                        pass
                    break
        return result
