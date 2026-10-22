"""
JSON卡片格式聊天记录管理器
负责管理work对应的聊天记录JSON文件，支持结构化JSON卡片格式
"""

import json
import os
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid
import tempfile
import threading
import fcntl
from contextlib import contextmanager
from functools import wraps
from config.paths import get_workspaces_path

logger = logging.getLogger(__name__)


_history_locks = {}
_history_locks_guard = threading.Lock()


def serialized_update(method):
    """One read/modify/replace transaction, shared by all writers and processes."""
    @wraps(method)
    def update(self, work_id, *args, **kwargs):
        with self._history_lock(work_id):
            return method(self, work_id, *args, **kwargs)
    return update


class ChatHistoryManager:
    """管理JSON卡片格式的聊天记录"""

    def __init__(self, workspace_base: str = None):
        if workspace_base is None:
            # 使用统一的路径配置
            self.workspace_base = str(get_workspaces_path())
        else:
            self.workspace_base = workspace_base

    def get_work_history(self, work_id: str) -> Dict:
        """获取指定工作的聊天记录"""
        history_file = self._get_history_file_path(work_id)

        if not os.path.exists(history_file):
            return self._create_default_history(work_id)

        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取聊天记录失败 {work_id}: {e}")
            raise

    @serialized_update
    def save_message(self, work_id: str, role: str, content: str, metadata: Optional[Dict] = None):
        """保存新消息到JSON文件（兼容旧格式）"""
        history = self.get_work_history(work_id)

        # 使用高精度时间戳，确保消息顺序正确
        timestamp = datetime.now().isoformat()

        message = {
            "id": len(history.get("messages", [])) + 1,
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "metadata": metadata or {}
        }

        history["messages"].append(message)
        self._save_history(work_id, history)
        logger.info(
            f"消息已保存 {work_id}: {role}, ID: {message['id']}, 时间: {timestamp}")

    @serialized_update
    def save_json_card_message(self, work_id: str, role: str, content: str,
                               json_blocks: List[Dict] = None, metadata: Optional[Dict] = None):
        """保存JSON卡片格式的消息"""
        history = self.get_work_history(work_id)

        timestamp = datetime.now().isoformat()
        message_id = str(uuid.uuid4())

        # 构建JSON卡片格式的消息
        message = {
            "id": message_id,
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "metadata": metadata or {},
            "json_blocks": json_blocks or [],
            "message_type": "json_card" if json_blocks else "text"
        }

        history["messages"].append(message)
        self._save_history(work_id, history)
        logger.info(
            f"JSON卡片消息已保存 {work_id}: {role}, ID: {message_id}, 块数: {len(json_blocks or [])}")

    @serialized_update
    def add_json_block_to_message(self, work_id: str, message_id: str, json_block: Dict):
        """向指定消息添加JSON块"""
        history = self.get_work_history(work_id)

        # 查找消息
        for message in history["messages"]:
            if message.get("id") == message_id:
                if "json_blocks" not in message:
                    message["json_blocks"] = []
                message["json_blocks"].append(json_block)
                message["message_type"] = "json_card"
                self._save_history(work_id, history)
                logger.info(
                    f"JSON块已添加到消息 {work_id}: {message_id}, 类型: {json_block.get('type')}")
                return True

        logger.warning(f"未找到消息 {work_id}: {message_id}")
        return False

    @serialized_update
    def update_context(self, work_id: str, context_updates: Dict):
        """更新工作上下文"""
        history = self.get_work_history(work_id)
        history["context"].update(context_updates)
        self._save_history(work_id, history)
        logger.info(f"上下文已更新 {work_id}")

    def get_messages(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """获取消息列表，按时间顺序和ID顺序排列"""
        history = self.get_work_history(work_id)
        messages = history.get("messages", [])

        # 确保消息按时间戳排序
        messages.sort(key=lambda x: x.get('timestamp', ''))

        if limit:
            return messages[-limit:]  # 返回最新的limit条消息
        return messages

    @serialized_update
    def clear_history(self, work_id: str):
        """清空聊天记录"""
        history = self._create_default_history(work_id)
        self._save_history(work_id, history)
        logger.info(f"聊天记录已清空 {work_id}")

    def _get_history_file_path(self, work_id: str) -> str:
        """获取聊天记录文件路径"""
        return os.path.join(self.workspace_base, work_id, "chat_history.json")

    def _create_default_history(self, work_id: str) -> Dict:
        """创建默认聊天记录结构"""
        return {
            "work_id": work_id,
            "session_id": f"{work_id}_session",
            "messages": [],
            "context": {
                "current_topic": "",
                "generated_files": [],
                "workflow_state": "created"
            },
            "created_at": datetime.now().isoformat(),
            "version": "2.0"  # 标记新版本格式
        }

    def _save_history(self, work_id: str, history: Dict):
        """保存聊天记录到文件"""
        # 确保目录存在
        work_dir = os.path.join(self.workspace_base, work_id)
        os.makedirs(work_dir, exist_ok=True)

        history_file = self._get_history_file_path(work_id)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=work_dir,
                                             prefix='.chat_history-', delete=False) as f:
                temporary = f.name
                json.dump(history, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, history_file)
            directory = os.open(work_dir, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if temporary and os.path.exists(temporary): os.unlink(temporary)

    @contextmanager
    def _history_lock(self, work_id):
        path = os.path.abspath(self._get_history_file_path(work_id))
        with _history_locks_guard:
            lock = _history_locks.setdefault(path, threading.RLock())
        with lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + '.lock', 'a') as guard:
                fcntl.flock(guard, fcntl.LOCK_EX)
                try: yield
                finally: fcntl.flock(guard, fcntl.LOCK_UN)

    @serialized_update
    def upsert_reply(self, work_id, reply):
        history = self.get_work_history(work_id)
        messages = history.setdefault('messages', [])
        for index, message in enumerate(messages):
            if message.get('id') == reply['id']:
                if message.get('metadata', {}).get('revision', 0) > reply['metadata']['revision']:
                    return
                messages[index] = reply
                break
        else:
            messages.append(reply)
        self._save_history(work_id, history)

    @serialized_update
    def interrupt_reply(self, work_id, reply_id, expected_revision):
        history = self.get_work_history(work_id)
        for reply in history.get('messages', []):
            metadata = reply.get('metadata', {})
            if reply.get('id') != reply_id or metadata.get('status') != 'running' or metadata.get('revision') != expected_revision:
                continue
            metadata.update(status='interrupted', revision=expected_revision + 1)
            for segment in reply.get('segments', []):
                item = segment.get('execution')
                if item and not item.get('finished_at'): item['status'] = 'interrupted'
            self._save_history(work_id, history)
            return reply
        return None

    @serialized_update
    def migrate_old_format(self, work_id: str):
        """迁移旧格式的聊天记录到新格式"""
        history = self.get_work_history(work_id)

        # 检查是否需要迁移
        if history.get("version") == "2.0":
            return  # 已经是新格式

        # 迁移消息格式
        old_messages = history.get("messages", [])
        new_messages = []

        for msg in old_messages:
            # Only explicit historical blocks are protocol; preserve all prose.
            json_blocks = msg.get("json_blocks", [])

            new_msg = {
                **msg,
                "id": msg.get("id") or str(uuid.uuid4()),
                "role": msg.get("role", "assistant"),
                "content": msg.get("content", ""),
                "timestamp": msg.get("timestamp", ""),
                "metadata": msg.get("metadata", {}),
                "json_blocks": json_blocks,
                "message_type": "json_card" if json_blocks else "text"
            }
            new_messages.append(new_msg)

        # 更新历史记录
        history["messages"] = new_messages
        history["version"] = "2.0"

        self._save_history(work_id, history)
        logger.info(f"聊天记录格式迁移完成 {work_id}: {len(new_messages)} 条消息")
