"""
traffic/parser/netstats_parser.py —— dumpsys netstats 文本解析

兼容多种 Android 版本的输出格式。
"""

import re
from typing import Optional, Dict


def parse_netstats_line(line: str) -> Optional[Dict[str, int]]:
    """
    解析单行 netstats 文本，提取 rxBytes / txBytes / rxPackets / txPackets。

    支持的格式示例：
      st=16666 rb=1234567 rp=8901 tb=98765 tp=4321 op=0
      16666 1234567 8901 98765 4321 0
    """
    line = line.strip()
    if not line:
        return None

    # 键值对格式
    if "rb=" in line:
        result: Dict[str, int] = {}
        for key in ["rb", "tb", "rp", "tp"]:
            m = re.search(rf"\b{key}=(\d+)", line)
            if m:
                full_key = {"rb": "rxBytes", "tb": "txBytes", "rp": "rxPackets", "tp": "txPackets"}[key]
                result[full_key] = int(m.group(1))
        return result if result else None

    # 纯数字格式（按空白分隔）
    parts = line.split()
    if len(parts) >= 6 and all(p.isdigit() for p in parts[:6]):
        try:
            return {
                "ifaceIdx": int(parts[0]),
                "rxBytes": int(parts[1]),
                "rxPackets": int(parts[2]),
                "txBytes": int(parts[3]),
                "txPackets": int(parts[4]),
                "operations": int(parts[5]),
            }
        except Exception:
            pass

    return None
