"""
日志配置管理
简化版本：只控制日志级别和LiteLLM输出
"""

import logging
import os

def setup_simple_logging(level: str = "INFO"):
    """
    设置简单的日志配置
    
    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # 设置日志级别
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # 配置根日志器
    logging.basicConfig(
        level=log_level,
        format='%(levelname)s - %(name)s - %(message)s',
        handlers=[
            logging.StreamHandler()  # 只输出到终端
        ]
    )
    
    # 关闭LiteLLM的详细输出
    try:
        import litellm
        litellm.set_verbose = False
        print(f"LiteLLM日志已关闭")
    except ImportError:
        pass
    
    # 关闭其他库的冗余日志
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    
    print(f"日志配置完成，级别: {level}")

def set_log_level(level: str):
    """动态设置日志级别"""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    
    # 重新设置LiteLLM
    try:
        import litellm
        litellm.set_verbose = False
    except ImportError:
        pass
    
    print(f"日志级别已更新为: {level}")

# 便捷函数
def debug_mode():
    """启用调试模式"""
    setup_simple_logging("DEBUG")

def quiet_mode():
    """启用静默模式（只显示错误）"""
    setup_simple_logging("ERROR")

def info_mode():
    """启用信息模式（默认）"""
    setup_simple_logging("INFO")
