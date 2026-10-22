"""
PaperGraphAgent：状态图流水线的入口（构造参数 + async run），聊天接口和评测共用

- 每次运行对应一个 thread；业务状态在 .system/research.sqlite，执行位置在 .system/graph.sqlite；
- 仅在缺少必要信息时 interrupt；用户回复进入同一研究循环；
- 恢复观察已提交成果与行动记录，由同一控制器选择下一步，不自动改写原目标。
"""
from services.file_services.workspace_layout import workspace_path

import json
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import trace as remote_trace

from ..config.observability import research_tracing
from ..research import ENGINE_VERSION
from ..research.store import ResearchStore
from ..research.control import refresh_registry
from ..research.planner import propose_revision, Planner
from .build import compile_graph
from .context import GraphContext
from .tracing import LLMCallTrace,labelled

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = ".system/graph.sqlite"
THREAD_PATH = ".system/graph_thread.json"
CALLS_PATH = ".system/llm_calls.json"
GRAPH_RECURSION_LIMIT = 200


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

        from ..research.runtime import ResearchRuntime
        ResearchRuntime(Path(workspace_dir)).policy()
        self.llm = llm
        self.stream_manager = stream_manager
        self.workspace_dir = str(Path(workspace_dir).resolve())
        self.work_id = work_id
        self.template_id = template_id
        self.codeagent_llm = codeagent_llm
        self.writer_llm = writer_llm
        self.options: Dict[str, Any] = {"output_mode": output_mode, **options}
        if (workspace_path(Path(self.workspace_dir), '.system/research.sqlite')).exists():
            from ..research.runtime import ResearchRuntime
            if ResearchRuntime(Path(self.workspace_dir)).policy()['compact_paper']:
                self.options.setdefault('max_solver_steps',12)
        self.last_result: Dict[str, Any] = {}
        self.waiting_confirm = False

    # ------------------------------------------------------------------

    def _thread_file(self) -> Path:
        return workspace_path(Path(self.workspace_dir), THREAD_PATH)

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

    async def run(self, user_input: str, *, new_run: bool = False, action: Literal['run', 'answer', 'resume'] = 'run') -> str:
        if action == 'resume':
            if new_run:
                raise ValueError('恢复操作不能新建研究')
            return await self.resume_failed()
        ctx = self._context()
        checkpoint = workspace_path(Path(self.workspace_dir), CHECKPOINT_PATH)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        await self._emit("main_agent_start", f"开始执行: {user_input[:100]}")
        try:
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = compile_graph(saver)
                graph_input, thread_id = await self._prepare(graph, user_input, action=action, new_run=new_run)
                if isinstance(graph_input, dict):
                    ctx.registry.reset()
                ctx.research = ResearchStore(self.workspace_dir, thread_id, experience_scope=ctx.experience_scope)
                config = {"configurable": {"thread_id": thread_id}, "recursion_limit": GRAPH_RECURSION_LIMIT}
                if graph_input is None:
                    return await self._resume_graph(graph, config, thread_id, ctx)
                ctx.research.initialize(user_input, output_mode=ctx.output_mode)
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
        if not change.strip():
            raise ValueError('修改内容不能为空')
        if research_id != self._load_thread():
            raise ValueError("研究标识已改变，请刷新后重试")
        self._require_current_thread()
        store = ResearchStore(self.workspace_dir, research_id, experience_scope=self.options.get("experience_scope"))
        snapshot = store.snapshot()
        if not snapshot:
            raise ValueError("研究尚未生成可修订任务")
        pending = snapshot.get('action') or {}
        if pending.get('capability') == 'ask' and pending.get('status') == 'waiting':
            raise ValueError('当前等待必要信息，请先回答待补充问题')
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
                with research_tracing(self.work_id or Path(self.workspace_dir).name, research_id, 'revise',
                                      command_id=command_id, base_revision=base_revision), \
                        remote_trace('PaperAgent.revise', inputs={'change': change, 'scope': scope}), \
                        budget_scope(Path(self.workspace_dir), research_id), labelled('amendment'):
                    requirements = await Planner(model).amendment(snapshot, change, scope)
            except Exception:
                self._save_thread(research_id,'failed')
                raise
            finally:trace.dump(workspace_path(Path(self.workspace_dir), '.system/planner_llm_calls.json'))
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
            ctx = self._context()
            ctx.research = store
            refresh_registry(ctx)
            self.last_result = self._result(ctx)
            output = self._final_message(self.last_result, ctx)
            await self._emit("main_agent_complete", output)
            return output
        return await self.resume_failed()

    async def resume_failed(self) -> str:
        """从当前提交状态恢复；重新领取未验收任务，不回放历史结果。"""
        self._require_current_thread()
        ctx = self._context()
        checkpoint = workspace_path(Path(self.workspace_dir), CHECKPOINT_PATH)
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
        business = ctx.research.snapshot()
        pending = (business or {}).get('action') or {}
        if pending.get('capability') == 'ask' and pending.get('status') == 'waiting':
            raise ValueError('当前等待必要信息，请先回答待补充问题')
        if business is None:
            raise ValueError('研究状态不存在，不能从 checkpoint 恢复业务数据')
        if ctx.research.snapshot() is not None:
            ctx.research.recover()
            from ..research.loop import resume
            resume(ctx.research,planning_limit=ctx.max_planning_calls)
            refresh_registry(ctx)
            from .checkpoint import discard_business_mirror
            await discard_business_mirror(graph, config)
            await graph.aupdate_state(config, {"research_blocked": False}, as_node="solve_join")
        return await self._invoke(graph, None, {**config, "recursion_limit": GRAPH_RECURSION_LIMIT}, thread_id, ctx)

    async def _invoke(self, graph, graph_input, config, thread_id: str, ctx: GraphContext) -> str:
        self._save_thread(thread_id, "running")
        trace = LLMCallTrace()
        try:
            from ..research.runtime import budget_scope
            operation = 'resume' if graph_input is None else 'research'
            with research_tracing(self.work_id or Path(self.workspace_dir).name, thread_id, operation,
                                  output_mode=self.options['output_mode'], engine_version=self.engine_version), \
                    budget_scope(Path(self.workspace_dir),thread_id) as runtime:
                runtime.note_stop('running','研究执行中')
                callbacks = config.get('callbacks') or []
                callbacks = [*callbacks, trace] if isinstance(callbacks, list) else callbacks.copy()
                if not isinstance(callbacks, list):
                    callbacks.add_handler(trace)
                result = await graph.ainvoke(graph_input, {**config, 'run_name': f'PaperAgent.{operation}',
                                                          'callbacks': callbacks}, context=ctx)
        except asyncio.CancelledError:
            self._save_thread(thread_id, 'paused')
            raise
        except Exception as exc:
            from ..research.runtime import ResearchRuntime
            runtime=ResearchRuntime(Path(self.workspace_dir),thread_id)
            reason=runtime.stop_reason()
            if not reason or reason.get('kind')=='running':runtime.note_stop('failed','研究执行失败，已保存检查点；请查看具体步骤。')
            self._save_thread(thread_id, "failed")
            for stage, status in list(ctx.stage_status.items()):
                if status == "in_progress":
                    try:
                        await ctx.set_stage(stage, "blocked", f"执行失败：{exc}")
                    except Exception:
                        logger.warning("更新失败阶段状态失败", exc_info=True)
            raise
        finally:
            trace.dump(workspace_path(Path(self.workspace_dir), CALLS_PATH))
        self.last_result = self._result(ctx)

        interrupts = result.get("__interrupt__") or []
        self.waiting_confirm = bool(interrupts)
        if interrupts:
            self._save_thread(thread_id, "interrupted")
            message = interrupts[0].value.get("message", "") if isinstance(interrupts[0].value, dict) else str(interrupts[0].value)
            await self._emit("paper_confirm", message)
            return message

        blocked = result.get("research_blocked", False)
        self._save_thread(thread_id, "blocked" if blocked else "done")
        output = self._final_message(self.last_result, ctx)
        await self._emit("main_agent_complete", output)
        return output

    async def _prepare(self, graph, user_input: str, *, action: str = 'run', new_run: bool = False):
        """Consume explicit operations; existing threads never infer a restart."""
        if action not in {'run', 'answer'} or action == 'answer' and new_run:
            raise ValueError('不支持的研究操作')
        thread_id = self._load_thread()
        if action == 'answer':
            self._require_current_thread()
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            if snapshot.interrupts and snapshot.next==('act',):
                logger.info("恢复等待确认的 thread %s", thread_id)
                store=ResearchStore(self.workspace_dir,thread_id)
                with store._transaction() as db:
                    state=store._read(db)
                    pending = (state or {}).get('action') or {}
                    if pending.get('capability') != 'ask' or pending.get('status') != 'waiting':
                        raise ValueError('待补充问题已改变，请重试')
                    message = pending['inputs']['message']
                    state.setdefault('clarifications',[]).append({'ref':'clarification:'+str(len(state.get('clarifications',[]))+1),'question':message,'response':user_input})
                    if state['action']:state['action']['status']='done'
                    store._save(db,state,'clarification_received',{'question':message,'response':user_input})
                return None, thread_id
            raise ValueError('当前没有等待补充信息的研究')
        if thread_id and not new_run:
            raise ValueError('已有研究，请明确恢复、回答待补充信息或重新研究')
        return {"research_blocked": False}, uuid.uuid4().hex

    @staticmethod
    def _result(ctx):
        """Transient compatibility view for callers, never checkpointed."""
        from ..research.control import graph_view
        state = ctx.research.snapshot()
        return {**graph_view(state), 'exports': state.get('exports', []),
                'paper_path': state.get('paper_version') if state.get('paper_conflict') else None,
                'artifact_conflict': state.get('paper_conflict', False),
                'delivery_stages': state.get('delivery_stages', {}),
                'research_blocked': state['outcome'] == 'blocked'}

    @staticmethod
    def _final_message(result: Dict[str, Any], ctx: GraphContext) -> str:
        if result.get("research_blocked"):
            export_stage = (result.get("delivery_stages") or {}).get("export", {})
            if export_stage.get("status") == "blocked":
                source = "paper.tex" if ctx.output_mode == "latex" else "paper.md"
                target = "PDF 编译" if ctx.output_mode == "latex" else "Word 导出"
                return f"论文源码 {source} 已保存，{target}未完成：{export_stage.get('note', '')}。修正源码或导出环境后可继续。"
            reason=ctx.stage_notes.get('solve') or '当前研究尚未满足交付条件，请查看具体步骤的运行记录。'
            return '研究已暂停：'+reason
        analysis = result.get("analysis") or {}
        solutions = result.get("solutions") or {}
        issues = result.get("issues") or []
        source = "paper.tex" if ctx.output_mode == "latex" else "paper.md"
        lines = [f"论文《{analysis.get('title', '')}》已写入 {result.get('paper_path') or source}。", ""]
        if result.get("artifact_conflict"):
            lines.append(f"{source} 存在人工修改，已保留人工稿和自动新稿，等待合并。")
        exported = result.get("exports") or []
        if exported:
            lines[0] += f"导出：{exported[-1]}。"
        for sp in analysis.get("subproblems") or []:
            status = (solutions.get(sp["id"]) or {}).get("status", "未求解")
            count = len(ctx.registry.for_subproblem(sp["id"]))
            lines.append(f"- {sp['id']} {sp['title']}：{'完成' if status == 'completed' else status}，计算结果已保存")
        lines.append("")
        lines.append(f"校验剩余问题 {len(issues)} 个。" if issues else "引用有效性与文稿结构校验已完成。")
        return "\n".join(lines)
