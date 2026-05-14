"""
analyzer.py —— 业务分类与内容分析核心模块

职责：
1. 全图预识别软件名称，判定业务大类。
2. 根据业务大类，对不同裁剪区域执行定向 OCR：
   - 分辨率关键词（上/下 1/6）
   - 卡顿关键词（中间 1/3 + 全图扫描）
   - 帧率（右上角 1/10，仅游戏类）
3. 软件名称缓存策略：避免每秒全图 OCR，保障单帧 <500ms。

返回数据结构：
    {
        "category": str,   # 业务大类
        "app": str,        # 具体软件名
        "resolution": str, # 识别到的分辨率/清晰度
        "is_lag": str,     # "是" / "否"
        "fps": str,        # 帧率，如 "60fps"
    }
"""

import re
import time
from typing import List, Dict, Optional
from PIL import Image

from .config import (
    APP_TO_CATEGORY,
    RESOLUTION_KEYWORDS,
    LAG_KEYWORDS,
    FPS_PATTERNS,
    RESOLUTION_CATEGORIES,
    FPS_CATEGORIES,
    APP_CACHE_SECONDS,
    MAX_IMAGE_LONG_SIDE,
    GAME_LATENCY_APPS,
    LATENCY_REGIONS,
    LATENCY_PATTERNS,
    CROP_TOP_RIGHT_RATIO,
)
from .image_utils import ImageProcessor
from .ocr_engine import OCREngine


class Analyzer:
    """
    截图内容分析器。
    """

    def __init__(self, ocr_engine: OCREngine):
        self.ocr = ocr_engine
        # 缓存字段
        self._cached_app: Optional[str] = None
        self._cached_category: Optional[str] = None
        self._cached_at: float = 0.0
        # 用户手动指定的目标软件（跳过全屏 OCR 识别）
        self._target_app: Optional[str] = None
        self._target_category: Optional[str] = None

    # ----------------------- 缓存管理 -----------------------

    def _is_cache_valid(self) -> bool:
        """判断当前缓存的软件名是否在有效期内。"""
        if self._cached_app is None:
            return False
        return (time.time() - self._cached_at) < APP_CACHE_SECONDS

    def _update_cache(self, app: str, category: str):
        """更新软件名缓存。"""
        self._cached_app = app
        self._cached_category = category
        self._cached_at = time.time()

    def _clear_cache(self):
        """清除缓存（例如识别失败时）。"""
        self._cached_app = None
        self._cached_category = None
        self._cached_at = 0.0

    def set_target_app(self, app_name: str):
        """
        手动设置目标软件名及其业务大类。
        设置后将跳过全屏 OCR 识别软件名的步骤，直接进行定向区域识别。
        """
        from .config import get_category_by_app
        self._target_app = app_name
        self._target_category = get_category_by_app(app_name)
        self._cached_app = app_name
        self._cached_category = self._target_category
        self._cached_at = time.time()

    # ----------------------- 匹配逻辑 -----------------------

    @staticmethod
    def match_app(texts: List[str]) -> tuple:
        """
        从 OCR 文本列表中匹配软件名称及其业务大类。

        :return: (软件名, 业务大类)，未匹配返回 (None, None)
        """
        # 为了提高准确率，优先匹配长词（如"腾讯会议"先于"会议"）
        # 但映射表里的键已经是完整名称，直接遍历即可。
        # 为提升效率，构建一个按长度降序的列表，避免短词误匹配。
        sorted_apps = sorted(APP_TO_CATEGORY.items(), key=lambda x: len(x[0]), reverse=True)
        for app_name, category in sorted_apps:
            for text in texts:
                if app_name in text:
                    return app_name, category
        return None, None

    @staticmethod
    def match_resolution(texts: List[str]) -> str:
        """
        从文本中匹配分辨率/清晰度关键词。
        若识别到多个，以第一个为准（通常 UI 上只会显示一个当前清晰度）。
        """
        for text in texts:
            upper = text.upper()
            for kw in RESOLUTION_KEYWORDS:
                if kw.upper() in upper or kw in text:
                    return kw
        return ""

    @staticmethod
    def match_lag(texts: List[str]) -> str:
        """
        从文本中匹配卡顿/网络异常关键词。
        :return: "是" 或 "否"
        """
        for text in texts:
            for kw in LAG_KEYWORDS:
                if kw in text:
                    return "是"
        return "否"

    @staticmethod
    def match_fps(texts: List[str]) -> str:
        """
        从文本中匹配帧率，如 "60fps", "FPS 59", "FPS59"。
        返回第一个匹配结果，统一小写格式如 "59fps"。
        对 OCR 粘连结果（如 FPS5910ms）做鲁棒处理。
        """
        patterns = [re.compile(p, re.IGNORECASE) for p in FPS_PATTERNS]
        for text in texts:
            for pattern in patterns:
                m = pattern.search(text)
                if m:
                    digit = m.group(1)
                    if not digit or not digit.isdigit():
                        continue
                    # 帧率通常 1~240；若数字过长（OCR 粘连），取前 2~3 位合理值
                    if len(digit) > 3:
                        for length in (3, 2):
                            candidate = digit[:length]
                            if 1 <= int(candidate) <= 240:
                                return f"{candidate}fps"
                        continue
                    val = int(digit)
                    if 1 <= val <= 240:
                        return f"{digit}fps"
        return ""

    @staticmethod
    def match_latency(texts: List[str]) -> str:
        """
        从文本中匹配游戏延迟值，如 "25ms", "Ping: 45", "延迟: 60"。
        返回第一个匹配结果，保留原始格式。
        """
        compiled = [re.compile(p) for p in LATENCY_PATTERNS]
        for text in texts:
            for pattern in compiled:
                m = pattern.search(text)
                if m:
                    return m.group(0)
        return ""

    # ----------------------- 核心分析 -----------------------

    def analyze_frame(self, image: Image.Image) -> Dict[str, str]:
        """
        对单帧截图进行完整分析（业务分类 + 分辨率/卡顿/帧率识别）。

        流程：
        1. 缩放图像（降低 OCR 耗时）。
        2. 若缓存过期，对全图 OCR 识别软件名并判定业务大类。
        3. 根据业务大类，选择对应裁剪区域进行定向 OCR。
        4. 汇总结果返回。

        :param image: 原始截图（PIL.Image）
        :return: 结构化结果字典
        """
        result = {
            "category": "",
            "app": "",
            "resolution": "",
            "is_lag": "否",
            "fps": "",
        }

        # 1. 初始化图像处理器并缩放
        processor = ImageProcessor(image)
        resized = processor.resize_for_performance(MAX_IMAGE_LONG_SIDE)
        resized_processor = ImageProcessor(resized)

        # 2. 软件名 & 业务大类识别（带缓存 / 或用户预设）
        category = None
        app_name = None

        if self._target_app is not None:
            # 用户已手动指定目标软件，跳过全屏 OCR
            app_name = self._target_app
            category = self._target_category
            self._update_cache(app_name, category)
        elif self._is_cache_valid():
            category = self._cached_category
            app_name = self._cached_app
        else:
            # 缓存过期或从未识别：对全图 OCR
            full_texts = self.ocr.recognize(resized)
            app_name, category = self.match_app(full_texts)
            if app_name:
                self._update_cache(app_name, category)
            else:
                self._clear_cache()
                result["is_lag"] = self.match_lag(full_texts)
                return result

        result["app"] = app_name or ""
        result["category"] = category or ""

        # 3. 直接对全缩小图做 OCR（mobile 模型够快，全图识别不易漏字）
        texts = self.ocr.recognize(resized)

        # 4. 从 OCR 结果中匹配各类关键词
        result["is_lag"] = self.match_lag(texts)
        if category in RESOLUTION_CATEGORIES:
            result["resolution"] = self.match_resolution(texts)
        if category in FPS_CATEGORIES:
            fps = self.match_fps(texts)
            if not fps:
                # 全图未命中，对右上角重点区域裁剪后再识别
                top_right = resized_processor.crop_top_right(CROP_TOP_RIGHT_RATIO)
                tr_texts = self.ocr.recognize(top_right)
                fps = self.match_fps(tr_texts)
            result["fps"] = fps

        # 5. 游戏延迟识别（仅针对已知有延迟显示的游戏）
        latency = ""
        if category == "（云）游戏" and app_name in GAME_LATENCY_APPS:
            latency = self.match_latency(texts)
            if not latency:
                # 全图未命中，对重点区域裁剪拼接后再识别
                proc = ImageProcessor(resized)
                region_images = [proc.crop_by_ratio(*r) for r in LATENCY_REGIONS]
                stacked = ImageProcessor.stack_vertical(region_images)
                region_texts = self.ocr.recognize(stacked)
                latency = self.match_latency(region_texts)
        result["latency"] = latency

        return result
