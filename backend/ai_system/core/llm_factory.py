from __future__ import annotations

from typing import Optional
from sqlalchemy.orm import Session

from ..config.environment import setup_environment_from_db
from .llm_handler import LLMHandler
from .stream_manager import StreamOutputManager


class LLMFactory:
    """统一创建 LLMHandler 的工厂，封装环境加载与模型选择。"""

    def __init__(self, db: Session):
        self.db = db
        self._env_manager = setup_environment_from_db(db)

    def initialize_system(self, system_type: str) -> None:
        self._env_manager.initialize_system(system_type)

    def create_handler(self, system_type: str, stream_manager: Optional[StreamOutputManager] = None) -> LLMHandler:
        config = self._env_manager.config_manager.get_model_config(system_type)
        return LLMHandler(model=str(config.model_id), stream_manager=stream_manager)

    def get_workspace_path(self, work_id: str) -> str:
        import os
        workspace_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "pa_data",
            "workspaces",
            work_id,
        )
        return workspace_path

    def setup_workspace(self, work_id: str) -> None:
        workspace_path = self.get_workspace_path(work_id)
        self._env_manager.setup_workspace(workspace_path)

    async def run_stream(self, system_type: str, messages, stream_manager: Optional[StreamOutputManager] = None, tools=None):
        handler = self.create_handler(system_type, stream_manager=stream_manager)
        assistant_message, tool_calls = await handler.process_stream(messages, tools)
        return assistant_message, tool_calls

    async def run_sync(self, system_type: str, messages, tools=None):
        handler = self.create_handler(system_type, stream_manager=None)
        assistant_message, tool_calls = await handler.process_sync(messages, tools)
        return assistant_message, tool_calls

 