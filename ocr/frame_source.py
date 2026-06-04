"""
frame_source.py —— 截图源抽象接口与可插拔后端实现

设计目标：
- 将「截图采集」与「OCR 识别」彻底解耦，使两者可独立演进。
- 通过 FrameSource 抽象基类，未来可从 adb exec-out 无缝切换到 scrcpy / minicap
  等视频流方案，上层 OcrModuleService 零改动。

当前实现：
- ADBFrameSource：基于 adb exec-out screencap -p，兼容性好，上限约 5fps。

未来扩展（已预留骨架，见文件末尾注释）：
- ScrcpyFrameSource：基于 scrcpy-server H.264 视频流，可达 30-60fps，
  推荐作为高帧率（>10fps）场景的最终方案。
"""

import subprocess
import io
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from PIL import Image


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class FrameSource(ABC):
    """
    截图源抽象接口。所有后端（ADB / scrcpy / minicap）必须实现此类。
    """

    @abstractmethod
    def start(self) -> None:
        """初始化连接、校验设备、启动服务端等。"""
        pass

    @abstractmethod
    def get_frame(self) -> Optional[Tuple[float, Image.Image]]:
        """
        获取最新一帧。

        Returns
        -------
        (timestamp, pil_image) or None
            timestamp 为 float（time.time()），pil_image 为 RGB 模式 PIL.Image。
            采集失败时返回 None，调用方应做跳过处理。
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """清理资源、关闭连接、停止服务端等。"""
        pass

    @property
    @abstractmethod
    def max_fps(self) -> float:
        """该后端在理想条件下的最大帧率（用于生产者线程睡眠补偿）。"""
        pass


# ---------------------------------------------------------------------------
# 当前默认实现：ADB exec-out
# ---------------------------------------------------------------------------

class ADBFrameSource(FrameSource):
    """
    基于 ``adb exec-out screencap -p`` 的截图源。

    特点：
    - 零外部依赖，纯 ADB 协议，兼容性最好。
    - 单次截图+传输耗时约 50-150ms，理论上限约 5-8fps（实际稳定 1-3fps）。
    - 适合当前 1fps OCR 需求，无需额外部署。

    参数
    ----
    device_id : str | None
        ADB 设备序列号；None 时使用默认单设备。
    capture_interval : float
        两次截图之间的目标间隔（秒），默认 1.0。
    """

    def __init__(self, device_id: Optional[str] = None, capture_interval: float = 1.0):
        self.device_id = device_id
        self.capture_interval = capture_interval
        self.base_cmd = ["adb"]
        if device_id:
            self.base_cmd += ["-s", device_id]
        self._screen_size: Optional[Tuple[int, int]] = None

    # ----------------------- FrameSource 接口实现 -----------------------

    def start(self) -> None:
        """检查 ADB 连接是否正常；异常时抛出 RuntimeError。"""
        if not self._check_connection():
            raise RuntimeError("ADB connection failed")

    def get_frame(self) -> Optional[Tuple[float, Image.Image]]:
        """获取一帧截图；失败返回 None。"""
        img = self._screenshot_exec_out()
        if img is not None:
            return time.time(), img
        # 降级方案
        img = self._screenshot_shell_pull()
        if img is not None:
            return time.time(), img
        return None

    def stop(self) -> None:
        """ADB 方案无持续连接，无需额外清理。"""
        pass

    @property
    def max_fps(self) -> float:
        # exec-out 物理上限约 5fps；这里按用户配置返回，防止过度空转
        return 1.0 / max(self.capture_interval, 0.2)

    # ----------------------- 内部方法 -----------------------

    def _check_connection(self) -> bool:
        """校验 ADB 是否可用且至少一台设备在线。"""
        try:
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True, text=True, encoding="utf-8",
                errors="ignore", timeout=10,
            )
            if result.returncode != 0:
                return False
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            devices = [l for l in lines[1:] if l.endswith("device")]
            if not devices:
                return False
            if self.device_id is None and len(devices) > 1:
                print("[ADBFrameSource] 检测到多台设备，请使用 device_id 参数指定")
                return False
            print(f"[ADBFrameSource] 设备连接正常: {devices[0].split()[0]}")
            return True
        except FileNotFoundError:
            print("[ADBFrameSource] 未找到 adb 命令，请安装 Android SDK Platform-Tools")
            return False
        except Exception as e:
            print(f"[ADBFrameSource] 检查连接异常: {e}")
            return False

    def _screenshot_exec_out(self) -> Optional[Image.Image]:
        cmd = self.base_cmd + ["exec-out", "screencap", "-p"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode != 0:
                return None
            data = result.stdout
            if not data.startswith(b"\x89PNG"):
                return None
            return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            return None

    def _screenshot_shell_pull(self) -> Optional[Image.Image]:
        import tempfile
        import os

        remote_path = "/sdcard/_auto_ocr_temp.png"
        local_path = os.path.join(tempfile.gettempdir(), "_auto_ocr_temp.png")
        cmd_shell = self.base_cmd + ["shell", "screencap", "-p", remote_path]
        cmd_pull = self.base_cmd + ["pull", remote_path, local_path]
        cmd_rm = self.base_cmd + ["shell", "rm", "-f", remote_path]
        try:
            r1 = subprocess.run(cmd_shell, capture_output=True, timeout=10)
            if r1.returncode != 0:
                return None
            r2 = subprocess.run(cmd_pull, capture_output=True, timeout=10)
            if r2.returncode != 0:
                return None
            return Image.open(local_path).convert("RGB")
        except Exception:
            return None
        finally:
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                pass
            try:
                subprocess.run(cmd_rm, capture_output=True, timeout=5)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 未来扩展：ScrcpyFrameSource 骨架（已注释，需要时取消注释并实现）
# ---------------------------------------------------------------------------
"""
import queue
import threading

class ScrcpyFrameSource(FrameSource):
    \"\"\"
    基于 scrcpy-server H.264 视频流的高帧率截图源。

    特点：
    - 利用手机 MediaCodec 硬件编码，CPU 占用极低。
    - PC 端通过 socket 接收 H.264，ffmpeg / av 解码为 PIL.Image。
    - 稳定支持 10-60fps，推荐作为高帧率连续帧采集方案。

    切换方式（只需在 core/orchestrator.py 中替换一行）：
        from ocr.frame_source import ScrcpyFrameSource
        frame_source = ScrcpyFrameSource(device_id=cfg.device_id, max_fps=30)
        self.ocr_service = OcrModuleService(
            target_app=cfg.target_app,
            frame_source=frame_source,
        )

    实现 TODO：
    1. push scrcpy-server.jar 到设备
    2. adb forward tcp:27183 localabstract:scrcpy
    3. 通过 app_process 启动服务端
    4. socket 接收 H.264 nal units
    5. PyAV / cv2.VideoCapture 解码为 numpy → PIL.Image
    6. get_frame() 从解码队列中取出最新帧
    \"\"\"

    def __init__(self, device_id: Optional[str] = None, max_fps: int = 30):
        self.device_id = device_id
        self._target_fps = max_fps
        self._running = False
        self._frame_queue: "queue.Queue[Tuple[float, Image.Image]]" = queue.Queue(maxsize=3)
        self._decoder_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # TODO: push jar, forward port, start server, init decoder
        raise NotImplementedError("ScrcpyFrameSource 尚未实现，请按 README 高帧率指南完成")

    def get_frame(self) -> Optional[Tuple[float, Image.Image]]:
        try:
            return self._frame_queue.get_nowait()
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._running = False
        # TODO: close socket, cleanup decoder

    @property
    def max_fps(self) -> float:
        return float(self._target_fps)
"""
