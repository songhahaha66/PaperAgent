"""
PaperAgent：会话执行入口，聊天与评测共用同一个 Agent 主循环

- 研究资料在 research.sqlite，可恢复会话与工具回执在 agent.sqlite；
- 暂停、等待用户、恢复和结束均由共用循环处理；
- 恢复观察已提交成果与行动记录，由同一控制器选择下一步，不自动改写原目标。
"""
from services.file_services.workspace_layout import workspace_path

import json
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from langsmith import trace as remote_trace

from ..config.observability import research_tracing
from ..research import ENGINE_VERSION
from ..research.store import ResearchStore
from ..research.control import refresh_registry
from ..research.planner import propose_revision, Planner
from ..graph.context import GraphContext
from ..graph.tracing import LLMCallTrace,labelled

logger = logging.getLogger(__name__)

THREAD_PATH = ".system/graph_thread.json"
CALLS_PATH = ".system/llm_calls.json"


class PaperAgent:
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
        **options,
    ):
        if not workspace_dir:
            if not work_id:
                raise ValueError("PaperAgent 需要 workspace_dir 或 work_id")
            from config.paths import get_workspace_path

            workspace_dir = str(get_workspace_path(work_id))
        if output_mode not in ("markdown", "latex", "word"):
            logger.warning("PaperAgent 只支持 markdown / latex / word 输出，按 markdown 处理 output_mode=%s", output_mode)
            output_mode = "markdown"

        from ..research.runtime import ResearchRuntime
        ResearchRuntime(Path(workspace_dir)).policy()
        self.llm = llm
        self.stream_manager = stream_manager
        self.workspace_dir = str(Path(workspace_dir).resolve())
        self.work_id = work_id
        self.template_id = template_id
        self.codeagent_llm = codeagent_llm
        self.options: Dict[str, Any] = {"output_mode": output_mode, **options}
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
        options = dict(self.options)
        options.setdefault('sandbox_work_id', self.work_id)
        return GraphContext.create(
            self.workspace_dir,
            planner_llm=self.llm,
            coder_llm=self.codeagent_llm,
            stream_manager=self.stream_manager,
            **options,
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
        if action=='resume':
            if new_run:raise ValueError('恢复操作不能新建研究')
            return await self.resume_failed()
        ctx=self._context()
        await self._emit('main_agent_start',f'开始执行: {user_input[:100]}')
        try:
            if action=='answer':
                self._require_current_thread();thread_id=self._load_thread()
                ctx.research=ResearchStore(self.workspace_dir,thread_id,experience_scope=ctx.experience_scope)
                pending=ctx.research.snapshot().get('pending_input')
                if not pending:raise ValueError('当前没有等待补充信息的研究')
                from ..research.clarifications import receive
                receive(ctx.research,pending['id'],user_input)
            elif action=='run':
                if self._load_thread() and not new_run:raise ValueError('已有研究，请明确恢复、回答待补充信息或重新研究')
                thread_id=uuid.uuid4().hex
                ctx.research=ResearchStore(self.workspace_dir,thread_id,experience_scope=ctx.experience_scope)
                ctx.research.initialize(user_input,output_mode=ctx.output_mode);ctx.registry.reset()
            else:raise ValueError('不支持的研究操作')
            return await self._invoke(thread_id,ctx)
        except Exception as exc:
            logger.error('PaperAgent 执行失败: %s',exc,exc_info=True)
            error_msg=f'任务执行失败: {exc}'
            await self._emit('main_agent_error',error_msg)
            return error_msg
        finally:await ctx.kernels.disconnect()

    async def revise_research(self, *, command_id: str, research_id: str, base_revision: int,
                              change: str, scope: list[str] | None = None, source_issue:dict|None=None,
                              replace_pending: bool = False, paper_context: dict | None = None) -> str:
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
        pending = snapshot.get('pending_input')
        if pending and not replace_pending:
            raise ValueError('当前等待必要信息，请先回答待补充问题')
        if not snapshot['experiments']:
            if source_issue:raise ValueError('历史实验意见请使用对应实验的修订入口')
            store.amend_questions(command_id, base_revision, change, scope)
        else:
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
        return await self.send_message(change, message_id='revision:' + command_id, replace_pending=True,
                                       paper_context=paper_context)

    async def resume_failed(self) -> str:
        self._require_current_thread()
        ctx=self._context();thread_id=self._load_thread()
        ctx.research=ResearchStore(self.workspace_dir,thread_id,experience_scope=ctx.experience_scope)
        state=ctx.research.snapshot()
        if state is None:raise ValueError('研究状态不存在，不能从会话重建业务数据')
        if state.get('pending_input'):raise ValueError('当前等待必要信息，请先回答待补充问题')
        try:
            ctx.research.recover()
            if state['outcome'] not in {'completed','needs_attention'} or state.get('conversation_input'):
                from ..research.session import resume
                resume(ctx.research)
            return await self._invoke(thread_id,ctx)
        finally:await ctx.kernels.disconnect()

    async def send_message(self, message: str, *, question_id: str | None = None,
                           message_id: str | None = None, history: list | None = None,
                           replace_pending: bool = False, paper_context: dict | None = None) -> str:
        """Append a durable user turn to the existing research Agent session."""
        self._require_current_thread()
        store = ResearchStore(self.workspace_dir, self._load_thread())
        if message_id:
            from contextlib import closing
            with closing(store._connect()) as db:
                row = db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',
                                 (store.research_id, 'conversation:' + message_id)).fetchone()
            if row:
                receipt = json.loads(row[0])
                if receipt['message'] != message:
                    raise ValueError('消息标识已被其他内容使用')
                if receipt['reply'] is not None:
                    await self._emit('main_agent_complete', receipt['reply'])
                    return receipt['reply']
                # The turn has already scheduled an action, e.g. an export.
                # Continue that action without injecting the request again.
                output = await self.resume_failed()
                self._save_message_receipt(store, message_id, message, output)
                return output
        with store._transaction() as db:
            state = store._read(db)
            pending_turn = state.get('conversation_input')
            previous_turn = None
            if pending_turn and (not message_id or pending_turn['id'] != message_id):
                if not replace_pending:
                    raise ValueError('上一条消息尚未处理完成，请先恢复当前任务')
                previous_turn = {k: pending_turn[k] for k in ('id', 'message', 'outcome') if k in pending_turn}
                pending_turn = None
            if question_id is not None and (question_id not in state['questions'] or state['questions'][question_id].get('retired')):
                raise ValueError('问题范围已改变，请刷新后重试')
            if not pending_turn:
                # The message router has chosen new work. Preserve the old
                # action in the turn but do not replay it ahead of that request.
                state['conversation_input'] = {'id': message_id or 'user:' + uuid.uuid4().hex,
                    'message': message, 'question_id': question_id, 'outcome': state['outcome'],
                    'paper_context': paper_context,
                    'history': [{'role': m['role'], 'content': str(m.get('content', ''))[-2000:]}
                                for m in (history or [])[-8:] if m.get('role') in {'user', 'assistant'}],
                    'previous_turn': previous_turn,
                    'deferred_action': {k: v for k, v in (state.get('action') or {}).items()
                                        if k in {'id', 'capability', 'status', 'target', 'message'}}
                                       if (state.get('action') or {}).get('status') != 'done' else None}
                state['action'] = None
                if replace_pending:state['pending_input']=None
                state.pop('last_reply',None)
                state['publication'] = None
                state['outcome'] = 'running'
                store._save(db, state, 'conversation_input', state['conversation_input'])
        output = await self.resume_failed()
        if message_id:
            self._save_message_receipt(store, message_id, message, output)
        return output

    @staticmethod
    def _save_message_receipt(store, message_id, message, reply):
        from ..research.store import digest
        with store._transaction() as db:
            db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                (store.research_id, 'conversation:' + message_id, digest(message),
                 json.dumps({'message': message, 'reply': reply}, ensure_ascii=False)))

    async def _invoke(self,thread_id,ctx):
        from ..research.runtime import budget_scope,ResearchRuntime
        from ..research.session import run_research
        self._save_thread(thread_id,'running');trace=LLMCallTrace()
        from .loop import MODEL_CALLBACKS
        trace_token=MODEL_CALLBACKS.set([trace])
        try:
            with research_tracing(self.work_id or Path(self.workspace_dir).name,thread_id,'research',
                                  output_mode=self.options['output_mode'],engine_version=self.engine_version), \
                    budget_scope(Path(self.workspace_dir),thread_id) as runtime:
                runtime.note_stop('running','研究执行中')
                result=await run_research(ctx)
        except asyncio.CancelledError:
            self._save_thread(thread_id,'paused');raise
        except Exception:
            runtime=ResearchRuntime(Path(self.workspace_dir),thread_id)
            reason=runtime.stop_reason()
            if not reason or reason.get('kind')=='running':runtime.note_stop('failed','研究执行失败，会话与工具回执已保存。')
            self._save_thread(thread_id,'failed');raise
        finally:
            MODEL_CALLBACKS.reset(trace_token)
            trace.dump(workspace_path(Path(self.workspace_dir),CALLS_PATH))
        self.last_result=self._result(ctx)
        self.waiting_confirm=result['status']=='waiting'
        if self.waiting_confirm:
            self._save_thread(thread_id,'interrupted')
            message=ctx.research.snapshot()['pending_input']['message']
            await self._emit('paper_confirm',message)
            return message
        self._save_thread(thread_id,'paused' if result['status']=='paused' else 'done')
        output=self._final_message(self.last_result,ctx)
        await self._emit('main_agent_complete',output)
        return output

    @staticmethod
    def _result(ctx):
        """Transient compatibility view for callers, never checkpointed."""
        from ..research.control import graph_view
        from ..research.runs import run_context
        state = ctx.research.snapshot()
        return {**graph_view(state), 'runs': run_context(ctx.research), 'exports': state.get('exports', []),
                'reply': state.get('last_reply'),
                'paper_path': state.get('paper_version') if state.get('paper_conflict') else None,
                'artifact_conflict': state.get('paper_conflict', False),
                'delivery_stages': state.get('delivery_stages', {}),
                'research_blocked': state['outcome'] not in {'completed', 'needs_attention'}}

    @staticmethod
    def _final_message(result: Dict[str, Any], ctx: GraphContext) -> str:
        if result.get('reply'):
            return result['reply']
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
        lines.append(f"另有 {len(issues)} 条待核实意见。" if issues else "正文与交付文件已保存。")
        return "\n".join(lines)
