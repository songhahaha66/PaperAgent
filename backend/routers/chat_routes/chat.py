"""
简化版聊天系统API路由
使用JSON存储聊天记录，简化WebSocket逻辑
支持断线重连恢复AI任务
"""

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import asyncio
import logging
import json

from database.database import get_db
from auth.auth import get_current_user, verify_token
from services.chat_services.chat_service import ChatService
from services.chat_services.task_manager import task_manager
from ai_system.config.environment import setup_environment_from_db
from ai_system.core_managers.stream_manager import PersistentStreamManager, SimpleStreamCallback
from ai_system.graph import PaperGraphAgent
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
                'task_id': task.task_id, 'action': task.action,
            })
            outputs = task_manager.get_outputs_after(work_id, after_seq)
            logger.info(f"[RECONNECT] {task.task_id} 从 seq {after_seq} 补发 {len(outputs)} 条输出")
            for output in outputs:
                key = 'content' if output.type == 'content' else 'block'
                await self._send_locked(work_id, {'type': output.type, key: output.data}, output.seq)
            await self._send_locked(work_id, {
                'type': 'reconnect_complete',
                'message': '历史输出恢复完成，继续接收新输出...',
                'task_id': task.task_id, 'action': task.action,
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
TRANSIENT_BLOCK_TYPES = {'plan_updated', 'file_changed', 'research_snapshot', 'research_state'}


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
        return await asyncio.to_thread(research_snapshot, get_workspace_path(work_id), work_id, work.status)
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
                action = resolve_action(message_data.get("action", "auto"), problem, snapshot)
                subproblem_id = message_data.get("subproblem_id")
                if subproblem_id is not None and not isinstance(subproblem_id, str):
                    raise ValueError("子问题编号无效")
                revision_command = None
                if action == "revise_research":
                    from pydantic import ValidationError
                    try:
                        revision_command = ResearchRevisionCommand.model_validate(message_data.get("revision"))
                    except ValidationError:
                        raise ValueError("增量修改需要有效的命令标识、研究标识和基础版本")
                    if revision_command.scope is not None and (len(set(revision_command.scope)) != len(revision_command.scope) or
                            not set(revision_command.scope) <= {t["task_id"] for t in snapshot.tasks}):
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

            # 创建流式回调
            class WebSocketStreamCallback(SimpleStreamCallback):
                def __init__(self, work_id: str, chat_service: ChatService):
                    super().__init__()
                    self.work_id = work_id
                    self.chat_service = chat_service
                    self.content = ""
                    self.json_blocks = []
                    logger.info(f"WebSocket回调初始化完成，work_id: {work_id}")

                async def on_content(self, content: str):
                    """实时发送流式内容到WebSocket"""
                    self.content += content
                    
                    # 记录到任务管理器（用于断线重连恢复），带序号发给当前连接
                    seq = task_manager.add_output(self.work_id, 'content', content)
                    try:
                        await manager.send(self.work_id, {'type': 'content', 'content': content}, seq)

                        # 使用配置参数优化延迟
                        from ai_system.config.async_config import AsyncConfig
                        config = AsyncConfig.get_websocket_config()
                        if len(content) > config["content_yield_threshold"]:
                            await asyncio.sleep(config["content_yield_delay"])
                    except Exception as e:
                        logger.error(f"发送WebSocket内容失败: {e}")

                async def on_message_complete(self, role: str, content: str):
                    """消息完成回调"""
                    logger.debug(f"消息完成，角色: {role}, 长度: {len(content)}, JSON块数: {len(self.json_blocks)}")

                async def on_json_block(self, block: dict):
                    """处理JSON格式的数据块"""
                    active_task = task_manager.get_task(self.work_id)
                    block = {**block, "schema_version": 1, "work_id": self.work_id,
                             "task_id": active_task.task_id if active_task else None}
                    if block.get('type') not in TRANSIENT_BLOCK_TYPES:
                        self.json_blocks.append(block)
                    # Stable identifiers travel with both live events and replay.
                    seq = task_manager.add_output(self.work_id, 'json_block', block)
                    try:
                        await manager.send(self.work_id, {'type': 'json_block', 'block': block}, seq)
                        logger.debug(f"发送JSON块: {block.get('type', 'unknown')}")
                    except Exception as e:
                        logger.error(f"发送JSON块失败: {e}")

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
            stream_manager = PersistentStreamManager(
                stream_callback=ws_callback,
                chat_service=chat_service,  # 传入chat_service实例以支持消息持久化
                session_id=str(session.session_id)
            )
            
            # 创建任务记录；task_id 告诉前端，断线重连时据此续传
            task = task_manager.create_task(work_id, user_id, problem, action=action)
            start_status = original_status if action == "ask" else "in_progress"
            if action != "ask":
                await loop.run_in_executor(None, set_work_status, work_id, start_status)
            await manager.send(work_id, {'type': 'start', 'message': '正在回答...' if action == "ask" else '开始研究...',
                                         'task_id': task.task_id, 'action': action, 'work_status': start_status})

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
                    logger.info(f"求解阶段使用 code 配置的模型: {codeagent_llm}")
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
                    logger.info(f"写作阶段使用 writing 配置的模型: {writer_llm}")
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
            main_agent = None if action == "ask" else PaperGraphAgent(
                llm_handler.get_llm_instance(), 
                stream_manager, 
                workspace_dir, 
                work_id, 
                template_id, 
                codeagent_llm,
                output_mode=output_mode,
                writer_llm=writer_llm
            )

            # 立即保存用户消息到持久化存储，确保历史记录顺序正确
            await stream_manager.save_user_message(message_data['problem'])
            logger.info(f"[PERSISTENCE] 用户消息已立即保存到持久化存储")

            # 执行AI任务 - 使用异步任务避免阻塞
            try:
                # 标记任务开始
                task_manager.start_task(work_id)

                # 定义AI任务执行函数
                async def run_ai_task():
                    try:
                        if action == "ask":
                            history = await loop.run_in_executor(
                                None, chat_service.get_work_chat_history, work_id, 8,
                            )
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
                            graph_input = "确认" if action == "confirm" else problem
                            if action == "run" and snapshot.has_research:
                                await main_agent.run(graph_input, new_run=True)
                            else:
                                await main_agent.run(graph_input)
                        
                        # AI处理完成后，保存最终的AI消息
                        # 使用新的数据库连接，避免长时间运行后连接断开的问题
                        final_content = ws_callback.content.strip()
                        
                        def save_final_message():
                            save_db = next(get_db())
                            try:
                                save_chat_service = ChatService(save_db)
                                if ws_callback.json_blocks:
                                    save_chat_service.add_json_card_message(
                                        work_id,
                                        "assistant",
                                        final_content,
                                        ws_callback.json_blocks,
                                        {"system_type": "brain"}
                                    )
                                    logger.info(f"[PERSISTENCE] JSON卡片消息已保存，块数: {len(ws_callback.json_blocks)}")
                                else:
                                    save_chat_service.add_message(
                                        work_id,
                                        "assistant",
                                        final_content,
                                        {"system_type": "brain"}
                                    )
                                    logger.info(f"[PERSISTENCE] 普通文本消息已保存，长度: {len(final_content)}")
                            finally:
                                save_db.close()
                        
                        await loop.run_in_executor(None, save_final_message)

                        # 停在"确认建模方案"时工作是待确认，不是已完成
                        failed_blocks = any(b.get('type') == 'main_agent_error' for b in ws_callback.json_blocks)
                        solutions = (getattr(main_agent, 'last_result', None) or {}).get('solutions') or {}
                        incomplete = any(solution.get('status') != 'completed' for solution in solutions.values())
                        work_status = original_status if action == "ask" else ('failed' if failed_blocks else 'waiting' if getattr(main_agent, 'waiting_confirm', False) else 'paused' if incomplete or (getattr(main_agent, 'last_result', None) or {}).get('research_blocked') else 'completed')
                        if action != "ask":
                            await loop.run_in_executor(None, set_work_status, work_id, work_status)
                            try:
                                evidence = await asyncio.to_thread(research_snapshot, workspace_dir, work_id, work_status)
                                await stream_manager.send_json_block("research_snapshot", evidence.model_dump())
                            except ValueError:
                                logger.warning("研究快照暂不可读，保留任务完成状态: %s", work_id)

                        # 标记任务完成
                        task_manager.complete_task(work_id)
                        
                        # 发送完成消息到前端（通过manager获取当前活跃连接）
                        try:
                            await manager.send(work_id, {'type': 'complete', 'message': 'AI分析完成', 'work_status': work_status})
                            logger.info(f"[COMPLETE] 完成消息已发送到前端: {work_id}")
                        except Exception as e:
                            logger.debug(f"发送完成消息失败: {e}")
                        
                        logger.info(f"[PERSISTENCE] AI处理完成，最终消息已保存到持久化存储")
                        
                    except asyncio.CancelledError:
                        logger.info(f"AI任务被取消: {work_id}")
                        
                        task = task_manager.get_task(work_id)
                        interrupted_status = original_status if action == "ask" else ('paused' if task and task.cancel_reason == 'paused' else 'failed' if task and task.error else 'cancelled')
                        # 取消时也保存已生成的内容
                        partial_content = ws_callback.content.strip()
                        partial_json_blocks = ws_callback.json_blocks
                        
                        if partial_content or partial_json_blocks:
                            logger.info(f"[CANCELLED] 保存取消前已生成的内容")
                            
                            def save_cancelled_message():
                                save_db = next(get_db())
                                try:
                                    save_chat_service = ChatService(save_db)
                                    notices = {
                                        'paused': "任务已暂停，进度已保存。可从断点继续。",
                                        'failed': "任务意外中断，已有结果已保存。可从断点继续。",
                                        'cancelled': "任务已取消，已有结果已保存。可从断点继续。",
                                    }
                                    cancel_notice = "\n\n---\n" + ("回复已停止。" if action == "ask" else notices[interrupted_status])
                                    final_content = partial_content + cancel_notice
                                    
                                    if partial_json_blocks:
                                        save_chat_service.add_json_card_message(
                                            work_id, "assistant", final_content,
                                            partial_json_blocks,
                                            {"system_type": "brain", "status": interrupted_status}
                                        )
                                    else:
                                        save_chat_service.add_message(
                                            work_id, "assistant", final_content,
                                            {"system_type": "brain", "status": interrupted_status}
                                        )
                                except Exception as save_error:
                                    logger.error(f"[CANCELLED] 保存部分内容失败: {save_error}")
                                finally:
                                    save_db.close()
                            
                            await loop.run_in_executor(None, save_cancelled_message)
                        
                        if action != "ask":
                            await loop.run_in_executor(None, set_work_status, work_id, interrupted_status)
                        try:
                            await manager.send(work_id, {'type': 'complete', 'work_status': interrupted_status, 'reload_history': True})
                        except Exception:
                            pass
                        raise
                    except Exception as e:
                        logger.error(f"AI任务执行失败: {e}")
                        
                        # 失败时也保存已生成的内容
                        partial_content = ws_callback.content.strip()
                        partial_json_blocks = ws_callback.json_blocks
                        
                        if partial_content or partial_json_blocks:
                            logger.info(f"[FAILED] 保存失败前已生成的内容")
                            
                            def save_failed_message():
                                save_db = next(get_db())
                                try:
                                    save_chat_service = ChatService(save_db)
                                    error_notice = "\n\n---\n回答失败，请重试。" if action == "ask" else f"\n\n---\n⚠️ *任务执行失败: {str(e)[:100]}*"
                                    final_content = partial_content + error_notice
                                    
                                    if partial_json_blocks:
                                        save_chat_service.add_json_card_message(
                                            work_id, "assistant", final_content,
                                            partial_json_blocks,
                                            {"system_type": "brain", "status": "failed", "error": "answer_failed" if action == "ask" else str(e)}
                                        )
                                    else:
                                        save_chat_service.add_message(
                                            work_id, "assistant", final_content,
                                            {"system_type": "brain", "status": "failed", "error": "answer_failed" if action == "ask" else str(e)}
                                        )
                                except Exception as save_error:
                                    logger.error(f"[FAILED] 保存部分内容失败: {save_error}")
                                finally:
                                    save_db.close()
                            
                            await loop.run_in_executor(None, save_failed_message)
                        
                        if action != "ask":
                            await loop.run_in_executor(None, set_work_status, work_id, 'failed')
                        task_manager.fail_task(work_id, str(e))
                        
                        # 通知前端（之前这里调用了不存在的方法，失败时前端会一直停在"生成中"）
                        await manager.send(work_id, {'type': 'error', 'message': (str(e) if isinstance(e, ResearchContextPermissionError) else '回答失败，请重试') if action == 'ask' else f'AI处理失败: {str(e)}', 'work_status': original_status if action == 'ask' else 'failed'})
                        raise

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
            response = await llm.ainvoke(messages)
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
            logger.error(f"AI生成标题失败: {e}")
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
