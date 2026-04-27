"""
image_utils.py —— 图像处理工具模块

封装基于 Pillow 的常用裁剪、缩放、转换操作，供 analyzer 调用。
所有裁剪方法均返回新的 Image 对象，不修改原图。
"""

from typing import Tuple, List
from PIL import Image
import numpy as np


class ImageProcessor:
    """
    对单张截图进行各类区域裁剪和性能优化缩放。
    """

    def __init__(self, image: Image.Image):
        """
        :param image: PIL.Image 对象（RGBA / RGB 均可）
        """
        self.image = image.convert("RGB")
        self.width, self.height = self.image.size

    # ----------------------- 性能优化 -----------------------

    def resize_for_performance(self, max_long_side: int = 1280) -> Image.Image:
        """
        等比缩放图像，使长边不超过 max_long_side。
        可显著降低 OCR 计算量，提升单帧处理速度。

        :param max_long_side: 长边最大像素值
        :return: 缩放后的 Image 对象
        """
        w, h = self.width, self.height
        long_side = max(w, h)
        if long_side <= max_long_side:
            return self.image.copy()

        scale = max_long_side / long_side
        new_size = (int(w * scale), int(h * scale))
        return self.image.resize(new_size, Image.Resampling.LANCZOS)

    # ----------------------- 区域裁剪 -----------------------

    def crop_top(self, ratio: float = 1 / 6) -> Image.Image:
        """
        裁剪上方区域（用于识别分辨率/清晰度）
        :param ratio: 占全高的比例
        :return: 裁剪后的 Image
        """
        h = int(self.height * ratio)
        return self.image.crop((0, 0, self.width, h))

    def crop_bottom(self, ratio: float = 1 / 6) -> Image.Image:
        """
        裁剪下方区域（用于识别分辨率/清晰度）
        :param ratio: 占全高的比例
        :return: 裁剪后的 Image
        """
        h = int(self.height * ratio)
        top = self.height - h
        return self.image.crop((0, top, self.width, self.height))

    def crop_middle(self, ratio: float = 1 / 3) -> Image.Image:
        """
        裁剪中间区域（用于检测卡顿弹窗/提示）
        :param ratio: 占全高的比例
        :return: 裁剪后的 Image
        """
        h = int(self.height * ratio)
        top = (self.height - h) // 2
        bottom = top + h
        return self.image.crop((0, top, self.width, bottom))

    def crop_top_right(self, ratio: float = 1 / 10) -> Image.Image:
        """
        裁剪右上角区域（游戏类通常在此显示 fps/网络状态）
        :param ratio: 占全宽和全高的比例（正方形区域）
        :return: 裁剪后的 Image
        """
        w = int(self.width * ratio)
        h = int(self.height * ratio)
        left = self.width - w
        return self.image.crop((left, 0, self.width, h))

    # ----------------------- 区域拼接 -----------------------

    @staticmethod
    def stack_vertical(images: List[Image.Image]) -> Image.Image:
        """
        将多张图像按垂直方向拼接为一张长图。
        用于把多个分散区域合并后只做一次 OCR。

        :param images: PIL.Image 列表（宽度需一致）
        :return: 拼接后的 Image
        """
        if not images:
            raise ValueError("images list is empty")
        width = images[0].width
        total_height = sum(img.height for img in images)
        stacked = Image.new("RGB", (width, total_height))
        y = 0
        for img in images:
            stacked.paste(img, (0, y))
            y += img.height
        return stacked

    # ----------------------- 格式转换 -----------------------

    def to_numpy(self) -> np.ndarray:
        """
        转换为 OpenCV / PaddleOCR 所需的 numpy array（RGB）。
        """
        return np.array(self.image)
