"""
result_manager.py —— 识别结果管理与 Excel 导出模块

职责：
1. 实时收集每一帧的结构化识别结果。
2. 在终端以表格形式打印最新结果（保留最近 N 行上下文）。
3. 最终导出带时间戳的 Excel 文件（.xlsx）。

依赖：openpyxl
"""

import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from openpyxl import Workbook


class ResultManager:
    """
    结果收集器：内存存储 + 实时终端表格 + Excel 导出。
    """

    # 终端表格列定义（与 Excel 保持一致）
    COLUMNS = ["时间戳", "业务大类", "具体软件", "分辨率", "是否卡顿", "帧率"]

    def __init__(self, max_display_rows: int = 10, category: str = "", app_name: str = ""):
        """
        :param max_display_rows: 终端实时表格最多展示最近 N 行
        :param category: 业务大类（用于文件名）
        :param app_name: 具体软件名（用于文件名）
        """
        self.max_display_rows = max_display_rows
        self.category = category
        self.app_name = app_name
        self.results: List[Dict[str, str]] = []
        self.start_time: Optional[float] = None
        self.excel_path: Optional[str] = None
        self._wb: Optional[Workbook] = None
        self._ws = None

    # ----------------------- 生命周期 -----------------------

    def start(self):
        """
        开始一轮新的识别任务，初始化内存列表和 Excel 工作簿。
        """
        self.results.clear()
        self.start_time = time.time()
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 输出目录：使用相对路径，避免硬编码本地绝对路径
        output_dir = Path("results/ocr")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 文件名：业务大类-应用名称-ocr-{timestamp}.xlsx
        safe_category = self.category.replace("/", "-").replace("\\", "-") if self.category else "unknown"
        safe_app = self.app_name.replace("/", "-").replace("\\", "-") if self.app_name else "unknown"
        filename = f"{safe_category}-{safe_app}-ocr-{timestamp_str}.xlsx"
        self.excel_path = str(output_dir / filename)

        self._wb = Workbook()
        self._ws = self._wb.active
        self._ws.title = "识别结果"
        self._ws.append(self.COLUMNS)

        print(f"[ResultManager] 结果文件准备就绪: {self.excel_path}")

    def add_result(self, row: Dict[str, str]):
        """
        添加一行结果，同时写入 Excel 工作簿（内存中，尚未 save）。
        """
        self.results.append(row)
        if self._ws is not None:
            self._ws.append([
                row.get("时间戳", ""),
                row.get("业务大类", ""),
                row.get("具体软件", ""),
                row.get("分辨率", ""),
                row.get("是否卡顿", ""),
                row.get("帧率", ""),
            ])

    def save(self) -> Optional[str]:
        """
        将 Excel 工作簿保存到磁盘。
        :return: 保存的文件路径
        """
        if self._wb is None or self.excel_path is None:
            return None
        try:
            # 确保目录存在
            output_dir = os.path.dirname(self.excel_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            self._wb.save(self.excel_path)
            return self.excel_path
        except Exception as e:
            print(f"[ResultManager] Excel 保存失败: {e}")
            return None

    # ----------------------- 终端实时表格 -----------------------

    def print_realtime_table(self):
        """
        在终端打印最近 N 行结果的结构化表格。
        采用固定宽度 + 分隔线，兼容大多数终端环境。
        """
        if not self.results:
            return

        # 取最近 N 行
        display = self.results[-self.max_display_rows:]

        # 计算每列最大宽度（含表头）
        widths = [len(c) for c in self.COLUMNS]
        for row in display:
            for idx, col in enumerate(self.COLUMNS):
                val_len = len(str(row.get(col, "")))
                if val_len > widths[idx]:
                    widths[idx] = min(val_len, 20)  # 上限，避免超长

        # 确保最小显示宽度
        widths = [max(w, 6) for w in widths]

        def _sep():
            parts = ["+" + "-" * (w + 2) for w in widths]
            return "".join(parts) + "+"

        def _line(values):
            parts = []
            for idx, val in enumerate(values):
                text = str(val)[:widths[idx]]
                parts.append("| " + text.ljust(widths[idx]) + " ")
            return "".join(parts) + "|"

        # 清屏并打印（Windows 兼容）
        print("\n" * 2)
        print("[实时识别结果]")
        print(_sep())
        print(_line(self.COLUMNS))
        print(_sep())
        for row in display:
            print(_line([row.get(c, "") for c in self.COLUMNS]))
        print(_sep())
        print(f"共 {len(self.results)} 条记录 | 最新: {display[-1].get('时间戳', '')}")
