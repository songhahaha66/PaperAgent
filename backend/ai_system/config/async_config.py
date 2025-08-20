"""
异步配置管理器
管理异步任务、线程池和并发设置，确保系统的高性能和稳定性
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from typing import Optional, Dict, Any
import threading

logger = logging.getLogger(__name__)


class AsyncConfigManager:
    """异步配置管理器"""
    
    def __init__(self):
        self.thread_pool: Optional[ThreadPoolExecutor] = None
        self.process_pool: Optional[ProcessPoolExecutor] = None
        self.max_workers = 10
        self.max_processes = 4
        self._lock = threading.Lock()
        self._initialized = False
        
    def initialize(self, max_workers: int = 10, max_processes: int = 4):
        """初始化异步配置"""
        with self._lock:
            if self._initialized:
                logger.warning("AsyncConfigManager已经初始化")
                return
                
            self.max_workers = max_workers
            self.max_processes = max_processes
            
            # 创建线程池
            self.thread_pool = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="AsyncConfig"
            )
            
            # 创建进程池（可选，用于CPU密集型任务）
            try:
                self.process_pool = ProcessPoolExecutor(
                    max_workers=max_processes
                )
                logger.info(f"进程池初始化成功，最大进程数: {max_processes}")
            except Exception as e:
                logger.warning(f"进程池初始化失败，将只使用线程池: {e}")
                self.process_pool = None
            
            self._initialized = True
            logger.info(f"AsyncConfigManager初始化完成，线程池大小: {max_workers}")
    
    def get_thread_pool(self) -> ThreadPoolExecutor:
        """获取线程池"""
        if not self._initialized:
            raise RuntimeError("AsyncConfigManager未初始化，请先调用initialize()")
        return self.thread_pool
    
    def get_process_pool(self) -> Optional[ProcessPoolExecutor]:
        """获取进程池"""
        if not self._initialized:
            raise RuntimeError("AsyncConfigManager未初始化，请先调用initialize()")
        return self.process_pool
    
    async def run_in_thread(self, func, *args, **kwargs):
        """在线程池中运行同步函数"""
        if not self._initialized:
            raise RuntimeError("AsyncConfigManager未初始化，请先调用initialize()")
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.thread_pool,
            functools.partial(func, *args, **kwargs)
        )
    
    async def run_in_process(self, func, *args, **kwargs):
        """在进程池中运行函数"""
        if not self._initialized:
            raise RuntimeError("AsyncConfigManager未初始化，请先调用initialize()")
        
        if not self.process_pool:
            # 如果没有进程池，回退到线程池
            logger.warning("进程池不可用，回退到线程池")
            return await self.run_in_thread(func, *args, **kwargs)
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.process_pool,
            functools.partial(func, *args, **kwargs)
        )
    
    def shutdown(self):
        """关闭所有池"""
        with self._lock:
            if self.thread_pool:
                self.thread_pool.shutdown(wait=True)
                self.thread_pool = None
                logger.info("线程池已关闭")
            
            if self.process_pool:
                self.process_pool.shutdown(wait=True)
                self.process_pool = None
                logger.info("进程池已关闭")
            
            self._initialized = False
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态信息"""
        return {
            "initialized": self._initialized,
            "max_workers": self.max_workers,
            "max_processes": self.max_processes,
            "thread_pool_active": self.thread_pool is not None,
            "process_pool_active": self.process_pool is not None
        }


# 全局异步配置管理器实例
async_config = AsyncConfigManager()


def get_async_config() -> AsyncConfigManager:
    """获取全局异步配置管理器"""
    return async_config


def initialize_async_config(max_workers: int = 10, max_processes: int = 4):
    """初始化全局异步配置"""
    async_config.initialize(max_workers, max_processes)


def shutdown_async_config():
    """关闭全局异步配置"""
    async_config.shutdown()


# 导入functools
import functools
