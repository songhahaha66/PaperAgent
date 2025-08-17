"""
JSON格式聊天记录管理器
负责管理work对应的聊天记录JSON文件
"""

import json
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ChatHistoryManager:
    """管理JSON格式的聊天记录"""
    
    def __init__(self, workspace_base: str = "../pa_data/workspaces"):
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
            return self._create_default_history(work_id)
    
    def save_message(self, work_id: str, role: str, content: str, metadata: Optional[Dict] = None):
        """保存新消息到JSON文件"""
        history = self.get_work_history(work_id)
        
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        
        history["messages"].append(message)
        self._save_history(work_id, history)
        logger.info(f"消息已保存 {work_id}: {role}")
    
    def update_context(self, work_id: str, context_updates: Dict):
        """更新工作上下文"""
        history = self.get_work_history(work_id)
        history["context"].update(context_updates)
        self._save_history(work_id, history)
        logger.info(f"上下文已更新 {work_id}")
    
    def get_messages(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """获取消息列表"""
        history = self.get_work_history(work_id)
        messages = history.get("messages", [])
        
        if limit:
            return messages[-limit:]
        return messages
    
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
            "created_at": datetime.now().isoformat()
        }
    
    def _save_history(self, work_id: str, history: Dict):
        """保存聊天记录到文件"""
        # 确保目录存在
        work_dir = os.path.join(self.workspace_base, work_id)
        os.makedirs(work_dir, exist_ok=True)
        
        # 保存到文件
        history_file = self._get_history_file_path(work_id)
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
