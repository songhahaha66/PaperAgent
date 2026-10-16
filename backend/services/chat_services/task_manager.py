"""
AI任务管理器
支持任务状态跟踪、断线重连恢复
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

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
    type: str  # 'content', 'json_block'
    data: Any
    seq: int = 0  # 任务内递增序号，前端按它去重、断线后从断点续传
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
    # 累积的输出（用于断线重连恢复）。不设上限：长任务的早期事件（题目分析、阶段）也要能恢复；
    # 体积最大的 plan_updated 只保留最新一条
    outputs: list = field(default_factory=list)
    last_seq: int = 0
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
        task = self._tasks.get(work_id)
        if task and task.status == TaskStatus.RUNNING:
            if self._is_stale(task):
                logger.warning(f"任务协程已结束但状态仍是运行中，标记失败: {task.task_id}")
                self.fail_task(work_id, "任务意外中断")
                return None
            return task
        return None

    @staticmethod
    def _is_stale(task: AITask) -> bool:
        """按协程是否还活着判断，不按时长：完整的数模流水线常常超过半小时，按时长判定会把正常任务杀掉"""
        return task._async_task is not None and task._async_task.done()
    
    def start_task(self, work_id: str):
        """标记任务开始"""
        task = self._tasks.get(work_id)
        if task:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            logger.info(f"任务开始: {task.task_id}")
    
    def add_output(self, work_id: str, output_type: str, data: Any) -> Optional[int]:
        """添加任务输出，返回它的序号（没有任务时返回 None）"""
        task = self._tasks.get(work_id)
        if task:
            task.last_seq += 1
            output = TaskOutput(type=output_type, data=data, seq=task.last_seq)
            if output_type == 'json_block' and isinstance(data, dict) and data.get('type') == 'plan_updated':
                task.outputs = [
                    o for o in task.outputs
                    if not (o.type == 'json_block' and isinstance(o.data, dict) and o.data.get('type') == 'plan_updated')
                ]
            task.outputs.append(output)
            
            # 同时更新最终内容
            if output_type == 'content':
                task.final_content += data
            elif output_type == 'json_block':
                task.json_blocks.append(data)
            return output.seq
        return None
    
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
        """标记任务失败并取消后台协程"""
        task = self._tasks.get(work_id)
        if task:
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            task.error = error
            if task._async_task and not task._async_task.done():
                task._async_task.cancel()
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
    
    def get_outputs_after(self, work_id: str, seq: int = 0) -> list[TaskOutput]:
        """序号大于 seq 的输出（断线重连时从断点续传）"""
        task = self._tasks.get(work_id)
        if not task:
            return []
        return [o for o in task.outputs if o.seq > seq]
    
    def get_task_status(self, work_id: str) -> dict:
        task = self._tasks.get(work_id)
        if not task:
            return {"status": "none", "has_task": False}

        if task.status == TaskStatus.RUNNING and self._is_stale(task):
            logger.warning(f"任务协程已结束但状态仍是运行中，标记失败: {task.task_id}")
            self.fail_task(work_id, "任务意外中断")

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
            "last_seq": task.last_seq,
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
