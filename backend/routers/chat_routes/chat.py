"""
简化版聊天系统API路由
使用JSON存储聊天记录，简化WebSocket逻辑
支持断线重连恢复AI任务
"""

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


class ConnectionManager:
    """
    每个 work 一条活跃连接。任务输出带递增序号 seq：
    - 每条连接记着已发到哪个序号（cursor），同一序号不会发两次；
    - 断线重连时在锁内先补发 cursor 之后的输出，补完才放行实时输出，保证顺序不乱、不重复。
    """

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self._cursors: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, work_id: str) -> asyncio.Lock:
        return self._locks.setdefault(work_id, asyncio.Lock())

    def register(self, work_id: str, websocket: WebSocket, cursor: int = 0):
        """登记连接（覆盖旧连接）；调用方需持有 lock(work_id)"""
        self.active_connections[work_id] = websocket
        self._cursors[work_id] = cursor

    def disconnect(self, work_id: str, websocket: WebSocket | None = None):
        if work_id not in self.active_connections:
            return
        if websocket is not None and self.active_connections[work_id] is not websocket:
            logger.debug(f"[WS] disconnect 跳过: {work_id} 当前连接不是请求断开的连接（已被新连接取代）")
            return
        del self.active_connections[work_id]
        self._cursors.pop(work_id, None)
        logger.info(f"WebSocket连接断开: {work_id}")

    async def _send_locked(self, work_id: str, payload: dict, seq: int | None = None):
        websocket = self.active_connections.get(work_id)
        if websocket is None:
            return
        if seq is not None:
            if seq <= self._cursors.get(work_id, 0):
                return
            payload = {**payload, 'seq': seq}
        try:
            if websocket.client_state.value != 1:
                self.disconnect(work_id, websocket)
                return
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))
            if seq is not None:
                self._cursors[work_id] = seq
        except Exception as e:
            logger.warning(f"发送WebSocket消息失败: {e}")
            self.disconnect(work_id, websocket)

    async def send(self, work_id: str, payload: dict, seq: int | None = None):
        """发给当前连接；带 seq 的是任务输出，按序号去重"""
        async with self.lock(work_id):
            await self._send_locked(work_id, payload, seq)

    async def replay(self, work_id: str, websocket: WebSocket, task, after_seq: int):
        """登记新连接并补发 after_seq 之后的任务输出；补发期间实时输出在锁外等待"""
        async with self.lock(work_id):
            self.register(work_id, websocket, after_seq)
            await self._send_locked(work_id, {
                'type': 'reconnect',
                'message': '检测到正在进行的AI任务，正在恢复...',
                'task_id': task.task_id, 'reply_id': task.reply_id, 'action': task.action,
            })
            outputs = task_manager.get_outputs_after(work_id, after_seq)
            logger.info(f"[RECONNECT] {task.task_id} 从 seq {after_seq} 补发 {len(outputs)} 条输出")
            for output in outputs:
                key = 'update' if output.type == 'reply_update' else 'content' if output.type == 'content' else 'block'
                await self._send_locked(work_id, {'type': output.type, key: output.data}, output.seq)
            await self._send_locked(work_id, {
                'type': 'reconnect_complete',
                'message': '历史输出恢复完成，继续接收新输出...',
                'task_id': task.task_id, 'reply_id': task.reply_id, 'action': task.action,
            })

    def is_connected(self, work_id: str) -> bool:
        if work_id not in self.active_connections:
            return False
        try:
            return self.active_connections[work_id].client_state.value == 1
        except Exception:
            return False

    def get_connection_count(self) -> int:
        return len(self.active_connections)


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
    from ai_system.research.runtime import ResearchRuntime
    if task.action!='ask':ResearchRuntime(get_workspace_path(work_id)).note_stop('manual_pause','已手动暂停，进度与累计用量保留')
    task_manager.cancel_task(work_id, reason="paused")
    if task._async_task:
        try:
            await asyncio.shield(task._async_task)
        except asyncio.CancelledError:
            pass
    return {"work_status": work.status if task.action == "ask" else "paused"}


@router.websocket("/ws/{work_id}")
async def websocket_chat(websocket: WebSocket, work_id: str):
    """WebSocket聊天接口，支持断线重连恢复"""
    ws_callback = None
    is_reconnect_mode = False  # 标记是否为重连模式
    
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

        # 认证成功
        await websocket.send_text(json.dumps({
            'type': 'auth_success',
            'message': '认证成功'
        }))

        # 检查是否有正在运行的任务（断线重连场景）：客户端带上 task_id 和 last_seq 时只补发断点之后的输出
        running_task = task_manager.get_running_task(work_id)
        if running_task:
            is_reconnect_mode = True
            same_task = auth_info.get('task_id') == running_task.task_id
            after_seq = int(auth_info.get('last_seq') or 0) if same_task else 0
            await manager.replay(work_id, websocket, running_task, after_seq)
        else:
            async with manager.lock(work_id):
                manager.register(work_id, websocket)
            if auth_info.get('task_id'):
                await websocket.send_text(json.dumps({
                    'type': 'complete', 'work_status': work.status, 'reload_history': True,
                }))

        while True:
            ws_closed = False
            # 接收用户消息
            data = await websocket.receive_text()
            message_data = json.loads(data)

            # 处理心跳
            if message_data.get('type') == 'ping':
                await websocket.send_text(json.dumps({'type': 'pong'}))
                continue
            
            # 重连模式下，检查任务是否已完成
            if is_reconnect_mode:
                running_task = task_manager.get_running_task(work_id)
                if not running_task:
                    # 任务已完成，退出重连模式
                    is_reconnect_mode = False
                    logger.info(f"[RECONNECT] 任务已完成，退出重连模式")
                else:
                    # 任务还在运行，忽略新消息请求
                    if 'problem' in message_data:
                        await websocket.send_text(json.dumps({
                            'type': 'error',
                            'message': '当前有任务正在执行，请等待完成'
                        }))
                    continue

            if 'problem' not in message_data:
                await websocket.send_text(json.dumps({
                    'type': 'error',
                    'message': '消息格式错误'
                }))
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
                recent_dialogue = []
                intent_judge = None
                if requested_action == 'auto':
                    from services.chat_services.chat_history_manager import ChatHistoryManager
                    from ai_system.judge import JevJudge
                    from ai_system.config.environment import DatabaseConfigManager
                    def load_intent_config():
                        config_db = next(get_db())
                        try:
                            return DatabaseConfigManager(config_db).get_model_config('jev', user_id)
                        finally:
                            config_db.close()
                    intent_config = await asyncio.to_thread(load_intent_config)
                    intent_judge = JevJudge(model=intent_config.model_id, api_key=intent_config.api_key,
                                            base_url=intent_config.base_url, timeout=intent_config.timeout_seconds)
                    recent_dialogue = await asyncio.to_thread(ChatHistoryManager().get_messages, work_id, 6)
                try:
                    action = await resolve_action(requested_action, problem, snapshot, recent_dialogue,
                                                  scope=subproblem_id, judge=intent_judge,
                                                  timeout_seconds=intent_config.timeout_seconds if intent_judge is not None else 30)
                finally:
                    if intent_judge is not None:
                        await intent_judge.aclose()
                if action != 'ask':
                    if task_manager.get_running_task(work_id):
                        raise ValueError('当前有任务正在执行，请等待完成')
                    current = await asyncio.to_thread(research_snapshot, get_workspace_path(work_id), work_id, work.status)
                    if (current.research_id, current.commit_seq, current.graph_status) != (snapshot.research_id, snapshot.commit_seq, snapshot.graph_status):
                        raise ValueError('研究状态已改变，请重试')
                revision_command = None
                if action == "revise_research":
                    from pydantic import ValidationError
                    try:
                        revision=message_data.get('revision')
                        if revision is None and message_data.get('action','auto')=='auto':
                            from uuid import uuid4
                            from services.chat_services.research_session import revision_experiment_scope
                            expected=message_data.get('scope_context')
                            if expected is not None and (not isinstance(expected,dict) or
                                    expected.get('research_id')!=snapshot.research_id or
                                    expected.get('base_revision')!=snapshot.research_revision):
                                raise ValueError('研究版本已改变，请刷新后重试')
                            revision={'command_id':uuid4().hex,'research_id':snapshot.research_id,
                                      'base_revision':snapshot.research_revision,'scope':revision_experiment_scope(snapshot,subproblem_id)}
                        revision_command = ResearchRevisionCommand.model_validate(revision)
                    except ValidationError:
                        raise ValueError("增量修改需要有效的命令标识、研究标识和基础版本")
                    if revision_command.scope is not None and (len(set(revision_command.scope)) != len(revision_command.scope) or
                            not set(revision_command.scope) <= {t["task_id"] for t in snapshot.experiments}):
                        raise ValueError("修改范围包含未知或重复任务")
                    if revision_command.research_id != snapshot.research_id:
                        raise ValueError("研究标识已改变，请刷新后重试")
                if action == "ask":
                    subproblem_id = subproblem_id or infer_question_scope(problem, snapshot)
                    scoped_snapshot(snapshot, subproblem_id)
            except ValueError as exc:
                await manager.send(work_id, {"type": "error", "message": str(exc)})
                continue

            # 为每次消息处理创建新的数据库会话，避免长连接问题
            def init_message_processing():
                db = next(get_db())
                chat_service = ChatService(db)
                session = chat_service.create_or_get_work_session(work_id, user_id)
                return db, chat_service, session

            db, chat_service, session = await loop.run_in_executor(None, init_message_processing)

            class WebSocketStreamCallback(SimpleStreamCallback):
                def __init__(self, work_id, chat_service):
                    super().__init__()
                    self.work_id = work_id
                    self.writer = None
                    self.queue = asyncio.Queue()
                    self.sender = asyncio.create_task(self.send_updates())
                    self.timer = None
                    self.loop = asyncio.get_running_loop()

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

                async def flush_text(self):
                    while True:
                        await asyncio.sleep(0.5)
                        self.writer.flush()

                async def on_content(self, content):
                    self.writer.text(content)

                async def on_message_complete(self, role, content):
                    self.writer.flush()

                async def on_json_block(self, block):
                    if block.get('type') in TRANSIENT_BLOCK_TYPES:
                        # Invalidation payloads are never execution rows.
                        with self.writer.lock:
                            seq = task_manager.add_output(self.work_id, 'json_block', block)
                            self.loop.call_soon_threadsafe(self.queue.put_nowait, ({'type': 'json_block', 'block': block}, seq))
                    else:
                        self.writer.block(block)

                async def finalize(self, status, notice=None):
                    if self.timer: self.timer.cancel()
                    self.writer.finish(status, notice)
                    # Publish callbacks queued with call_soon_threadsafe must enter FIFO first.
                    await asyncio.sleep(0)
                    await self.queue.join()
                    self.sender.cancel()

            # 创建工作空间目录 - 使用统一路径配置
            workspace_dir = str(get_workspace_path(work_id))

            # 初始化AI环境与工作空间
            env_manager = setup_environment_from_db(db, workspace_dir)
            workspace_dir = env_manager.get_workspace_dir()
            model_config = env_manager.config_manager.get_model_config("brain", user_id)
            codeagent_model_config = None if action == "ask" else env_manager.config_manager.get_model_config("code", user_id)
            writer_model_config = None if action == "ask" else env_manager.config_manager.get_model_config("writing", user_id)

            # 创建流式回调和管理器
            ws_callback = WebSocketStreamCallback(work_id, chat_service)
            stream_manager = StreamOutputManager(stream_callback=ws_callback)
            
            # 创建任务记录；task_id 告诉前端，断线重连时据此续传
            task = task_manager.create_task(work_id, user_id, problem, action=action)
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

            # 获取工作的模板ID和输出模式
            template_id = None
            output_mode = "markdown"  # 默认值
            try:
                from services.data_services.crud import get_work
                work = get_work(db, work_id)
                if work:
                    if hasattr(work, 'template_id') and work.template_id:
                        template_id = work.template_id
                        logger.info(f"工作 {work_id} 使用模板: {template_id}")
                    if hasattr(work, 'output_mode') and work.output_mode:
                        output_mode = work.output_mode
                        logger.info(f"工作 {work_id} 输出模式: {output_mode}")
            except Exception as e:
                logger.warning(f"获取工作配置失败: {e}")

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
            await asyncio.to_thread(chat_service.add_message, work_id, 'user', message_data['problem'])
            logger.info(f"[PERSISTENCE] 用户消息已立即保存到持久化存储")

            ws_callback.writer = ReplyWriter(chat_service.history_manager, work_id, task.task_id,
                                               workspace_dir, ws_callback.publish)
            task.reply_id = ws_callback.writer.id
            ws_callback.timer = asyncio.create_task(ws_callback.flush_text())
            await manager.send(work_id, {'type': 'start', 'message': '正在回答...' if action == "ask" else '开始研究...',
                                         'task_id': task.task_id, 'reply_id': task.reply_id, 'action': action, 'work_status': start_status})

            # 执行AI任务 - 使用异步任务避免阻塞
            try:
                # 标记任务开始
                task_manager.start_task(work_id)

                # 定义AI任务执行函数
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

                ai_task = asyncio.create_task(run_ai_task())
                task_manager.set_async_task(work_id, ai_task)

                async def ws_recv_loop():
                    while True:
                        data = await websocket.receive_text()
                        msg = json.loads(data)
                        if msg.get('type') == 'ping':
                            await websocket.send_text(json.dumps({'type': 'pong'}))

                ws_watch = asyncio.create_task(ws_recv_loop())

                done, pending = await asyncio.wait(
                    [ai_task, ws_watch],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if ws_watch in pending:
                    ws_watch.cancel()
                    try:
                        await ws_watch
                    except (asyncio.CancelledError, WebSocketDisconnect):
                        pass

                if ws_watch in done:
                    # 连接已断开，任务在后台继续；不能再回到外层循环 receive，否则 starlette 抛 RuntimeError
                    ws_closed = True
                    exc = ws_watch.exception() if not ws_watch.cancelled() else None
                    if isinstance(exc, WebSocketDisconnect):
                        logger.info(f"[WS] 用户在任务执行期间断开: {work_id}，AI任务继续后台运行")
                    elif exc:
                        logger.warning(f"[WS] ws_watch 异常退出: {exc}")

                if ai_task in done and not ai_task.cancelled() and ai_task.exception():
                    raise ai_task.exception()

            except Exception as e:
                logger.error(f"AI任务执行失败: {e}")

            finally:
                # 确保数据库连接被正确关闭
                try:
                    db.close()
                except Exception as close_error:
                    logger.warning(f"关闭数据库连接时出错: {close_error}")

            if ws_closed:
                manager.disconnect(work_id, websocket)
                return

    except WebSocketDisconnect:
        logger.info(f"WebSocket客户端断开连接: {work_id}")
        manager.disconnect(work_id, websocket)
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
            finally:title_trace.dump(get_workspace_path(work_id)/'.system/title_llm_calls.json')
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
