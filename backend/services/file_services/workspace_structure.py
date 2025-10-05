"""
工作空间目录结构统一管理
"""

from pathlib import Path
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class WorkspaceStructureManager:
    """工作空间目录结构统一管理"""

    # 统一的目录结构定义
    WORKSPACE_DIRECTORIES = [
        "code",          # 所有代码文件
        "outputs",       # 所有输出文件
        "logs",          # 所有日志文件
        "temp",          # 临时文件
    ]

    @classmethod
    def create_workspace_structure(cls, workspace_path: Path, work_id: str) -> None:
        """创建统一的工作空间目录结构和初始文件"""
        try:
            # 创建目录结构
            for directory in cls.WORKSPACE_DIRECTORIES:
                dir_path = workspace_path / directory
                dir_path.mkdir(parents=True, exist_ok=True)

            # 创建初始文件
            cls._create_workspace_files(workspace_path, work_id)

            logger.info(f"工作空间目录结构和初始文件创建完成: {workspace_path}")
        except Exception as e:
            logger.error(f"创建工作空间目录和文件失败: {e}")
            raise Exception(f"创建工作空间目录和文件失败: {e}")

    @classmethod
    def _create_workspace_files(cls, workspace_path: Path, work_id: str) -> None:
        """创建工作空间初始文件"""
        # 创建初始元数据文件
        metadata = {
            "work_id": work_id,
            "created_at": str(datetime.now()),
            "status": "created",
            "progress": 0
        }

        metadata_file = workspace_path / "metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        # 创建初始对话历史文件
        chat_history = {
            "work_id": work_id,
            "session_id": f"{work_id}_session",
            "messages": [],
            "context": {
                "current_topic": "",
                "generated_files": [],
                "workflow_state": "created"
            },
            "created_at": str(datetime.now()),
            "version": "2.0"
        }
        chat_file = workspace_path / "chat_history.json"
        with open(chat_file, 'w', encoding='utf-8') as f:
            json.dump(chat_history, f, ensure_ascii=False, indent=2)