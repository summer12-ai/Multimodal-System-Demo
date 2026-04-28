"""
ocr_engine.py —— PaddleOCR 离线识别引擎封装

职责：
1. 初始化 PaddleOCR（使用轻量模型，关闭方向分类器以提速）。
2. 提供 recognize() 统一接口，输入 PIL.Image，返回纯文本列表。
3. 对异常进行捕获，防止单张图识别失败导致主循环崩溃。

性能提示：
- 首次初始化会下载模型（约 10~20MB），请保持网络通畅；下载后即为纯离线运行。
- use_angle_cls=False 可节省约 30% 时间。
- 输入图像尺寸越小，det+rec 越快。
"""

import time
from typing import List
from PIL import Image
import numpy as np

# PaddleOCR 延迟导入，避免脚本一启动就加载大模块（实际在类实例化时加载）
# 这样可以让主程序先打印提示信息，提升用户体验
_paddleocr_imported = False
PaddleOCR = None


def _ensure_import():
    """确保 PaddleOCR 已导入（单例）"""
    global _paddleocr_imported, PaddleOCR
    if not _paddleocr_imported:
        from paddleocr import PaddleOCR as _PaddleOCR
        PaddleOCR = _PaddleOCR
        _paddleocr_imported = True


class OCREngine:
    """
    基于 PaddleOCR 的轻量离线识别引擎。
    """

    def __init__(self, use_gpu: bool = False, max_image_long_side: int = 1280):
        """
        :param use_gpu: 是否使用 GPU（需安装 paddlepaddle-gpu）
        :param max_image_long_side: 内部缩放参考值，仅用于日志提示
        """
        _ensure_import()
        print("[OCREngine] 正在初始化 PaddleOCR 模型，首次运行需下载模型文件，请稍候...")
        start = time.time()

        # 使用 mobile 轻量模型（PP-OCRv5_mobile），CPU 推理速度比 server 版快 3~5 倍
        # 禁用文档方向分类/矫正/文本行方向分类（手机截图本身就是正向，预处理反而干扰识别）
        # 首次运行需下载模型文件（约 20MB），请保持网络通畅
        self.ocr = PaddleOCR(
            text_detection_model_name='PP-OCRv5_mobile_det',
            text_recognition_model_name='PP-OCRv5_mobile_rec',
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_thresh=0.3,
            text_det_box_thresh=0.3,
        )

        elapsed = time.time() - start
        print(f"[OCREngine] 初始化完成，耗时 {elapsed:.1f}s")
        self.max_image_long_side = max_image_long_side

    def recognize(self, image: Image.Image) -> List[str]:
        """
        对单张图片进行 OCR 识别。

        :param image: PIL.Image 对象（RGB）
        :return: 识别到的文本字符串列表（按检测顺序）
        """
        try:
            arr = np.array(image.convert("RGB"))
            result = self.ocr.ocr(arr)
            texts = []
            if not result:
                return texts

            # PaddleOCR 3.x 返回字典列表格式：
            # [{'rec_texts': ['text1', 'text2'], 'rec_scores': [...], ...}]
            first_page = result[0]
            if isinstance(first_page, dict):
                texts = [str(t) for t in first_page.get("rec_texts", [])]
            elif isinstance(first_page, list):
                # 兼容旧版嵌套列表格式：[[[box], [text, confidence]], ...]
                for line in first_page:
                    if line and len(line) >= 2:
                        texts.append(str(line[1][0]))
            return texts
        except Exception as e:
            # 单帧识别失败不应阻断主循环，返回空列表并打印警告
            print(f"[OCREngine] 识别失败: {e}")
            return []
