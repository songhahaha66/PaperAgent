"""
简化版聊天系统API路由
使用JSON存储聊天记录，简化WebSocket逻辑
支持断线重连恢复AI任务
"""

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import asyncio
import logging
import json
import os

from database.database import get_db
from auth.auth import get_current_user, verify_token
from services.chat_services.chat_service import ChatService
from services.chat_services.task_manager import task_manager, TaskStatus
from ai_system.config.environment import setup_environment_from_db
from ai_system.core_managers.stream_manager import PersistentStreamManager, SimpleStreamCallback
from ai_system.core_agents.main_agent import MainAgent
from ai_system.core_handlers.llm_handler import LLMHandler
from langchain_core.messages import HumanMessage
from config.paths import get_workspace_path
from ..utils import route_guard

logger = logging.getLogger(__name__)

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


# 简化的WebSocket连接管理器
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, work_id: str):
        await websocket.accept()
        self.active_connections[work_id] = websocket
        logger.info(f"WebSocket连接建立: {work_id}")

    def disconnect(self, work_id: str):
        if work_id in self.active_connections:
            del self.active_connections[work_id]
        logger.info(f"WebSocket连接断开: {work_id}")

    async def send_message(self, work_id: str, message: str):
        if work_id in self.active_connections:
            try:
                websocket = self.active_connections[work_id]
                # 检查连接状态
                if websocket.client_state.value == 1:  # 1表示连接已建立
                    await websocket.send_text(message)
                else:
                    logger.warning(f"WebSocket连接状态异常: {work_id}, 状态: {websocket.client_state.value}")
                    self.disconnect(work_id)
            except Exception as e:
                logger.error(f"发送WebSocket消息失败: {e}")
                self.disconnect(work_id)
    
    def is_connected(self, work_id: str) -> bool:
        """检查指定work_id的连接是否有效"""
        if work_id not in self.active_connections:
            return False
        
        websocket = self.active_connections[work_id]
        try:
            # 检查连接状态
            return websocket.client_state.value == 1
        except Exception:
            return False
    
    def get_connection_count(self) -> int:
        """获取当前活跃连接数"""
        return len(self.active_connections)


manager = ConnectionManager()


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

        # 注册连接（会覆盖旧连接，这是预期行为）
        manager.active_connections[work_id] = websocket
        
        # 检查是否有正在运行的任务（断线重连场景）
        running_task = task_manager.get_running_task(work_id)
        if running_task:
            is_reconnect_mode = True
            logger.info(f"[RECONNECT] 检测到正在运行的任务: {running_task.task_id}")
            
            await websocket.send_text(json.dumps({
                'type': 'reconnect',
                'message': '检测到正在进行的AI任务，正在恢复...',
                'task_id': running_task.task_id
            }))
            
            # 发送已累积的输出
            output_count = len(running_task.outputs)
            logger.info(f"[RECONNECT] 恢复 {output_count} 条历史输出")
            
            for output in list(running_task.outputs):  # 使用list复制避免迭代时修改
                try:
                    if websocket.client_state.value != 1:
                        logger.warning("[RECONNECT] 连接已断开，停止恢复")
                        break
                    if output.type == 'content':
                        await websocket.send_text(json.dumps({
                            'type': 'content',
                            'content': output.data
                        }))
                    elif output.type == 'json_block':
                        await websocket.send_text(json.dumps({
                            'type': 'json_block',
                            'block': output.data
                        }))
                except Exception as e:
                    logger.error(f"[RECONNECT] 恢复输出失败: {e}")
                    break
            
            await websocket.send_text(json.dumps({
                'type': 'reconnect_complete',
                'message': '历史输出恢复完成，继续接收新输出...'
            }))
            
            # 重连模式下，只需要等待任务完成或接收心跳，不处理新消息
            # 任务的新输出会通过 task_manager 自动发送到当前连接

        while True:
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

            # 发送开始消息
            await websocket.send_text(json.dumps({
                'type': 'start',
                'message': '开始AI分析...'
            }))

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
                    self.current_message_id = None
                    self.json_blocks = []
                    self._closed = False  # 标记连接是否已关闭
                    logger.info(f"WebSocket回调初始化完成，work_id: {work_id}")

                def _get_websocket(self) -> WebSocket | None:
                    """获取当前活跃的WebSocket连接"""
                    return manager.active_connections.get(self.work_id)

                def _is_connection_open(self) -> bool:
                    """检查WebSocket连接是否仍然打开"""
                    if self._closed:
                        return False
                    ws = self._get_websocket()
                    if not ws:
                        return False
                    try:
                        return ws.client_state.value == 1
                    except Exception:
                        return False

                def mark_closed(self):
                    """标记连接已关闭"""
                    self._closed = True

                async def on_content(self, content: str):
                    """实时发送流式内容到WebSocket"""
                    self.content += content
                    
                    # 记录到任务管理器（用于断线重连恢复）
                    task_manager.add_output(self.work_id, 'content', content)
                    
                    # 检查连接状态
                    if not self._is_connection_open():
                        logger.debug(f"WebSocket已关闭，跳过发送内容: {self.work_id}")
                        return
                    
                    try:
                        ws = self._get_websocket()
                        if ws:
                            await ws.send_text(json.dumps({
                                'type': 'content',
                                'content': content
                            }))
                            logger.debug(f"发送流式内容: {repr(content[:30])}")

                        # 使用配置参数优化延迟
                        from ai_system.config.async_config import AsyncConfig
                        config = AsyncConfig.get_websocket_config()
                        if len(content) > config["content_yield_threshold"]:
                            await asyncio.sleep(config["content_yield_delay"])
                    except Exception as e:
                        if "close message has been sent" in str(e):
                            logger.debug(f"WebSocket连接已关闭，停止发送: {self.work_id}")
                            self._closed = True
                        else:
                            logger.error(f"发送WebSocket内容失败: {e}")

                async def on_message_complete(self, role: str, content: str):
                    """消息完成回调"""
                    try:
                        logger.debug(
                            f"收到消息完成回调，角色: {role}, 长度: {len(content)}, JSON块数: {len(self.json_blocks)}")
                    except Exception as e:
                        logger.error(f"处理消息完成失败: {e}")

                async def on_json_block(self, block: dict):
                    """处理JSON格式的数据块"""
                    # 添加到JSON块列表
                    self.json_blocks.append(block)
                    
                    # 记录到任务管理器（用于断线重连恢复）
                    task_manager.add_output(self.work_id, 'json_block', block)
                    
                    # 检查连接状态
                    if not self._is_connection_open():
                        logger.debug(f"WebSocket已关闭，跳过发送JSON块: {self.work_id}")
                        return
                    
                    try:
                        ws = self._get_websocket()
                        if ws:
                            await ws.send_text(json.dumps({
                                'type': 'json_block',
                                'block': block
                            }))
                            logger.debug(f"发送JSON块: {block.get('type', 'unknown')}")
                    except Exception as e:
                        if "close message has been sent" in str(e):
                            logger.debug(f"WebSocket连接已关闭，停止发送JSON块: {self.work_id}")
                            self._closed = True
                        else:
                            logger.error(f"发送JSON块失败: {e}")

            # 创建工作空间目录 - 使用统一路径配置
            workspace_dir = str(get_workspace_path(work_id))

            # 初始化AI环境与工作空间
            env_manager = setup_environment_from_db(db, workspace_dir)
            workspace_dir = env_manager.get_workspace_dir()
            model_config = env_manager.config_manager.get_model_config("brain", user_id)
            codeagent_model_config = env_manager.config_manager.get_model_config("code", user_id)
            writer_model_config = env_manager.config_manager.get_model_config("writing", user_id)

            # 创建流式回调和管理器
            ws_callback = WebSocketStreamCallback(work_id, chat_service)
            stream_manager = PersistentStreamManager(
                stream_callback=ws_callback,
                chat_service=chat_service,  # 传入chat_service实例以支持消息持久化
                session_id=str(session.session_id)
            )
            
            # 创建任务记录
            task = task_manager.create_task(work_id, user_id, message_data['problem'])

            # 创建支持多AI提供商的LLM处理器
            llm_handler = LLMHandler(
                model_config=model_config,
                stream_manager=stream_manager
            )

            # 获取codeagent的LLM实例（仅使用LangChain模型，禁止SmolAgents）
            codeagent_llm = None
            if codeagent_model_config:
                from ai_system.core_handlers.llm_providers import create_llm_from_model_config
                try:
                    codeagent_llm = create_llm_from_model_config(codeagent_model_config)
                    logger.info(f"使用LangChain模型作为CodeAgent: {codeagent_llm}")
                except Exception as e:
                    logger.error(f"创建CodeAgent专用LangChain模型失败: {e}")
                    codeagent_llm = llm_handler.get_llm_instance()
            else:
                logger.info("未提供codeagent配置，使用主LLM")
                codeagent_llm = llm_handler.get_llm_instance()

            # 获取writer的LLM实例（从"writing"配置加载）
            writer_llm = None
            if writer_model_config:
                from ai_system.core_handlers.llm_providers import create_llm_from_model_config
                try:
                    writer_llm = create_llm_from_model_config(writer_model_config)
                    logger.info(f"使用LangChain模型作为WriterAgent: {writer_llm}")
                except Exception as e:
                    logger.error(f"创建WriterAgent专用LangChain模型失败: {e}")
                    writer_llm = None
            else:
                logger.info("未提供writer配置，WriterAgent将使用主LLM")
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

            # 创建MainAgent，传入workspace_dir、work_id、template_id、codeagent_llm、output_mode、writer_llm
            main_agent = MainAgent(
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
                        await main_agent.run(message_data['problem'])
                        
                        # AI处理完成后，保存最终的AI消息
                        final_content = ws_callback.content.strip()

                        if ws_callback.json_blocks:
                            chat_service.add_json_card_message(
                                work_id,
                                "assistant",
                                final_content,
                                ws_callback.json_blocks,
                                {"system_type": "brain"}
                            )
                            logger.info(f"[PERSISTENCE] JSON卡片消息已保存，块数: {len(ws_callback.json_blocks)}")
                        else:
                            chat_service.add_message(
                                work_id,
                                "assistant",
                                final_content,
                                {"system_type": "brain"}
                            )
                            logger.info(f"[PERSISTENCE] 普通文本消息已保存，长度: {len(final_content)}")

                        # 标记任务完成
                        task_manager.complete_task(work_id)
                        
                        # 发送完成消息到前端
                        if ws_callback._is_connection_open():
                            try:
                                ws = ws_callback._get_websocket()
                                if ws:
                                    await ws.send_text(json.dumps({
                                        'type': 'complete',
                                        'message': 'AI分析完成'
                                    }))
                            except Exception as e:
                                logger.debug(f"发送完成消息失败: {e}")
                        
                        logger.info(f"[PERSISTENCE] AI处理完成，最终消息已保存到持久化存储")
                        
                    except asyncio.CancelledError:
                        logger.info(f"AI任务被取消: {work_id}")
                        task_manager.cancel_task(work_id)
                        raise
                    except Exception as e:
                        logger.error(f"AI任务执行失败: {e}")
                        task_manager.fail_task(work_id, str(e))
                        
                        # 尝试发送错误消息
                        if ws_callback._is_connection_open():
                            try:
                                ws = ws_callback._get_websocket()
                                if ws:
                                    await ws.send_text(json.dumps({
                                        'type': 'error',
                                        'message': f'AI处理失败: {str(e)}'
                                    }))
                            except Exception:
                                pass
                        raise

                # 创建异步任务
                ai_task = asyncio.create_task(run_ai_task())
                task_manager.set_async_task(work_id, ai_task)

                # 等待AI任务完成，添加超时保护
                try:
                    await asyncio.wait_for(ai_task, timeout=600)  # 10分钟超时
                except asyncio.TimeoutError:
                    logger.error(f"AI任务执行超时: {work_id}")
                    task_manager.fail_task(work_id, "任务执行超时")
                    if ws_callback._is_connection_open():
                        ws = ws_callback._get_websocket()
                        if ws:
                            await ws.send_text(json.dumps({
                                'type': 'error',
                                'message': 'AI任务执行超时，请重试'
                            }))

            except Exception as e:
                logger.error(f"AI任务执行失败: {e}")

            finally:
                db.close()

    except WebSocketDisconnect:
        logger.info(f"WebSocket客户端断开连接: {work_id}")
        manager.disconnect(work_id)
    except Exception as e:
        logger.error(f"WebSocket处理失败: {e}")
        try:
            # 检查连接状态再发送错误消息
            if websocket.client_state.value == 1:
                await websocket.send_text(json.dumps({
                    'type': 'error',
                    'message': f'处理失败: {str(e)}'
                }))
        except Exception:
            pass
        manager.disconnect(work_id)


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
