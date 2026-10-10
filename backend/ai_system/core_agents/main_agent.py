"""Chat entry that always runs the v2 state graph."""

import logging
from typing import Optional

from langchain_core.language_models import BaseLanguageModel

from config.paths import get_workspace_path

from ..graph.runner import run_pipeline
from ..judge.factory import get_judge

logger = logging.getLogger(__name__)


class MainAgent:
    """Thin facade. The writing loop lives in `graph.runner`."""

    def __init__(
        self,
        llm: BaseLanguageModel,
        stream_manager=None,
        workspace_dir: str = None,
        work_id: Optional[str] = None,
        template_id: Optional[int] = None,
        codeagent_llm=None,
        output_mode: str = "markdown",
        writer_llm=None,
    ):
        self.llm = llm
        self.stream_manager = stream_manager
        self.work_id = work_id
        self.template_id = template_id
        self.workspace_dir = workspace_dir
        self.output_mode = output_mode
        self.writer_llm = writer_llm
        self.codeagent_llm = codeagent_llm

        if not workspace_dir and work_id:
            self.workspace_dir = str(get_workspace_path(work_id))

        logger.info(
            "MainAgent 使用 v2 状态图，work_id=%s, output_mode=%s",
            work_id,
            output_mode,
        )

    async def run(self, user_input: str) -> str:
        logger.info("MainAgent 开始执行: %s", user_input[:100])
        return await run_pipeline(
            work_id=self.work_id or "work",
            workspace_dir=self.workspace_dir or ".",
            user_message=user_input,
            template_id=self.template_id,
            output_mode=self.output_mode,
            llm=self.llm,
            writer_llm=self.writer_llm,
            stream_manager=self.stream_manager,
            judge=get_judge(self.llm),
            coder_llm=self.codeagent_llm,
        )
