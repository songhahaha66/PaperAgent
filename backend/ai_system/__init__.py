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

# 延迟导入避免循环依赖
__version__ = "0.1.0"

# 定义可导出的类名，但不立即导入
__all__ = [
    'MainAgent',
    'CodeAgent',
    'BaseAgent',
    'LLMHandler',
    'LLMFactory',
    'StreamOutputManager',
    'ContextManager',
    'CodeExecutor',
    'FileTools',
    'TemplateAgentTools'
    # 'ToolManager'  # 已移除
]

def _import_core_components():
    """延迟导入核心组件"""
    from .core_agents import MainAgent, CodeAgent, BaseAgent
    from .core_handlers.llm_handler import LLMHandler
    from .core_handlers.llm_factory import LLMFactory
    from .core_managers.stream_manager import StreamOutputManager
    from .core_managers.context_manager import ContextManager
    # ToolManager 已移除，不再导入
    from .core_tools.code_executor import CodeExecutor
    from .core_tools.file_tools import FileTools
    from .core_tools.template_tools import TemplateAgentTools

    return {
        'MainAgent': MainAgent,
        'CodeAgent': CodeAgent,
        'BaseAgent': BaseAgent,
        'LLMHandler': LLMHandler,
        'LLMFactory': LLMFactory,
        'StreamOutputManager': StreamOutputManager,
        'ContextManager': ContextManager,
        # 'ToolManager': ToolManager,  # 已移除
        'CodeExecutor': CodeExecutor,
        'FileTools': FileTools,
        'TemplateAgentTools': TemplateAgentTools
    }

# 创建模块级别的属性访问器
def __getattr__(name):
    """延迟导入属性"""
    if name in __all__:
        components = _import_core_components()
        if name in components:
            return components[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
