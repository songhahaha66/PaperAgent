"""
简化版聊天系统API路由
使用JSON存储聊天记录，简化WebSocket逻辑
支持断线重连恢复AI任务
"""
from services.file_services.workspace_layout import workspace_path

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from services.chat_services.conversation_timeline import ReplyWriter, TIMELINE, resolve_executions
from sqlalchemy.orm import Session
import asyncio
import logging
import json
import sqlite3

from database.database import get_db
from auth.auth import get_current_user, verify_token
from services.chat_services.chat_service import ChatService
from services.chat_services.task_manager import task_manager
from ai_system.config.environment import setup_environment_from_db
from ai_system.core_managers.stream_manager import StreamOutputManager, SimpleStreamCallback
from ai_system.graph.agent import PaperGraphAgent
from ai_system.core_handlers.llm_handler import LLMHandler
from langchain_core.messages import HumanMessage
from config.paths import get_workspace_path
from services.chat_services.research_session import (
    research_snapshot, resolve_action, scoped_snapshot, infer_question_scope, answer_research_question, ResearchRevisionCommand,
    ResearchContextPermissionError,
)
from ..utils import route_guard

logger = logging.getLogger(__name__)

def set_work_status(work_id: str, status: str) -> None:
    """生成过程同步到工作状态（列表和页头的"进行中/待确认/已完成"），失败只记日志，不影响任务"""
    db = next(get_db())
    try:
        from services.data_services.crud import update_work_status
        update_work_status(db, work_id, status)
    except Exception as e:
        logger.warning(f"更新工作状态失败 {work_id} -> {status}: {e}")
    finally:
        db.close()


router = APIRouter(prefix="/api/chat", tags=["聊天系统"])

# 全局变量用于存储app实例的引用（在WebSocket中使用）
_app_instance = None

def set_app_instance(app):
    """设置app实例引用，供WebSocket使用"""
    global _app_instance
    _app_instance = app

def get_app_instance():
    """获取app实例引用"""
    return _app_instance


from services.chat_services.connections import ConnectionManager


manager = ConnectionManager()

# 只用于刷新右侧面板的事件，不写进聊天记录
TRANSIENT_BLOCK_TYPES = {'plan_updated', 'file_changed', 'research_snapshot', 'research_state', 'research_activity'}


@router.get("/work/{work_id}/history")
@route_guard
async def get_work_chat_history(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定工作的聊天记录（前端格式）"""
    chat_service = ChatService(db)

    # 验证用户权限（通过session验证）
    session = chat_service.get_session_by_work_id(work_id, current_user_id)
    if not session:
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

    messages = chat_service.get_work_chat_history_for_frontend(work_id)
    context = chat_service.get_work_context(work_id)

    return {
        "work_id": work_id,
        "messages": messages,
        "context": context
    }


class ExecutionReferences(BaseModel):
    reply_id: str = Field(min_length=1, max_length=128)
    segment_ids: list[str] = Field(max_length=100)


@router.post('/work/{work_id}/execution-references')
@route_guard
async def get_execution_references(work_id: str, request: ExecutionReferences,
                                   current_user_id: int = Depends(get_current_user),
                                   db: Session = Depends(get_db)):
    chat_service = ChatService(db)
    session = chat_service.get_session_by_work_id(work_id, current_user_id)
    if not session:
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail='无权限访问')
    reply = next((m for m in chat_service.get_work_chat_history(work_id)
                  if m.get('id') == request.reply_id and m.get('role') == 'assistant'), None)
    if reply is None: raise HTTPException(status_code=404, detail='回复不存在')
    db.close()
    try:
        rows = await asyncio.to_thread(resolve_executions, get_workspace_path(work_id), reply, request.segment_ids)
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail='执行记录暂不可用') from exc
    return {'reply_id': request.reply_id, 'executions': rows}


@router.get("/work/{work_id}/history/raw")
@route_guard
async def get_work_chat_history_raw(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定工作的聊天记录（原始格式）"""
    chat_service = ChatService(db)

    session = chat_service.get_session_by_work_id(work_id, current_user_id)
    if not session:
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

    messages = chat_service.get_work_chat_history(work_id)
    context = chat_service.get_work_context(work_id)

    return {
        "work_id": work_id,
        "messages": messages,
        "context": context
    }


@router.get("/work/{work_id}/history/stats")
@route_guard
async def get_work_chat_statistics(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定工作的聊天统计信息"""
    chat_service = ChatService(db)

    session = chat_service.get_session_by_work_id(work_id, current_user_id)
    if not session:
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

    stats = chat_service.get_chat_statistics(work_id)

    return {
        "work_id": work_id,
        "statistics": stats
    }


@router.get("/work/{work_id}/task-status")
@route_guard
async def get_task_status(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前工作的AI任务状态"""
    # 验证权限
    from services.data_services.crud import get_work
    work = get_work(db, work_id)
    if not work or work.created_by != current_user_id:
        raise HTTPException(status_code=403, detail="无权限访问")

    return task_manager.get_task_status(work_id)


@router.get("/work/{work_id}/research")
async def get_research_snapshot(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from services.data_services.crud import get_work
    work = get_work(db, work_id)
    if not work or work.created_by != current_user_id:
        raise HTTPException(status_code=403, detail="无权限访问")
    try:
        status = work.status
        if status == "in_progress" and task_manager.get_task_status(work_id).get("status") not in {"pending", "running"}:
            status = "paused"  # A persisted running flag cannot revive a lost coordinator.
        db.close()
        return await asyncio.to_thread(research_snapshot, get_workspace_path(work_id), work_id, status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/work/{work_id}/pause")
@route_guard
async def pause_work(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from services.data_services.crud import get_work
    work = get_work(db, work_id)
    if not work or work.created_by != current_user_id:
        raise HTTPException(status_code=403, detail="无权限访问")
    task = task_manager.get_running_task(work_id)
    if not task:
        return {"work_status": work.status}
    original_status = work.status
    db.close()
    from ai_system.research.runtime import ResearchRuntime
    if task.action!='ask':ResearchRuntime(get_workspace_path(work_id)).note_stop('manual_pause','已手动暂停，进度与累计用量保留')
    task_manager.cancel_task(work_id, reason="paused")
    if task._async_task:
        try:
            await asyncio.shield(task._async_task)
        except asyncio.CancelledError:
            pass
    return {"work_status": original_status if task.action == "ask" else "paused"}


async def _execute_chat_task(work_id, user_id, problem, action, snapshot,
                             subproblem_id, revision_command, original_status, task):
    """Own all execution resources independently of the submitting browser."""
    loop = asyncio.get_running_loop()
    ws_callback = None
    try:
        # Read relational metadata in one bounded scope. The Agent and chat
        # history use file-backed storage and must not retain this transaction.
        def init_message_processing():
            db = next(get_db())
            try:
                chat_service = ChatService(db)
                chat_service.create_or_get_work_session(work_id, user_id)
                workspace_dir = str(get_workspace_path(work_id))
                env_manager = setup_environment_from_db(db, workspace_dir)
                workspace_dir = env_manager.get_workspace_dir()
                model_config = env_manager.config_manager.get_model_config('brain', user_id)
                code_config = None if action == 'ask' else env_manager.config_manager.get_model_config('code', user_id)
                writer_config = None if action == 'ask' else env_manager.config_manager.get_model_config('writing', user_id)
                template_id, output_mode = None, 'markdown'
                try:
                    from services.data_services.crud import get_work
                    work = get_work(db, work_id)
                    if work:
                        template_id = getattr(work, 'template_id', None)
                        output_mode = getattr(work, 'output_mode', None) or output_mode
                except Exception as exc:
                    logger.warning('获取工作配置失败: %s', exc)
                return chat_service, workspace_dir, model_config, code_config, writer_config, template_id, output_mode
            finally:
                db.close()

        (chat_service, workspace_dir, model_config, codeagent_model_config,
         writer_model_config, template_id, output_mode) = await loop.run_in_executor(None, init_message_processing)
        # Only file-backed history operations remain after initialization.
        chat_service.db_session = None

        class WebSocketStreamCallback(SimpleStreamCallback):
            def __init__(self, work_id, chat_service):
                super().__init__()
                self.work_id = work_id
                self.writer = None
                self.queue = asyncio.Queue()
                self.sender = asyncio.create_task(self.send_updates())
                self.timer = None
                self.loop = asyncio.get_running_loop()
                from services.chat_services.research_push import ResearchPush
                self.research_push=ResearchPush(
                    lambda:research_snapshot(workspace_dir,self.work_id,'in_progress'),self.publish_snapshot)

            async def publish_snapshot(self, kind, content):
                await self.on_json_block({'type':kind,'content':content},snapshot=True)

            @property
            def json_blocks(self): return self.writer.reply['json_blocks']

            def publish(self, update):
                # Called under ReplyWriter's lock, including from worker threads.
                # Assign replay seq synchronously; one FIFO consumer sends live deltas.
                current = task_manager.get_task(self.work_id)
                if not current or current.task_id != update['chat_task_id']: return
                if self.writer is not None:
                    current.final_content = self.writer.reply['content']
                    current.json_blocks = self.writer.reply['json_blocks'].copy()
                seq = task_manager.add_output(self.work_id, 'reply_update', update)
                self.loop.call_soon_threadsafe(self.queue.put_nowait,
                    ({'type': 'reply_update', 'update': update, 'reply_id': update['reply_id']}, seq))

            async def send_updates(self):
                while True:
                    payload, seq = await self.queue.get()
                    try:
                        current = task_manager.get_task(self.work_id)
                        if self.writer and current and current.task_id == self.writer.reply['metadata']['chat_task_id']:
                            await manager.send(self.work_id, payload, seq)
                    finally: self.queue.task_done()
                    # Give healthy subscribers a turn during a burst of committed updates.
                    await asyncio.sleep(0)

            async def flush_text(self):
                while True:
                    await asyncio.sleep(0.5)
                    self.writer.flush()

            async def on_content(self, content):
                self.writer.text(content)

            async def on_message_complete(self, role, content):
                self.writer.flush()

            async def on_json_block(self, block, *, snapshot=False):
                if not snapshot and block.get('type') in {'research_state','research_activity'}:
                    self.research_push.schedule()
                    return
                if block.get('type')=='research_snapshot' and not snapshot:
                    # A terminal authoritative snapshot must not be followed by an
                    # older in-progress projection still rendering in a worker.
                    await self.research_push.close()
                if block.get('type') in TRANSIENT_BLOCK_TYPES:
                    # Invalidation payloads are never execution rows.
                    with self.writer.lock:
                        current = task_manager.get_task(self.work_id)
                        if not current or current.task_id != self.writer.reply['metadata']['chat_task_id']:
                            return
                        seq = task_manager.add_output(self.work_id, 'json_block', block)
                        self.loop.call_soon_threadsafe(self.queue.put_nowait, ({'type': 'json_block', 'block': block}, seq))
                else:
                    self.writer.block(block)

            async def finalize(self, status, notice=None):
                await self.research_push.close()
                if self.timer: self.timer.cancel()
                self.writer.finish(status, notice)
                # Publish callbacks queued with call_soon_threadsafe must enter FIFO first.
                await asyncio.sleep(0)
                await self.queue.join()
                self.sender.cancel()

        # 创建流式回调和管理器
        ws_callback = WebSocketStreamCallback(work_id, chat_service)
        stream_manager = StreamOutputManager(stream_callback=ws_callback)

        # 创建任务记录；task_id 告诉前端，断线重连时据此续传
        task.action = action
        start_status = original_status if action == "ask" else "in_progress"
        if action != "ask":
            await loop.run_in_executor(None, set_work_status, work_id, start_status)


        # 创建支持多AI提供商的LLM处理器
        llm_handler = LLMHandler(
            model_config=model_config,
            stream_manager=stream_manager,
            **({"max_tokens": 3000} if action == "ask" else {}),
        )

        # 求解阶段的模型（"code" 配置）
        codeagent_llm = None
        if codeagent_model_config and action != "ask":
            from ai_system.core_handlers.llm_providers import create_llm_from_model_config
            try:
                codeagent_llm = create_llm_from_model_config(codeagent_model_config)
                logger.info("求解阶段使用模型: %s", codeagent_model_config.model_id)
            except Exception as e:
                logger.error(f"创建 code 配置模型失败: {e}")
                codeagent_llm = llm_handler.get_llm_instance()
        else:
            logger.info("未提供codeagent配置，使用主LLM")
            codeagent_llm = llm_handler.get_llm_instance()

        # 获取writer的LLM实例（从"writing"配置加载）
        writer_llm = None
        if writer_model_config and action != "ask":
            from ai_system.core_handlers.llm_providers import create_llm_from_model_config
            try:
                writer_llm = create_llm_from_model_config(writer_model_config)
                logger.info("写作阶段使用模型: %s", writer_model_config.model_id)
            except Exception as e:
                logger.error(f"创建 writing 配置模型失败: {e}")
                writer_llm = None
        else:
            logger.info("未提供 writing 配置，写作使用主模型")
            writer_llm = None

        logger.info(f"工作 {work_id} 使用状态图流水线，输出 {output_mode}")
        from services.research_experience import ExperienceScope
        main_agent = None if action == "ask" else PaperGraphAgent(
            llm_handler.get_llm_instance(),
            stream_manager,
            workspace_dir,
            work_id,
            template_id,
            codeagent_llm,
            output_mode=output_mode,
            writer_llm=writer_llm,
            experience_scope=ExperienceScope(user_id,work_id)
        )

        # 立即保存用户消息到持久化存储，确保历史记录顺序正确
        await asyncio.to_thread(chat_service.add_message, work_id, 'user', problem)
        logger.info(f"[PERSISTENCE] 用户消息已立即保存到持久化存储")

        ws_callback.writer = ReplyWriter(chat_service.history_manager, work_id, task.task_id,
                                           workspace_dir, ws_callback.publish)
        task.reply_id = ws_callback.writer.id
        ws_callback.timer = asyncio.create_task(ws_callback.flush_text())
        task_manager.start_task(work_id)
        await manager.send(work_id, {'type': 'start', 'request_id': task.request_id, 'message': '正在回答...' if action == "ask" else '开始研究...',
                                     'task_id': task.task_id, 'reply_id': task.reply_id, 'action': action, 'work_status': start_status, 'started_at': task.started_at})

        async def run_ai_task():
            timeline_token = TIMELINE.set(ws_callback.writer)
            try:
                if action == "ask":
                    history = await loop.run_in_executor(
                        None, chat_service.get_work_chat_history, work_id, 9,
                    )
                    history = [m for m in history if m.get('id') != ws_callback.writer.id][-8:]
                    from config.paths import get_workspace_path
                    from ai_system.research.runtime import budget_scope
                    with budget_scope(get_workspace_path(work_id)):
                        await answer_research_question(
                            llm_handler.get_llm_instance(), stream_manager, snapshot, workspace_dir,
                            problem, history, subproblem_id,
                            destination_url=getattr(model_config, "base_url", "") or "",
                        )
                elif action == "revise_research":
                    await main_agent.revise_research(change=problem, **revision_command.model_dump())
                elif action == "message":
                    await main_agent.send_message(problem, question_id=subproblem_id)
                elif action == "resume":
                    await main_agent.resume_failed()
                else:
                    graph_input = problem
                    await main_agent.run(graph_input, action='answer' if action in {'confirm', 'revise'} else 'run',
                                         new_run=action == 'run' and snapshot.has_research)

                # 停在"确认建模方案"时工作是待确认，不是已完成
                failed_blocks = any(b.get('type') == 'main_agent_error' for b in ws_callback.json_blocks)
                from ai_system.research.views import completed_work_status
                work_status = original_status if action == 'ask' else ('failed' if failed_blocks else
                    await asyncio.to_thread(completed_work_status, workspace_dir))
                if action != "ask":
                    await loop.run_in_executor(None, set_work_status, work_id, work_status)
                    try:
                        evidence = await asyncio.to_thread(research_snapshot, workspace_dir, work_id, work_status)
                        await stream_manager.send_json_block("research_snapshot", evidence.model_dump())
                    except ValueError:
                        logger.warning("研究快照暂不可读，保留任务完成状态: %s", work_id)

                await ws_callback.finalize('failed' if failed_blocks else 'completed')

                # 标记任务完成
                task_manager.complete_task(work_id)

                # 发送完成消息到前端（通过manager获取当前活跃连接）
                try:
                    await manager.send(work_id, {'type': 'complete', 'reply_id': ws_callback.writer.id, 'message': 'AI分析完成', 'work_status': work_status, 'reload_history': True})
                    logger.info(f"[COMPLETE] 完成消息已发送到前端: {work_id}")
                except Exception as e:
                    logger.debug(f"发送完成消息失败: {e}")

                logger.info(f"[PERSISTENCE] AI处理完成，最终消息已保存到持久化存储")

            except asyncio.CancelledError:
                logger.info(f"AI任务被取消: {work_id}")

                task = task_manager.get_task(work_id)
                interrupted_status = original_status if action == "ask" else ('paused' if task and task.cancel_reason == 'paused' else 'failed' if task and task.error else 'cancelled')
                notices = {'paused': '任务已暂停，进度已保存。可从断点继续。',
                           'failed': '任务意外中断，已有结果已保存。可从断点继续。',
                           'cancelled': '任务已取消，已有结果已保存。可从断点继续。'}
                await ws_callback.finalize('cancelled' if action == 'ask' else interrupted_status, '回复已停止。' if action == 'ask' else notices[interrupted_status])

                if action != "ask":
                    await loop.run_in_executor(None, set_work_status, work_id, interrupted_status)
                try:
                    await manager.send(work_id, {'type': 'complete', 'work_status': interrupted_status, 'reload_history': True})
                except Exception:
                    pass
                raise
            except Exception as e:
                logger.error(f"AI任务执行失败: {e}")

                await ws_callback.finalize('failed', '回答失败，请重试。' if action == 'ask' else '任务执行失败，已有结果已保存。可从断点继续。')

                if action != "ask":
                    await loop.run_in_executor(None, set_work_status, work_id, 'failed')
                task_manager.fail_task(work_id, str(e))

                # 通知前端（之前这里调用了不存在的方法，失败时前端会一直停在"生成中"）
                await manager.send(work_id, {'type': 'error', 'reply_id': ws_callback.writer.id, 'reload_history': True, 'message': (str(e) if isinstance(e, ResearchContextPermissionError) else '回答失败，请重试') if action == 'ask' else '任务执行失败，已有记录已保存。', 'work_status': original_status if action == 'ask' else 'failed'})
                raise
            finally:
                TIMELINE.reset(timeline_token)
                if ws_callback.timer: ws_callback.timer.cancel()
                ws_callback.sender.cancel()

        await run_ai_task()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Chat task failed: %s', task.task_id)
        # run_ai_task persists and announces its own failures; handle setup failures here.
        if task.status.value not in {'failed', 'cancelled', 'completed'}:
            if ws_callback and ws_callback.writer:
                await ws_callback.finalize('failed', '任务初始化失败，请重试。')
            task_manager.fail_task(work_id, '任务初始化失败')
            if action != 'ask':
                await asyncio.to_thread(set_work_status, work_id, 'failed')
            await manager.send(work_id, {'type': 'error', 'task_id': task.task_id,
                'message': '任务初始化失败，请重试。', 'reload_history': True,
                'work_status': original_status if action == 'ask' else 'failed'})
    finally:
        if ws_callback:
            if ws_callback.timer: ws_callback.timer.cancel()
            ws_callback.sender.cancel()


@router.websocket("/ws/{work_id}")
async def websocket_chat(websocket: WebSocket, work_id: str):
    """WebSocket聊天接口，支持断线重连恢复"""

    try:
        # 接受连接
        await websocket.accept()

        # 等待认证信息
        auth_data = await websocket.receive_text()
        auth_info = json.loads(auth_data)

        if 'token' not in auth_info:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'message': '缺少认证token'
            }))
            await websocket.close()
            return

        # 验证token
        user_id = verify_token(auth_info['token'])
        if user_id is None:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'message': '无效的认证token'
            }))
            await websocket.close()
            return

        # 验证work权限
        loop = asyncio.get_running_loop()

        def verify_work_permission():
            db = next(get_db())
            try:
                from services.data_services.crud import get_work
                work = get_work(db, work_id)
                return work
            finally:
                db.close()

        work = await loop.run_in_executor(None, verify_work_permission)
        if not work or work.created_by != user_id:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'message': '无权限访问此工作'
            }))
            await websocket.close()
            return

        if work and getattr(work, 'architecture_version', 1) != 2:
            await websocket.send_json({'type': 'error', 'message': '历史项目仅支持导出，请新建项目上传旧资料。'})
            await websocket.close(code=1008)
            return

        # Subscribe before acknowledging auth. The snapshot and replay are queued
        # without network awaits, so live events cannot fall through the join gap.
        manager.register(work_id, websocket)
        task_status = task_manager.get_task_status(work_id)
        running_task = task_manager.get_task(work_id) if task_status.get('status') in {'pending', 'running'} else None
        await manager.send_to(work_id, websocket, {
            'type': 'auth_success', 'message': '认证成功',
            'task': task_status,
            'work_status': getattr(work, 'status', None), 'reload_history': True,
        })
        if running_task:
            same_task = auth_info.get('task_id') == running_task.task_id
            after_seq = auth_info.get('last_seq', 0) if same_task else 0
            await manager.replay(work_id, websocket, running_task, after_seq)

        while True:
            # 接收用户消息
            data = await websocket.receive_text()
            message_data = json.loads(data)
            if not isinstance(message_data, dict):
                await manager.send_to(work_id, websocket, {'type': 'error', 'scope': 'command', 'message': '消息格式错误'})
                continue
            request_id = message_data.get('request_id')
            if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 128):
                await manager.send_to(work_id, websocket, {'type': 'error', 'scope': 'command', 'message': '请求标识无效'})
                continue

            if message_data.get('type') == 'ping':
                await manager.send_to(work_id, websocket, {'type': 'pong'})
                continue
            if 'problem' not in message_data or task_manager.get_active_task(work_id):
                await manager.send_to(work_id, websocket, {'type': 'error', 'scope': 'command',
                    'request_id': request_id,
                    'message': '当前有任务正在执行，请等待完成' if 'problem' in message_data else '消息格式错误'})
                continue

            # Resolve before constructing a graph or changing the work status.
            try:
                problem = message_data["problem"]
                if not isinstance(problem, str) or not problem.strip() or len(problem) > 20000:
                    raise ValueError("请输入有效消息（最多 20000 字符）")
                work = await loop.run_in_executor(None, verify_work_permission)
                if not work or work.created_by != user_id:
                    raise ValueError("无权限访问此工作")
                original_status = work.status
                snapshot = await asyncio.to_thread(
                    research_snapshot, get_workspace_path(work_id), work_id, original_status,
                )
                requested_action = message_data.get('action', 'auto')
                subproblem_id = message_data.get('subproblem_id')
                if subproblem_id is not None and not isinstance(subproblem_id, str):
                    raise ValueError('问题范围无效')
                action = await resolve_action(requested_action, problem, snapshot)
                if action != 'ask':
                    if task_manager.get_active_task(work_id):
                        raise ValueError('当前有任务正在执行，请等待完成')
                    current = await asyncio.to_thread(research_snapshot, get_workspace_path(work_id), work_id, work.status)
                    if (current.research_id, current.commit_seq, current.graph_status) != (snapshot.research_id, snapshot.commit_seq, snapshot.graph_status):
                        raise ValueError('研究状态已改变，请重试')
                revision_command = None
                if action == "revise_research":
                    from pydantic import ValidationError
                    try:
                        revision=message_data.get('revision')
                        revision_command = ResearchRevisionCommand.model_validate(revision)
                    except ValidationError:
                        raise ValueError("增量修改需要有效的命令标识、研究标识和基础版本")
                    revision_ids = ({t["task_id"] for t in snapshot.experiments} if snapshot.experiments else
                                    {q['id'] for q in snapshot.questions if not q.get('retired')})
                    if revision_command.scope is not None and (len(set(revision_command.scope)) != len(revision_command.scope) or
                            not set(revision_command.scope) <= revision_ids):
                        raise ValueError("修改范围包含未知或重复任务")
                    if revision_command.research_id != snapshot.research_id:
                        raise ValueError("研究标识已改变，请刷新后重试")
                if action == "message" and subproblem_id is not None:
                    if subproblem_id not in {q['id'] for q in snapshot.questions if not q.get('retired')}:
                        raise ValueError("问题范围已改变，请刷新后重试")
                    expected=message_data.get('scope_context')
                    if expected is not None and (not isinstance(expected,dict) or
                            expected.get('research_id')!=snapshot.research_id or
                            expected.get('base_revision')!=snapshot.research_revision):
                        raise ValueError('研究版本已改变，请刷新后重试')
                if action == "ask":
                    subproblem_id = subproblem_id or infer_question_scope(problem, snapshot)
                    scoped_snapshot(snapshot, subproblem_id)
            except ValueError as exc:
                await manager.send_to(work_id, websocket, {"type": "error", "scope": "command", 'request_id': request_id, "message": str(exc)})
                continue

            # Reserve before asynchronous initialization; never replace another browser's task.
            try:
                task = task_manager.create_task(work_id, user_id, problem, action=action)
            except ValueError as exc:
                await manager.send_to(work_id, websocket, {'type': 'error', 'scope': 'command', 'request_id': request_id, 'message': str(exc)})
                continue
            task.request_id = request_id
            ai_task = asyncio.create_task(_execute_chat_task(
                work_id, user_id, problem, action, snapshot, subproblem_id,
                revision_command, original_status, task))
            task_manager.set_async_task(work_id, ai_task)

    except WebSocketDisconnect:
        logger.info(f"WebSocket客户端断开连接: {work_id}")
    except Exception as e:
        logger.error(f"WebSocket处理失败: {e}")
        try:
            if websocket.client_state.value == 1:
                await websocket.send_text(json.dumps({
                    'type': 'error',
                    'message': f'处理失败: {str(e)}'
                }))
        except Exception:
            pass
    finally:
        manager.disconnect(work_id, websocket)


@router.post("/work/{work_id}/generate-title")
async def generate_work_title(
    work_id: str,
    request: dict,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """AI生成工作标题并自动更新到数据库"""
    try:
        # 验证用户权限
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

        # 获取用户问题
        question = request.get("question", "")
        if not question:
            raise HTTPException(status_code=400, detail="缺少问题内容")

        # 初始化AI环境
        env_manager = setup_environment_from_db(db)
        model_config = env_manager.config_manager.get_model_config("brain", current_user_id)

        # 构建标题生成提示词
        title_prompt = f"""请根据用户的研究问题生成一个简洁、专业的学术论文标题。
要求：
1. 标题要准确反映研究内容
2. 使用学术化的表达
3. 长度精简，不超过15个字符
4. 只返回标题，不要其他内容

用户问题：{question}

请生成标题："""

        # 调用AI生成标题
        try:
            # 使用新的LangChain方式生成标题
            llm_handler = LLMHandler(
                model_config=model_config
            )
            llm = llm_handler.get_llm_instance()
            # The title request may take minutes; update_work opens a fresh
            # transaction after the model returns.
            db.close()

            # 使用LangChain标准消息格式
            messages = [HumanMessage(content=title_prompt)]

            # 同步调用LLM
            from ai_system.research.runtime import budget_scope
            from ai_system.graph.tracing import labelled,LLMCallTrace
            from config.paths import get_workspace_path
            title_trace=LLMCallTrace()
            try:
                with budget_scope(get_workspace_path(work_id)),labelled('title'):
                    response = await llm.ainvoke(messages,config={'callbacks':[title_trace]})
            finally:title_trace.dump(workspace_path(get_workspace_path(work_id), '.system/title_llm_calls.json'))
            title = response.content

            # 清理标题（移除可能的引号、换行等）
            title = title.strip().strip('"').strip("'").strip()

            # 如果AI生成失败或为空，使用问题作为备选标题
            if not title:
                title = question[:50] if len(question) > 50 else question
                status = "fallback"
            else:
                status = "success"

        except Exception as e:
            logger.error('AI生成标题失败: %s',type(e).__name__)
            # 如果AI生成失败，使用问题作为备选标题
            title = question[:50] if len(question) > 50 else question
            status = "fallback"

        # 直接更新数据库中的标题
        try:
            from services.data_services.crud import update_work
            from schemas.schemas import WorkUpdate

            # 创建WorkUpdate对象
            work_update = WorkUpdate(title=title.strip())
            updated_work = update_work(db, work_id, work_update, current_user_id)

            if not updated_work:
                raise HTTPException(status_code=404, detail="工作不存在")

            return {
                "title": updated_work.title,
                "status": status,
                "message": "标题生成并更新成功"
            }
        except Exception as e:
            logger.error(f"更新标题到数据库失败: {e}")
            raise HTTPException(status_code=500, detail=f"标题更新失败: {str(e)}")

    except Exception as e:
        logger.error(f"生成工作标题失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
