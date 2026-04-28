"""
parser.py —— logcat 行解析器
"""

import re
from datetime import datetime
from .models import LogEvent


# threadtime 示例：
# 04-22 21:10:03.123  1234  5678 I MediaCodec: message...
THREADTIME_RE = re.compile(
    r"^(?P<md>\d{2}-\d{2})\s+"
    r"(?P<hms>\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<pid>\d+)\s+"
    r"(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEF])\s+"
    r"(?P<tag>[^:]+):\s*"
    r"(?P<msg>.*)$"
)


def parse_line(line: str) -> LogEvent:
    line = (line or "").strip()
    m = THREADTIME_RE.match(line)
    if not m:
        return LogEvent(
            timestamp=datetime.now().strftime("%m-%d %H:%M:%S.%f")[:-3],
            level="U",
            tag="UNPARSED",
            pid="",
            tid="",
            message=line,
        )
    return LogEvent(
        timestamp=f"{m.group('md')} {m.group('hms')}",
        level=m.group("level"),
        tag=m.group("tag").strip(),
        pid=m.group("pid"),
        tid=m.group("tid"),
        message=m.group("msg"),
    )
