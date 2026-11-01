"""
沙箱执行结果及离线测试内核；生产任务由 factory 创建远端独立进程。
"""

from .kernel import CellResult, PythonKernel

__all__ = ["CellResult", "PythonKernel"]
