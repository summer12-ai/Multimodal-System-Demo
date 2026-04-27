"""
collector.py —— adb logcat 持续采集器
"""

import subprocess
import threading
from typing import Callable, Optional


class LogcatCollector:
    """
    封装 adb logcat 子进程，持续读取日志流。
    """

    def __init__(self, on_line: Callable[[str], None], device_id: Optional[str] = None):
        self.on_line = on_line
        self.device_id = device_id
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None

    def _build_cmd(self):
        cmd = ["adb"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        cmd += ["logcat", "-v", "threadtime"]
        return cmd

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        cmd = self._build_cmd()
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
            for line in self._proc.stdout:
                if self._stop_event.is_set():
                    break
                self.on_line(line.rstrip("\n"))
        except Exception as e:
            self.on_line(f"LOGCAT_COLLECTOR_ERROR::{e}")
        finally:
            # _run 线程退出时只做进程清理，避免在当前线程里 join 自身
            self._stop_event.set()
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass

    def stop(self):
        self._stop_event.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        # 避免 join 当前线程导致 RuntimeError: cannot join current thread
        if (
            self._thread
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=1.0)
