"""
异步处理配置
优化异步任务的执行参数，平衡性能和响应性
"""

import asyncio
from typing import Dict, Any

class AsyncConfig:
    """异步处理配置类"""
    
    # LLM流式处理配置
    LLM_STREAM_CONFIG = {
        "yield_interval": 5,  # 每多少个chunk让出控制权
        "yield_delay": 0.0005,  # 让出控制权的延迟时间（秒）
        "max_concurrent_tools": 1,  # 最大并发工具调用数（改为1，顺序执行）
    }
    
    # WebSocket配置
    WEBSOCKET_CONFIG = {
        "content_yield_threshold": 50,  # 内容长度超过此值才让出控制权
        "content_yield_delay": 0.0005,  # 内容发送的延迟时间
        "json_block_yield_delay": 0,  # JSON块发送的延迟时间（0表示不延迟）
        "heartbeat_interval": 30,  # 心跳间隔（秒）
        "connection_timeout": 300,  # 连接超时时间（秒）
    }
    
    # 工具调用配置
    TOOL_CALL_CONFIG = {
        "sequential_execution": True,  # 是否顺序执行工具调用
        "execution_yield_delay": 0.001,  # 工具调用间的延迟时间
        "max_retry_attempts": 2,  # 最大重试次数
        "retry_delay": 0.1,  # 重试延迟时间（秒）
    }
    
    # 异步任务配置
    TASK_CONFIG = {
        "max_workers": 2,  # 线程池最大工作线程数
        "task_timeout": 300,  # 任务超时时间（秒）
        "context_switch_delay": 0.0001,  # 上下文切换延迟时间
    }
    
    @classmethod
    def get_llm_stream_config(cls) -> Dict[str, Any]:
        """获取LLM流式处理配置"""
        return cls.LLM_STREAM_CONFIG.copy()
    
    @classmethod
    def get_websocket_config(cls) -> Dict[str, Any]:
        """获取WebSocket配置"""
        return cls.WEBSOCKET_CONFIG.copy()
    
    @classmethod
    def get_tool_call_config(cls) -> Dict[str, Any]:
        """获取工具调用配置"""
        return cls.TOOL_CALL_CONFIG.copy()
    
    @classmethod
    def get_task_config(cls) -> Dict[str, Any]:
        """获取异步任务配置"""
        return cls.TASK_CONFIG.copy()
    
    @classmethod
    def optimize_for_performance(cls):
        """性能优化配置"""
        cls.LLM_STREAM_CONFIG["yield_interval"] = 10
        cls.LLM_STREAM_CONFIG["yield_delay"] = 0.0001
        cls.WEBSOCKET_CONFIG["content_yield_threshold"] = 100
        cls.WEBSOCKET_CONFIG["content_yield_delay"] = 0.0001
        cls.TASK_CONFIG["max_workers"] = 4
    
    @classmethod
    def optimize_for_responsiveness(cls):
        """响应性优化配置"""
        cls.LLM_STREAM_CONFIG["yield_interval"] = 3
        cls.LLM_STREAM_CONFIG["yield_delay"] = 0.001
        cls.WEBSOCKET_CONFIG["content_yield_threshold"] = 20
        cls.WEBSOCKET_CONFIG["content_yield_delay"] = 0.001
        cls.TASK_CONFIG["max_workers"] = 2
