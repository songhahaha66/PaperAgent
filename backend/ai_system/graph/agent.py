"""
PaperGraphAgent：状态图流水线的入口（构造参数 + async run），聊天接口和评测共用

- 每次运行对应一个 thread；业务状态在 .system/research.sqlite，执行位置在 .system/graph.sqlite；
- 图在"确认建模方案"处 interrupt 时，run 返回待确认内容，用户下一条消息作为回复恢复运行；
- 进程中断后，用户发"继续"会先补跑失败子问题，再从断点完成写作和导出。
"""

import json
import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from ..judge import JevJudge, LLMJudge, jev_available
from ..research import ENGINE_VERSION
from ..research.store import ResearchStore
from ..research.control import refresh_registry
from ..research.planner import propose_revision, Planner
from .build import compile_graph
from .context import GraphContext
from .nodes.confirm import is_approval
from .tracing import LLMCallTrace,labelled

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
    engine_version = ENGINE_VERSION

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
        if output_mode not in ("markdown", "latex", "word"):
            logger.warning("PaperGraphAgent 只支持 markdown / latex / word 输出，按 markdown 处理 output_mode=%s", output_mode)
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
            if (Path(self.workspace_dir)/'.system/research.sqlite').exists():
                from ..research.runtime import ResearchRuntime
                confirm_models=ResearchRuntime(Path(self.workspace_dir)).policy()['confirm_models']
        if "explore_top_k" not in options and os.getenv("AI_EXPLORE_TOPK"):
            options["explore_top_k"] = int(os.environ["AI_EXPLORE_TOPK"])
        self.options: Dict[str, Any] = {"confirm_models": confirm_models, "output_mode": output_mode, **options}
        if (Path(self.workspace_dir)/'.system/research.sqlite').exists():
            from ..research.runtime import ResearchRuntime
            if ResearchRuntime(Path(self.workspace_dir)).policy()['compact_paper']:
                self.options.setdefault('max_solver_steps',12)
                self.options.setdefault('max_planning_calls',1)
        self.last_result: Dict[str, Any] = {}
        self.waiting_confirm = False

    # ------------------------------------------------------------------

    def _thread_file(self) -> Path:
        return Path(self.workspace_dir) / THREAD_PATH

    def _load_thread(self) -> Optional[str]:
        path = self._thread_file()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("thread_id")
        return None

    def _require_current_thread(self) -> None:
        path = self._thread_file()
        if not path.exists():
            raise ValueError("没有可恢复的研究记录")
        if json.loads(path.read_text(encoding="utf-8")).get("engine_version") != ENGINE_VERSION:
            raise ValueError("该研究记录不支持恢复，请重新研究")

    def _save_thread(self, thread_id: str, status: str) -> None:
        path = self._thread_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"thread_id": thread_id, "status": status,
                                         "engine_version": self.engine_version}), encoding="utf-8")
        temporary.replace(path)

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

    async def run(self, user_input: str, *, new_run: bool = False) -> str:
        ctx = self._context()
        checkpoint = Path(self.workspace_dir) / CHECKPOINT_PATH
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        await self._emit("main_agent_start", f"开始执行: {user_input[:100]}")
        try:
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = compile_graph(saver)
                graph_input, thread_id = ({"task": user_input}, uuid.uuid4().hex) if new_run else await self._prepare(graph, user_input)
                if isinstance(graph_input, dict):
                    ctx.registry.reset()
                ctx.research = ResearchStore(self.workspace_dir, thread_id, experience_scope=ctx.experience_scope)
                config = {"configurable": {"thread_id": thread_id}, "recursion_limit": GRAPH_RECURSION_LIMIT}
                if graph_input is None:
                    return await self._resume_graph(graph, config, thread_id, ctx)
                return await self._invoke(graph, graph_input, config, thread_id, ctx)
        except Exception as exc:
            logger.error("PaperGraphAgent 执行失败: %s", exc, exc_info=True)
            error_msg = f"任务执行失败: {exc}"
            await self._emit("main_agent_error", error_msg)
            return error_msg
        finally:
            await ctx.kernels.close()

    async def revise_research(self, *, command_id: str, research_id: str, base_revision: int,
                              change: str, scope: list[str] | None = None, source_issue:dict|None=None) -> str:
        """Apply an idempotent revision, then selectively resume this research."""
        if research_id != self._load_thread():
            raise ValueError("研究标识已改变，请刷新后重试")
        self._require_current_thread()
        store = ResearchStore(self.workspace_dir, research_id, experience_scope=self.options.get("experience_scope"))
        snapshot = store.snapshot()
        if not snapshot:
            raise ValueError("研究尚未生成可修订任务")
        # Stable command payload even when retrying after the revision committed.
        issue_options={'source_issue':source_issue} if source_issue else {}
        patch = store.load_revision_command(command_id, base_revision, change, scope,**issue_options)
        if patch is None:
            if source_issue:
                from services.chat_services.issue_edits import require_binding
                linked=require_binding(store,source_issue,writing=False)
                if linked['route']!='research' or scope!=[linked['issue']['task_id']]:raise ValueError('源意见必须进入对应任务的定向研究修改')
            if snapshot["revision"] != base_revision:
                raise ValueError("研究版本已改变，请刷新后重试")
            from ..research.runtime import budget_scope
            trace=LLMCallTrace()
            callbacks=getattr(self.llm,'callbacks',None)
            model=self.llm.model_copy(update={'callbacks':[*(callbacks if isinstance(callbacks,list) else []),trace]}) if hasattr(self.llm,'model_copy') else self.llm
            try:
                with budget_scope(Path(self.workspace_dir), research_id),labelled('amendment'):
                    requirements = await Planner(model).amendment(snapshot, change, scope)
            except Exception:
                self._save_thread(research_id,'failed')
                raise
            finally:trace.dump(Path(self.workspace_dir)/'.system/planner_llm_calls.json')
            patch = propose_revision(snapshot, command_id, base_revision, change, scope, requirements)
            if source_issue:require_binding(store,source_issue,writing=False)
        store.apply_revision_command(command_id, base_revision, change, scope, patch,**issue_options)
        if source_issue:
            with store._transaction() as db:
                key='issue-research:'+command_id
                if not db.execute('SELECT 1 FROM commands WHERE research_id=? AND id=?',(research_id,key)).fetchone():
                    from ..research.store import digest
                    link={'source_issue':source_issue,'revision_command_id':command_id,'base_revision':base_revision,'scope':scope}
                    db.execute('INSERT INTO commands VALUES (?,?,?,?)',(research_id,key,digest(link),json.dumps(link,ensure_ascii=False)))
                    store._save(db,store._read(db),'issue_research_revision_linked',link)
        current = store.snapshot()
        if current["commit_seq"] == snapshot["commit_seq"] and current["outcome"] in {"completed", "needs_attention"}:
            # A delivered command retry must not republish the paper or rerun export.
            checkpoint = Path(self.workspace_dir) / CHECKPOINT_PATH
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = compile_graph(saver)
                saved = await graph.aget_state({"configurable": {"thread_id": research_id}})
            self.last_result = saved.values
            ctx = self._context()
            ctx.research = store
            refresh_registry(ctx)
            output = self._final_message(self.last_result, ctx)
            await self._emit("main_agent_complete", output)
            return output
        return await self.resume_failed()

    async def resume_failed(self) -> str:
        """从当前提交状态恢复；重新领取未验收任务，不回放历史结果。"""
        self._require_current_thread()
        ctx = self._context()
        checkpoint = Path(self.workspace_dir) / CHECKPOINT_PATH
        thread_id = self._load_thread()
        if not thread_id or not checkpoint.exists():
            raise RuntimeError("没有可恢复的运行记录")
        try:
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = compile_graph(saver)
                ctx.research = ResearchStore(self.workspace_dir, thread_id, experience_scope=ctx.experience_scope)
                config = {"configurable": {"thread_id": thread_id}}
                return await self._resume_graph(graph, config, thread_id, ctx)
        finally:
            await ctx.kernels.close()

    async def _resume_graph(self, graph, config, thread_id: str, ctx: GraphContext) -> str:
        """聊天和评测共用：业务提交决定有效工作，checkpoint 只记录执行位置。"""
        if ctx.research.snapshot() is not None:
            ctx.research.recover()
            refresh_registry(ctx)
            await graph.aupdate_state(config, {"research_blocked": False}, as_node="solve_join")
        return await self._invoke(graph, None, {**config, "recursion_limit": GRAPH_RECURSION_LIMIT}, thread_id, ctx)

    async def _invoke(self, graph, graph_input, config, thread_id: str, ctx: GraphContext) -> str:
        self._save_thread(thread_id, "running")
        trace = LLMCallTrace()
        before = await graph.aget_state(config)
        log_offset = len(before.values.get("log") or [])
        try:
            from ..research.runtime import budget_scope
            with budget_scope(Path(self.workspace_dir),thread_id):
                result = await graph.ainvoke(graph_input, {**config, "callbacks": [trace]}, context=ctx)
        except asyncio.CancelledError:
            self._save_thread(thread_id, 'paused')
            raise
        except Exception as exc:
            self._save_thread(thread_id, "failed")
            for stage, status in list(ctx.stage_status.items()):
                if status == "in_progress":
                    try:
                        await ctx.set_stage(stage, "blocked", f"执行失败：{exc}")
                    except Exception:
                        logger.warning("更新失败阶段状态失败", exc_info=True)
            raise
        finally:
            trace.dump(Path(self.workspace_dir) / CALLS_PATH)
        self.last_result = result

        interrupts = result.get("__interrupt__") or []
        self.waiting_confirm = bool(interrupts)
        if interrupts:
            self._save_thread(thread_id, "interrupted")
            message = interrupts[0].value.get("message", "") if isinstance(interrupts[0].value, dict) else str(interrupts[0].value)
            await self._emit("paper_confirm", message)
            return message

        blocked = result.get("research_blocked", False)
        self._save_thread(thread_id, "blocked" if blocked else "done")
        output = self._final_message({**result, "log": (result.get("log") or [])[log_offset:]}, ctx)
        await self._emit("main_agent_complete", output)
        return output

    async def _prepare(self, graph, user_input: str):
        """决定是恢复已有 thread 还是开新 thread"""
        thread_id = self._load_thread()
        if thread_id:
            self._require_current_thread()
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            if snapshot.interrupts:
                logger.info("恢复等待确认的 thread %s", thread_id)
                return Command(resume=user_input), thread_id
            if is_approval(user_input) and (snapshot.next or snapshot.values.get("research_blocked")):
                logger.info("从断点继续 thread %s，下一步 %s", thread_id, snapshot.next)
                return None, thread_id
        return {"task": user_input}, uuid.uuid4().hex

    @staticmethod
    def _final_message(result: Dict[str, Any], ctx: GraphContext) -> str:
        if result.get("research_blocked"):
            if ctx.stage_status.get("export") == "blocked":
                source = "paper.tex" if ctx.output_mode == "latex" else "paper.md"
                target = "PDF 编译" if ctx.output_mode == "latex" else "Word 导出"
                return f"论文源码 {source} 已保存，{target}未完成：{ctx.stage_notes.get('export', '')}。修正源码或导出环境后可继续。"
            return "研究已暂停：存在未关闭的研究或审查问题，或任务尚未通过验收。已有结果已保留，可查看问题记录、修改要求或继续重试。"
        analysis = result.get("analysis") or {}
        solutions = result.get("solutions") or {}
        issues = result.get("issues") or []
        source = "paper.tex" if ctx.output_mode == "latex" else "paper.md"
        lines = [f"论文《{analysis.get('title', '')}》已写入 {result.get('paper_path') or source}。", ""]
        if result.get("artifact_conflict"):
            lines.append(f"{source} 存在人工修改，已保留人工稿和自动新稿，等待合并。")
        exported = [line for line in result.get("log") or [] if line.startswith("export:")]
        if exported:
            lines[0] += f"导出：{exported[-1].split(':', 1)[1].strip()}。"
        for sp in analysis.get("subproblems") or []:
            status = (solutions.get(sp["id"]) or {}).get("status", "未求解")
            count = len(ctx.registry.for_subproblem(sp["id"]))
            lines.append(f"- {sp['id']} {sp['title']}：{'完成' if status == 'completed' else status}，登记结果 {count} 项")
        lines.append("")
        lines.append(f"校验剩余问题 {len(issues)} 个。" if issues else "引用与数字校验全部通过。")
        return "\n".join(lines)
