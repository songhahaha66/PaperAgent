"""
AI任务管理器
支持任务状态跟踪、断线重连恢复
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any
from collections import deque

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskOutput:
    """任务输出项"""
    type: str  # 'content', 'json_block', 'error', 'complete'
    data: Any
    timestamp: float = field(default_factory=time.time)


@dataclass
class AITask:
    """AI任务"""
    task_id: str
    work_id: str
    user_id: int
    message: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    # 累积的输出（用于断线重连恢复）
    outputs: deque = field(default_factory=lambda: deque(maxlen=1000))
    # 最终内容
    final_content: str = ""
    json_blocks: list = field(default_factory=list)
    # asyncio任务引用
    _async_task: Optional[asyncio.Task] = None


class TaskManager:
    """
    AI任务管理器（单例）
    管理所有进行中的AI任务，支持断线重连恢复
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # work_id -> AITask
        self._tasks: dict[str, AITask] = {}
        # 任务超时时间（秒）
        self._task_timeout = 600  # 10分钟
        # 已完成任务保留时间（秒）
        self._completed_retention = 60  # 1分钟
        logger.info("TaskManager 初始化完成")
    
    def create_task(self, work_id: str, user_id: int, message: str) -> AITask:
        """创建新任务"""
        # 如果已有进行中的任务，先取消
        if work_id in self._tasks:
            old_task = self._tasks[work_id]
            if old_task.status == TaskStatus.RUNNING:
                self.cancel_task(work_id)
        
        task_id = f"{work_id}_{int(time.time() * 1000)}"
        task = AITask(
            task_id=task_id,
            work_id=work_id,
            user_id=user_id,
            message=message
        )
        self._tasks[work_id] = task
        logger.info(f"创建任务: {task_id}, work_id: {work_id}")
        return task
    
    def get_task(self, work_id: str) -> Optional[AITask]:
        """获取任务"""
        return self._tasks.get(work_id)
    
    def get_running_task(self, work_id: str) -> Optional[AITask]:
        """获取正在运行的任务"""
        task = self._tasks.get(work_id)
        if task and task.status == TaskStatus.RUNNING:
            return task
        return None
    
    def start_task(self, work_id: str):
        """标记任务开始"""
        task = self._tasks.get(work_id)
        if task:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            logger.info(f"任务开始: {task.task_id}")
    
    def add_output(self, work_id: str, output_type: str, data: Any):
        """添加任务输出"""
        task = self._tasks.get(work_id)
        if task:
            output = TaskOutput(type=output_type, data=data)
            task.outputs.append(output)
            
            # 同时更新最终内容
            if output_type == 'content':
                task.final_content += data
            elif output_type == 'json_block':
                task.json_blocks.append(data)
    
    def complete_task(self, work_id: str):
        """标记任务完成"""
        task = self._tasks.get(work_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            logger.info(f"任务完成: {task.task_id}")
            # 启动清理定时器
            asyncio.create_task(self._cleanup_completed_task(work_id))
    
    def fail_task(self, work_id: str, error: str):
        """标记任务失败"""
        task = self._tasks.get(work_id)
        if task:
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            task.error = error
            logger.error(f"任务失败: {task.task_id}, 错误: {error}")
            asyncio.create_task(self._cleanup_completed_task(work_id))
    
    def cancel_task(self, work_id: str):
        """取消任务"""
        task = self._tasks.get(work_id)
        if task:
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            if task._async_task and not task._async_task.done():
                task._async_task.cancel()
            logger.info(f"任务取消: {task.task_id}")
    
    def set_async_task(self, work_id: str, async_task: asyncio.Task):
        """设置asyncio任务引用"""
        task = self._tasks.get(work_id)
        if task:
            task._async_task = async_task
    
    def get_outputs_since(self, work_id: str, since_timestamp: float = 0) -> list[TaskOutput]:
        """获取指定时间戳之后的所有输出"""
        task = self._tasks.get(work_id)
        if not task:
            return []
        return [o for o in task.outputs if o.timestamp > since_timestamp]
    
    def get_task_status(self, work_id: str) -> dict:
        """获取任务状态信息"""
        task = self._tasks.get(work_id)
        if not task:
            return {"status": "none", "has_task": False}
        
        return {
            "has_task": True,
            "task_id": task.task_id,
            "status": task.status.value,
            "message": task.message,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "error": task.error,
            "output_count": len(task.outputs),
            "final_content_length": len(task.final_content),
            "json_blocks_count": len(task.json_blocks)
        }
    
    async def _cleanup_completed_task(self, work_id: str):
        """清理已完成的任务"""
        await asyncio.sleep(self._completed_retention)
        task = self._tasks.get(work_id)
        if task and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            del self._tasks[work_id]
            logger.info(f"清理已完成任务: {task.task_id}")


# 全局单例
task_manager = TaskManager()
