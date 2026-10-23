"""
AI系统模块
论文流水线（graph/）、导出（export/）、代码沙箱（sandbox/）、评分（judge/）
"""

import sys
import os

# 添加 backend 根目录到Python路径，确保可以导入 services/config 等顶层模块。
# 不能插入 backend/ai_system，否则顶层 import config 会误命中 ai_system/config。
backend_root = os.path.dirname(os.path.dirname(__file__))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

__version__ = "0.1.0"
