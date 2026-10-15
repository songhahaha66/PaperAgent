"""
PaperGraphAgent：状态图版本的入口，接口与 MainAgent 保持一致（构造参数 + async run）

- 每次运行对应 checkpoint 里的一个 thread，状态存在 <workspace>/.system/graph.sqlite；
- 图在"确认建模方案"处 interrupt 时，run 返回待确认内容，用户下一条消息作为回复恢复运行；
- 进程中断后，用户发"继续"会从最后一个完成的节点接着跑。
"""

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from ..judge import JevJudge, LLMJudge, jev_available
from .build import compile_graph
from .context import GraphContext
from .nodes.confirm import is_approval
from .tracing import LLMCallTrace

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = ".system/graph.sqlite"
THREAD_PATH = ".system/graph_thread.json"
CALLS_PATH = ".system/llm_calls.json"
GRAPH_RECURSION_LIMIT = 200
DEFAULT_JEV_MODEL = "jev-1.14.0"


def default_judge(llm):
    """建模方案评审：有 Jev key 用 Jev（一次请求评完所有候选），否则用 LLM 结构化打分"""
    if jev_available():
        return JevJudge(model=os.getenv("JEV_MODEL", DEFAULT_JEV_MODEL))
    return LLMJudge(llm)


class PaperGraphAgent:
    def __init__(
        self,
        llm,
        stream_manager=None,
        workspace_dir: Optional[str] = None,
        work_id: Optional[str] = None,
        template_id: Optional[int] = None,
        codeagent_llm=None,
        output_mode: str = "markdown",
        writer_llm=None,
        judge: Any = "default",
        confirm_models: Optional[bool] = None,
        **options,
    ):
        if not workspace_dir:
            if not work_id:
                raise ValueError("PaperGraphAgent 需要 workspace_dir 或 work_id")
            from config.paths import get_workspace_path

            workspace_dir = str(get_workspace_path(work_id))
        if output_mode not in ("markdown", "latex"):
            logger.warning("PaperGraphAgent 只支持 markdown / latex 输出，按 markdown 处理 output_mode=%s", output_mode)
            output_mode = "markdown"

        self.llm = llm
        self.stream_manager = stream_manager
        self.workspace_dir = str(Path(workspace_dir).resolve())
        self.work_id = work_id
        self.template_id = template_id
        self.codeagent_llm = codeagent_llm
        self.writer_llm = writer_llm
        self.judge = default_judge(llm) if judge == "default" else judge
        if confirm_models is None:
            confirm_models = os.getenv("AI_CONFIRM_MODELS", "").lower() in ("1", "true", "yes")
        if "explore_top_k" not in options and os.getenv("AI_EXPLORE_TOPK"):
            options["explore_top_k"] = int(os.environ["AI_EXPLORE_TOPK"])
        if "design_ahead" not in options and os.getenv("AI_DESIGN_AHEAD"):
            options["design_ahead"] = os.environ["AI_DESIGN_AHEAD"].lower() in ("1", "true", "yes")
        self.options: Dict[str, Any] = {"confirm_models": confirm_models, "output_mode": output_mode, **options}
        self.last_result: Dict[str, Any] = {}

    # ------------------------------------------------------------------

    def _thread_file(self) -> Path:
        return Path(self.workspace_dir) / THREAD_PATH

    def _load_thread(self) -> Optional[str]:
        path = self._thread_file()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("thread_id")
        return None

    def _save_thread(self, thread_id: str, status: str) -> None:
        path = self._thread_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"thread_id": thread_id, "status": status}), encoding="utf-8")

    def _context(self) -> GraphContext:
        return GraphContext.create(
            self.workspace_dir,
            planner_llm=self.llm,
            coder_llm=self.codeagent_llm,
            writer_llm=self.writer_llm,
            judge=self.judge,
            stream_manager=self.stream_manager,
            **self.options,
        )

    async def _emit(self, block_type: str, content: Any) -> None:
        if self.stream_manager is None:
            return
        try:
            await self.stream_manager.send_json_block(block_type, content)
        except Exception as exc:
            logger.warning("推送 %s 失败: %s", block_type, exc)

    # ------------------------------------------------------------------

    async def run(self, user_input: str) -> str:
        ctx = self._context()
        checkpoint = Path(self.workspace_dir) / CHECKPOINT_PATH
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        await self._emit("main_agent_start", f"开始执行: {user_input[:100]}")
        try:
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = compile_graph(saver)
                graph_input, thread_id = await self._prepare(graph, user_input)
                if isinstance(graph_input, dict):
                    ctx.registry.reset()
                config = {"configurable": {"thread_id": thread_id}, "recursion_limit": GRAPH_RECURSION_LIMIT}
                return await self._invoke(graph, graph_input, config, thread_id, ctx)
        except Exception as exc:
            logger.error("PaperGraphAgent 执行失败: %s", exc, exc_info=True)
            error_msg = f"任务执行失败: {exc}"
            await self._emit("main_agent_error", error_msg)
            return error_msg
        finally:
            await ctx.kernels.close()

    async def resume_failed(self) -> str:
        """回到失败子问题求解之前的 checkpoint 接着跑

        网关故障、账号受限这类外部原因会让正在求解的子问题失败（并把状态写进 checkpoint），
        之后的写作也没有意义。外部问题解决后，从这些子问题开始求解之前的 checkpoint 重放，
        已完成的子问题不用重跑。没有失败的子问题时，从最新 checkpoint 接着跑完。
        """
        ctx = self._context()
        checkpoint = Path(self.workspace_dir) / CHECKPOINT_PATH
        thread_id = self._load_thread()
        if not thread_id or not checkpoint.exists():
            raise RuntimeError("没有可恢复的运行记录")
        try:
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = compile_graph(saver)
                config = {"configurable": {"thread_id": thread_id}}
                target = await graph.aget_state(config)
                failed = [
                    sid for sid, sol in (target.values.get("solutions") or {}).items()
                    if sol.get("status") != "completed"
                ]
                if failed:
                    target = None
                    async for snapshot in graph.aget_state_history(config):
                        solved = snapshot.values.get("solutions") or {}
                        if snapshot.next and not any(sid in solved for sid in failed):
                            target = snapshot
                            break
                    if target is None:
                        raise RuntimeError(f"找不到 {', '.join(failed)} 求解之前的 checkpoint")
                    for sid in failed:
                        ctx.registry.drop_subproblem(sid)
                logger.info("从 checkpoint 恢复，重跑 %s，下一步 %s", failed or "无", target.next)
                await self._emit("paper_stage", f"从断点恢复：重新求解 {', '.join(failed)}" if failed else "从断点恢复")
                resume_config = {**target.config, "recursion_limit": GRAPH_RECURSION_LIMIT}
                return await self._invoke(graph, None, resume_config, thread_id, ctx)
        finally:
            await ctx.kernels.close()

    async def _invoke(self, graph, graph_input, config, thread_id: str, ctx: GraphContext) -> str:
        self._save_thread(thread_id, "running")
        trace = LLMCallTrace()
        try:
            result = await graph.ainvoke(graph_input, {**config, "callbacks": [trace]}, context=ctx)
        finally:
            trace.dump(Path(self.workspace_dir) / CALLS_PATH)
        self.last_result = result

        interrupts = result.get("__interrupt__") or []
        if interrupts:
            self._save_thread(thread_id, "interrupted")
            message = interrupts[0].value.get("message", "") if isinstance(interrupts[0].value, dict) else str(interrupts[0].value)
            await self._emit("paper_confirm", message)
            return message

        self._save_thread(thread_id, "done")
        output = self._final_message(result, ctx)
        await self._emit("main_agent_complete", output)
        return output

    async def _prepare(self, graph, user_input: str):
        """决定是恢复已有 thread 还是开新 thread"""
        thread_id = self._load_thread()
        if thread_id:
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            if snapshot.interrupts:
                logger.info("恢复等待确认的 thread %s", thread_id)
                return Command(resume=user_input), thread_id
            if snapshot.next and is_approval(user_input):
                logger.info("从断点继续 thread %s，下一步 %s", thread_id, snapshot.next)
                return None, thread_id
        return {"task": user_input}, uuid.uuid4().hex

    @staticmethod
    def _final_message(result: Dict[str, Any], ctx: GraphContext) -> str:
        analysis = result.get("analysis") or {}
        solutions = result.get("solutions") or {}
        issues = result.get("issues") or []
        written = "paper.md" + ("、paper.tex 和 paper.pdf" if (ctx.workspace / "paper.pdf").exists() and ctx.output_mode == "latex" else "")
        lines = [f"论文《{analysis.get('title', '')}》已写入 {written}。", ""]
        for sp in analysis.get("subproblems") or []:
            status = (solutions.get(sp["id"]) or {}).get("status", "未求解")
            count = len(ctx.registry.for_subproblem(sp["id"]))
            lines.append(f"- {sp['id']} {sp['title']}：{'完成' if status == 'completed' else status}，登记结果 {count} 项")
        lines.append("")
        lines.append(f"校验剩余问题 {len(issues)} 个。" if issues else "引用与数字校验全部通过。")
        return "\n".join(lines)
