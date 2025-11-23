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

logger = logging.getLogger(__name__)


class ChatHistoryManager:
    """管理JSON卡片格式的聊天记录"""

    def __init__(self, workspace_base: str = None):
        if workspace_base is None:
            # 使用统一的路径配置
            from config.paths import get_workspaces_path
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
            return self._create_default_history(work_id)

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

    def get_messages_for_frontend(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """获取适合前端渲染的消息格式"""
        messages = self.get_messages(work_id, limit)

        # 转换为前端期望的格式
        frontend_messages = []
        for msg in messages:
            frontend_msg = {
                "id": msg.get("id", ""),
                "role": msg.get("role", "assistant"),
                "content": msg.get("content", ""),
                "datetime": msg.get("timestamp", ""),
                "avatar": self._get_avatar_for_role(msg.get("role", "assistant")),
                "systemType": self._get_system_type_from_metadata(msg.get("metadata", {})),
                "json_blocks": msg.get("json_blocks", []),
                "message_type": msg.get("message_type", "text")
            }
            frontend_messages.append(frontend_msg)

        return frontend_messages

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

        # 保存到文件
        history_file = self._get_history_file_path(work_id)
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _get_avatar_for_role(self, role: str) -> str:
        """根据角色获取头像"""
        avatars = {
            "user": "https://tdesign.gtimg.com/site/avatar.jpg",
            "assistant": "https://api.dicebear.com/7.x/bottts/svg?seed=assistant&backgroundColor=0052d9",
            "system": "https://api.dicebear.com/7.x/bottts/svg?seed=system&backgroundColor=ed7b2f"
        }
        return avatars.get(role, avatars["assistant"])

    def _get_system_type_from_metadata(self, metadata: Dict) -> Optional[str]:
        """从元数据中获取系统类型"""
        system_type = metadata.get("system_type")
        if system_type in ["brain", "code", "writing"]:
            return system_type
        return None

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
            # 解析内容中的JSON块
            json_blocks = self._extract_json_blocks_from_content(
                msg.get("content", ""))

            new_msg = {
                "id": str(uuid.uuid4()),  # 生成新的UUID
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

    def _extract_json_blocks_from_content(self, content: str) -> List[Dict]:
        """从内容中提取JSON块"""
        json_blocks = []
        lines = content.split('\n')
        current_block = ''
        in_json_block = False

        for line in lines:
            if line.strip().startswith('{') and line.strip().endswith('}'):
                # 单行JSON
                try:
                    block = json.loads(line.strip())
                    json_blocks.append(block)
                except:
                    pass
            elif line.strip().startswith('{'):
                # 多行JSON开始
                in_json_block = True
                current_block = line + '\n'
            elif line.strip().endswith('}') and in_json_block:
                # 多行JSON结束
                current_block += line
                try:
                    block = json.loads(current_block)
                    json_blocks.append(block)
                except:
                    pass
                in_json_block = False
                current_block = ''
            elif in_json_block:
                # 多行JSON中间
                current_block += line + '\n'

        return json_blocks
