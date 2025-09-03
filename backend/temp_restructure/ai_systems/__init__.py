"""
AI系统模块
包含AI对话、论文生成、代码执行等核心功能
"""

import sys
import os

# 添加项目根目录到Python路径，确保可以导入其他模块
project_root = os.path.dirname(__file__)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

__version__ = "0.1.0"
