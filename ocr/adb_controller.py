"""
adb_controller.py —— ADB 连接与截图控制模块

职责：
1. 检测 Android 设备是否通过 ADB 正常连接。
2. 提供高效截图接口，优先使用 exec-out 直接获取 PNG 字节流；
   若遇到 Windows 换行符问题，自动降级为 shell+pull 方案。
3. 获取屏幕分辨率（用于日志展示）。

异常处理：
- ADB 未安装 / 设备未连接：抛出 EnvironmentError，由主程序捕获后提示用户。
- 截图失败：返回 None，由主程序跳过当前帧。
"""

import subprocess
import io
from typing import Optional
from PIL import Image


class ADBController:
    """
    ADB 控制器，支持单设备自动识别或多设备显式指定。
    """

    def __init__(self, device_id: Optional[str] = None):
        """
        :param device_id: 设备序列号（adb devices 第一列）。若为 None，则使用默认单设备。
        """
        self.device_id = device_id
        self.base_cmd = ["adb"]
        if device_id:
            self.base_cmd += ["-s", device_id]
        self._screen_size: Optional[tuple] = None

    # ----------------------- 连接检测 -----------------------

    def check_connection(self) -> bool:
        """
        检查 ADB 是否可用且至少有一台设备处于 device 状态。

        :return: True 表示连接正常
        """
        try:
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10
            )
            if result.returncode != 0:
                print(f"[ADB] adb devices 执行失败: {result.stderr}")
                return False

            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            # 第一行为 "List of devices attached"
            devices = [line for line in lines[1:] if line.endswith("device")]
            if not devices:
                print("[ADB] 未检测到已连接的设备，请检查：")
                print("  1. 手机已开启 USB 调试（开发者选项）")
                print("  2. 数据线连接正常")
                print("  3. 驱动已正确安装")
                return False

            if self.device_id is None and len(devices) > 1:
                print("[ADB] 检测到多台设备，请使用 device_id 参数指定，或断开多余设备：")
                for d in devices:
                    print(f"    {d}")
                return False

            print(f"[ADB] 设备连接正常: {devices[0].split()[0]}")
            return True
        except FileNotFoundError:
            print("[ADB] 未找到 adb 命令，请安装 Android SDK Platform-Tools 并添加至系统 PATH")
            return False
        except subprocess.TimeoutExpired:
            print("[ADB] adb devices 执行超时")
            return False
        except Exception as e:
            print(f"[ADB] 检查连接时异常: {e}")
            return False

    # ----------------------- 截图 -----------------------

    def get_screenshot(self) -> Optional[Image.Image]:
        """
        获取手机屏幕截图。

        :return: PIL.Image 对象（RGB）；失败返回 None
        """
        img = self._screenshot_exec_out()
        if img is not None:
            return img
        # 降级方案
        return self._screenshot_shell_pull()

    def _screenshot_exec_out(self) -> Optional[Image.Image]:
        """
        优先方案：adb exec-out screencap -p
        在 Windows 下 subprocess.run(capture_output=True) 已能正确捕获原始二进制，
        无需再做 \\r\\n 替换（否则会破坏 PNG）。
        """
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
        """
        降级方案：先保存到手机 /sdcard，再 pull 到本地临时文件后读取。
        Windows 版 adb 不支持 pull 到 stdout（"-"），必须落到磁盘。
        """
        import tempfile
        import os

        remote_path = "/sdcard/_auto_ocr_temp.png"
        local_path = os.path.join(tempfile.gettempdir(), "_auto_ocr_temp.png")
        cmd_shell = self.base_cmd + ["shell", "screencap", "-p", remote_path]
        cmd_pull = self.base_cmd + ["pull", remote_path, local_path]
        cmd_rm_remote = self.base_cmd + ["shell", "rm", "-f", remote_path]
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
            # 清理本地临时文件
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                pass
            # 清理手机端临时文件，避免残留累积触发 MediaStore 索引膨胀
            try:
                subprocess.run(cmd_rm_remote, capture_output=True, timeout=5)
            except Exception:
                pass

    # ----------------------- 分辨率 -----------------------

    def get_screen_size(self) -> Optional[tuple]:
        """
        获取屏幕物理分辨率 (width, height)。
        """
        if self._screen_size is not None:
            return self._screen_size
        cmd = self.base_cmd + ["shell", "wm", "size"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # 输出示例: Physical size: 1080x2400
                for line in result.stdout.splitlines():
                    if "Physical size" in line:
                        parts = line.split(":")[-1].strip().split("x")
                        self._screen_size = (int(parts[0]), int(parts[1]))
                        return self._screen_size
        except Exception:
            pass
        return None
