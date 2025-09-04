"""
简化版聊天系统API路由
使用JSON存储聊天记录，简化WebSocket逻辑
"""

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import asyncio
import logging
import json
import os
import time

from database.database import get_db
from auth.auth import get_current_user, verify_token
from services.chat_services.chat_service import ChatService
from ai_system.config.environment import setup_environment_from_db
from ai_system.core_managers.stream_manager import PersistentStreamManager, SimpleStreamCallback
from ai_system.core_agents.main_agent import MainAgent
from ai_system.core_handlers.llm_handler import LLMHandler
from ai_system.core_handlers.llm_factory import LLMFactory
from ..utils import route_guard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["聊天系统"])


# 持久化生成状态管理 - 按照JSON记录格式
class GenerationState:
    def __init__(self, work_id: str, user_message: str, chat_service):
        self.work_id = work_id
        self.user_message = user_message
        self.chat_service = chat_service
        self.is_generating = True
        self.start_time = time.time()
        self.last_update = time.time()
        self.generation_task = None
        self.is_complete = False
        self.error_message = None
        
        # 创建临时消息记录，按照JSON格式
        self.temp_message_id = f"temp_{int(time.time() * 1000)}"
        self.temp_message = {
            "id": self.temp_message_id,
            "role": "assistant",
            "content": "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime()),
            "metadata": {"system_type": "brain", "is_generating": True},
            "json_blocks": [],
            "message_type": "text"
        }
        
        # 初始化临时消息到JSON文件
        self._save_temp_message()
        
    def add_content(self, content: str):
        """添加生成的内容到临时消息"""
        self.temp_message["content"] += content
        self.temp_message["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())
        self.last_update = time.time()
        self._save_temp_message()
        
    def add_json_block(self, block: dict):
        """添加JSON块到临时消息"""
        self.temp_message["json_blocks"].append(block)
        self.temp_message["message_type"] = "json_card"
        self.temp_message["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())
        self.last_update = time.time()
        self._save_temp_message()
        
    def complete_generation(self):
        """标记生成完成，保存最终消息"""
        self.is_generating = False
        self.is_complete = True
        self.temp_message["metadata"]["is_generating"] = False
        self.temp_message["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())
        self.last_update = time.time()
        
        # 保存最终消息到正式记录
        self._save_final_message()
        
    def set_error(self, error_message: str):
        """设置错误状态"""
        self.is_generating = False
        self.error_message = error_message
        self.temp_message["metadata"]["is_generating"] = False
        self.temp_message["metadata"]["error"] = error_message
        self.temp_message["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())
        self.last_update = time.time()
        self._save_temp_message()
        
    def _save_temp_message(self):
        """保存临时消息到JSON文件"""
        try:
            # 获取当前历史记录
            history = self.chat_service.history_manager.get_work_history(self.work_id)
            
            # 查找并更新临时消息，如果不存在则添加
            temp_found = False
            for i, msg in enumerate(history["messages"]):
                if msg.get("id") == self.temp_message_id:
                    history["messages"][i] = self.temp_message.copy()
                    temp_found = True
                    break
            
            if not temp_found:
                history["messages"].append(self.temp_message.copy())
            
            # 保存到文件
            self.chat_service.history_manager._save_history(self.work_id, history)
            
        except Exception as e:
            logger.error(f"保存临时消息失败: {e}")
    
    def _save_final_message(self):
        """保存最终消息，移除临时标记"""
        try:
            # 获取当前历史记录
            history = self.chat_service.history_manager.get_work_history(self.work_id)
            
            # 更新临时消息为最终消息
            for i, msg in enumerate(history["messages"]):
                if msg.get("id") == self.temp_message_id:
                    # 移除临时标记，生成新的UUID
                    import uuid
                    final_message = self.temp_message.copy()
                    final_message["id"] = str(uuid.uuid4())
                    final_message["metadata"]["is_generating"] = False
                    if "error" in final_message["metadata"]:
                        del final_message["metadata"]["error"]
                    
                    history["messages"][i] = final_message
                    break
            
            # 保存到文件
            self.chat_service.history_manager._save_history(self.work_id, history)
            
        except Exception as e:
            logger.error(f"保存最终消息失败: {e}")
    
    def get_temp_message(self) -> dict:
        """获取临时消息"""
        return self.temp_message.copy()
        
    def get_status(self) -> dict:
        """获取生成状态"""
        return {
            "work_id": self.work_id,
            "is_generating": self.is_generating,
            "is_complete": self.is_complete,
            "content_length": len(self.temp_message["content"]),
            "json_blocks_count": len(self.temp_message["json_blocks"]),
            "start_time": self.start_time,
            "last_update": self.last_update,
            "has_error": self.error_message is not None,
            "error_message": self.error_message,
            "temp_message_id": self.temp_message_id
        }


# 增强的WebSocket连接管理器，支持持久化生成
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.generation_states: dict[str, GenerationState] = {}
        self.pending_messages: dict[str, list] = {}  # 存储离线时的消息

    async def connect(self, websocket: WebSocket, work_id: str):
        await websocket.accept()
        self.active_connections[work_id] = websocket
        logger.info(f"WebSocket连接建立: {work_id}")
        
        # 检查是否有待发送的消息
        if work_id in self.pending_messages:
            logger.info(f"发现 {len(self.pending_messages[work_id])} 条待发送消息")
            for message in self.pending_messages[work_id]:
                try:
                    await websocket.send_text(message)
                except Exception as e:
                    logger.error(f"发送待发送消息失败: {e}")
            # 清空待发送消息
            del self.pending_messages[work_id]

    def disconnect(self, work_id: str):
        if work_id in self.active_connections:
            del self.active_connections[work_id]
        logger.info(f"WebSocket连接断开: {work_id}")

    async def send_message(self, work_id: str, message: str):
        """发送消息，如果连接断开则缓存消息"""
        if work_id in self.active_connections:
            try:
                websocket = self.active_connections[work_id]
                # 检查连接状态
                if websocket.client_state.value == 1:  # 1表示连接已建立
                    await websocket.send_text(message)
                else:
                    logger.warning(f"WebSocket连接状态异常: {work_id}, 状态: {websocket.client_state.value}")
                    self._cache_message(work_id, message)
                    self.disconnect(work_id)
            except Exception as e:
                logger.error(f"发送WebSocket消息失败: {e}")
                self._cache_message(work_id, message)
                self.disconnect(work_id)
        else:
            # 连接不存在，缓存消息
            self._cache_message(work_id, message)
    
    def _cache_message(self, work_id: str, message: str):
        """缓存消息，等待重连时发送"""
        if work_id not in self.pending_messages:
            self.pending_messages[work_id] = []
        self.pending_messages[work_id].append(message)
        logger.info(f"消息已缓存，work_id: {work_id}, 缓存消息数: {len(self.pending_messages[work_id])}")
    
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
    
    def start_generation(self, work_id: str, user_message: str, chat_service) -> GenerationState:
        """开始生成任务"""
        generation_state = GenerationState(work_id, user_message, chat_service)
        self.generation_states[work_id] = generation_state
        logger.info(f"开始生成任务: {work_id}")
        return generation_state
    
    def get_generation_state(self, work_id: str) -> GenerationState:
        """获取生成状态"""
        return self.generation_states.get(work_id)
    
    def complete_generation(self, work_id: str):
        """完成生成任务"""
        if work_id in self.generation_states:
            self.generation_states[work_id].complete_generation()
            logger.info(f"生成任务完成: {work_id}")
    
    def set_generation_error(self, work_id: str, error_message: str):
        """设置生成错误"""
        if work_id in self.generation_states:
            self.generation_states[work_id].set_error(error_message)
            logger.error(f"生成任务错误: {work_id}, 错误: {error_message}")
    
    def cleanup_generation(self, work_id: str):
        """清理生成状态"""
        if work_id in self.generation_states:
            del self.generation_states[work_id]
            logger.info(f"清理生成状态: {work_id}")
    
    def get_all_generation_states(self) -> dict:
        """获取所有生成状态"""
        return {work_id: state.get_status() for work_id, state in self.generation_states.items()}


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


@router.websocket("/ws/{work_id}")
async def websocket_chat(websocket: WebSocket, work_id: str):
    """简化版WebSocket聊天接口"""
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
        db = next(get_db())
        try:
            from services.data_services.crud import get_work
            work = get_work(db, work_id)
            if not work or work.created_by != user_id:
                await websocket.send_text(json.dumps({
                    'type': 'error',
                    'message': '无权限访问此工作'
                }))
                await websocket.close()
                return
        finally:
            db.close()

        # 认证成功
        await websocket.send_text(json.dumps({
            'type': 'auth_success',
            'message': '认证成功'
        }))

        # 注册连接
        manager.active_connections[work_id] = websocket

        # 创建聊天服务
        db = next(get_db())
        try:
            chat_service = ChatService(db)

            # 确保有session记录
            session = chat_service.create_or_get_work_session(work_id, user_id)

            while True:
                # 接收用户消息
                data = await websocket.receive_text()
                message_data = json.loads(data)

                # 处理心跳
                if message_data.get('type') == 'ping':
                    await websocket.send_text(json.dumps({'type': 'pong'}))
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

                # 检查是否已有正在进行的生成任务
                existing_generation = manager.get_generation_state(work_id)
                if existing_generation and existing_generation.is_generating:
                    logger.info(f"发现正在进行的生成任务: {work_id}")
                    
                    # 发送临时消息的当前状态
                    temp_message = existing_generation.get_temp_message()
                    await websocket.send_text(json.dumps({
                        'type': 'temp_message',
                        'message': temp_message
                    }))
                    
                    # 如果生成已完成，发送完成消息
                    if existing_generation.is_complete:
                        await websocket.send_text(json.dumps({
                            'type': 'complete',
                            'message': 'AI分析完成'
                        }))
                        # 清理生成状态
                        manager.cleanup_generation(work_id)
                        continue
                    elif existing_generation.error_message:
                        await websocket.send_text(json.dumps({
                            'type': 'error',
                            'message': existing_generation.error_message
                        }))
                        # 清理生成状态
                        manager.cleanup_generation(work_id)
                        continue
                    else:
                        # 生成仍在进行，等待完成
                        logger.info(f"等待正在进行的生成任务完成: {work_id}")
                        continue

                # 开始新的生成任务
                generation_state = manager.start_generation(work_id, message_data['problem'], chat_service)

                # 创建流式回调
                class WebSocketStreamCallback(SimpleStreamCallback):
                    def __init__(self, websocket: WebSocket, work_id: str, chat_service: ChatService, generation_state: GenerationState):
                        super().__init__()
                        self.websocket = websocket
                        self.work_id = work_id
                        self.chat_service = chat_service
                        self.generation_state = generation_state
                        self.content = ""
                        self.current_message_id = None
                        self.json_blocks = []
                        logger.info(f"WebSocket回调初始化完成，work_id: {work_id}")

                    async def on_content(self, content: str):
                        """更新临时消息内容，触发前端轮询"""
                        self.content += content
                        
                        # 更新生成状态（会自动保存到JSON文件）
                        if self.generation_state:
                            self.generation_state.add_content(content)
                        
                        # 发送更新通知，让前端知道有新内容
                        try:
                            await manager.send_message(self.work_id, json.dumps({
                                'type': 'content_updated',
                                'message': '内容已更新，请刷新'
                            }))
                            logger.debug(f"内容已更新: {repr(content[:30])}")
                            
                        except Exception as e:
                            logger.error(f"发送更新通知失败: {e}")

                    async def on_message_complete(self, role: str, content: str):
                        """消息完成回调"""
                        try:
                            logger.debug(
                                f"收到消息完成回调，角色: {role}, 长度: {len(content)}, JSON块数: {len(self.json_blocks)}")
                        except Exception as e:
                            logger.error(f"处理消息完成失败: {e}")

                    async def on_json_block(self, block: dict):
                        """处理JSON格式的数据块，更新临时消息"""
                        try:
                            # 添加到JSON块列表
                            self.json_blocks.append(block)
                            
                            # 更新生成状态（会自动保存到JSON文件）
                            if self.generation_state:
                                self.generation_state.add_json_block(block)

                            # 发送更新通知
                            await manager.send_message(self.work_id, json.dumps({
                                'type': 'json_block_updated',
                                'message': 'JSON块已更新，请刷新'
                            }))
                            logger.debug(f"JSON块已更新: {block.get('type', 'unknown')}")
                            
                        except Exception as e:
                            logger.error(f"更新JSON块失败: {e}")

                # 初始化AI环境与工作空间（通过工厂）
                llm_factory = LLMFactory(db)
                llm_factory.initialize_system("brain")
                llm_factory.setup_workspace(work_id)

                # 创建流式回调和管理器
                ws_callback = WebSocketStreamCallback(
                    websocket, work_id, chat_service, generation_state)
                stream_manager = PersistentStreamManager(
                    stream_callback=ws_callback,
                    chat_service=chat_service,  # 传入chat_service实例以支持消息持久化
                    session_id=str(session.session_id)
                )

                # 创建LLM处理器和主代理
                llm_handler = llm_factory.create_handler("brain", stream_manager=stream_manager)

                # 获取工作的模板ID
                template_id = None
                try:
                    from services.data_services.crud import get_work
                    work = get_work(db, work_id)
                    if work and hasattr(work, 'template_id') and work.template_id:
                        template_id = work.template_id
                        logger.info(f"工作 {work_id} 使用模板: {template_id}")
                except Exception as e:
                    logger.warning(f"获取工作模板ID失败: {e}")

                # 创建MainAgent，传入work_id和template_id
                main_agent = MainAgent(llm_handler, stream_manager, work_id, template_id)

                # 先加载真正的历史消息（排除当前这次对话）
                history_messages = chat_service.get_work_chat_history(
                    work_id, limit=20)
                if history_messages:
                    # 过滤掉可能包含当前消息的历史记录
                    # 只加载真正属于历史的消息
                    history_data = [{"role": msg["role"], "content": msg["content"]}
                                    for msg in history_messages]
                    main_agent.load_conversation_history(history_data)
                    logger.info(f"已加载 {len(history_data)} 条历史消息到MainAgent")
                else:
                    logger.info("没有找到历史消息，将创建新的对话")

                # 在正确位置添加用户消息到MainAgent的消息历史
                main_agent.add_user_message(message_data['problem'])

                # 立即保存用户消息到持久化存储，确保历史记录顺序正确
                await stream_manager.save_user_message(message_data['problem'])
                logger.info(f"[PERSISTENCE] 用户消息已立即保存到持久化存储")

                # 执行AI任务 - 支持持久化生成
                try:
                    # 创建异步任务执行AI处理，不等待完成
                    ai_task = asyncio.create_task(
                        main_agent.run(message_data['problem'])
                    )
                    
                    # 将任务保存到生成状态中
                    generation_state.generation_task = ai_task
                    
                    # 在后台运行AI任务，不阻塞WebSocket连接
                    async def background_ai_task():
                        try:
                            await ai_task
                            
                            # AI任务完成，标记生成完成（会自动保存最终消息）
                            manager.complete_generation(work_id)
                            
                            # 如果用户在线，发送完成消息
                            if manager.is_connected(work_id):
                                await manager.send_message(work_id, json.dumps({
                                    'type': 'complete',
                                    'message': 'AI分析完成'
                                }))
                            
                            logger.info(f"AI任务在后台完成: {work_id}")
                            
                        except Exception as e:
                            logger.error(f"AI任务执行失败: {e}")
                            manager.set_generation_error(work_id, str(e))
                            
                            # 如果用户在线，发送错误消息
                            if manager.is_connected(work_id):
                                await manager.send_message(work_id, json.dumps({
                                    'type': 'error',
                                    'message': f'AI处理失败: {str(e)}'
                                }))
                    
                    # 启动后台任务
                    asyncio.create_task(background_ai_task())
                    
                    # 等待一小段时间，让AI开始生成
                    await asyncio.sleep(1)
                    
                    # 检查AI任务是否已经开始生成内容
                    temp_message = generation_state.get_temp_message()
                    if temp_message["content"] or temp_message["json_blocks"]:
                        logger.info(f"AI任务已开始生成内容: {work_id}")
                    else:
                        logger.info(f"AI任务已启动，等待生成内容: {work_id}")
                    
                    # 继续监听WebSocket消息，不阻塞
                    continue

                except Exception as e:
                    logger.error(f"AI任务启动失败: {e}")
                    manager.set_generation_error(work_id, str(e))
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': f'AI处理启动失败: {str(e)}'
                    }))

        finally:
            db.close()

    except WebSocketDisconnect:
        manager.disconnect(work_id)
    except Exception as e:
        logger.error(f"WebSocket处理失败: {e}")
        try:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'message': f'处理失败: {str(e)}'
            }))
        except:
            pass
        manager.disconnect(work_id)


@router.post("/work/{work_id}/generate-title")
async def generate_work_title(
    work_id: str,
    request: dict,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """AI生成工作标题"""
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

        # 初始化AI环境（通过工厂）
        llm_factory = LLMFactory(db)
        llm_factory.initialize_system("brain")

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
            # 使用现有的process_stream方法，但不使用流式处理
            messages = [{"role": "user", "content": title_prompt}]

            # 创建临时的流管理器来捕获输出
            from ai_system.core_managers.stream_manager import SimpleStreamCallback

            class TitleCaptureCallback(SimpleStreamCallback):
                def __init__(self):
                    super().__init__(output_queue=None)  # 显式传递None给父类
                    self.content = ""

                async def print_stream(self, content: str):
                    self.content += content

                async def print_content(self, content: str):
                    self.content += content

                async def finalize_message(self):
                    pass

            title_callback = TitleCaptureCallback()
            from ai_system.core_managers.stream_manager import StreamOutputManager
            title_stream_manager = StreamOutputManager(title_callback)

            # 直接通过工厂执行同步（非流式）生成
            assistant_message, _ = await llm_factory.run_sync("brain", messages)
            title = assistant_message.get("content", "")

            # 清理标题（移除可能的引号、换行等）
            title = title.strip().strip('"').strip("'").strip()

            return {
                "title": title,
                "status": "success"
            }
        except Exception as e:
            logger.error(f"AI生成标题失败: {e}")
            # 如果AI生成失败，使用问题作为备选标题
            fallback_title = question[:50] if len(question) > 50 else question
            return {
                "title": fallback_title,
                "status": "fallback"
            }

    except Exception as e:
        logger.error(f"生成工作标题失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/work/{work_id}/title")
async def update_work_title(
    work_id: str,
    request: dict,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新工作标题"""
    try:
        # 验证用户权限
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

        # 获取新标题
        new_title = request.get("title", "")
        if not new_title or new_title.strip() == "":
            raise HTTPException(status_code=400, detail="标题不能为空")

        # 更新数据库中的标题
        from services.data_services.crud import update_work
        from schemas.schemas import WorkUpdate

        # 创建WorkUpdate对象
        work_update = WorkUpdate(title=new_title.strip())
        updated_work = update_work(db, work_id, work_update, current_user_id)

        if not updated_work:
            raise HTTPException(status_code=404, detail="工作不存在")

        return {
            "status": "success",
            "message": "标题更新成功"
        }

    except Exception as e:
        logger.error(f"更新工作标题失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/work/{work_id}/generation-status")
@route_guard
async def get_generation_status(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取AI生成状态"""
    try:
        # 验证用户权限
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

        # 获取生成状态
        generation_state = manager.get_generation_state(work_id)
        
        if not generation_state:
            return {
                "work_id": work_id,
                "is_generating": False,
                "has_generation": False,
                "message": "没有正在进行的生成任务"
            }

        return {
            "work_id": work_id,
            "is_generating": generation_state.is_generating,
            "has_generation": True,
            "generation_status": generation_state.get_status(),
            "temp_message": generation_state.get_temp_message()
        }

    except Exception as e:
        logger.error(f"获取生成状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/work/{work_id}/temp-message")
@route_guard
async def get_temp_message(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取临时消息（用于前端轮询）"""
    try:
        # 验证用户权限
        from services.data_services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

        # 获取生成状态
        generation_state = manager.get_generation_state(work_id)
        
        if not generation_state:
            return {
                "work_id": work_id,
                "has_temp_message": False,
                "message": "没有正在进行的生成任务"
            }

        return {
            "work_id": work_id,
            "has_temp_message": True,
            "temp_message": generation_state.get_temp_message(),
            "is_generating": generation_state.is_generating,
            "is_complete": generation_state.is_complete,
            "last_update": generation_state.last_update
        }

    except Exception as e:
        logger.error(f"获取临时消息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/generation-states")
@route_guard
async def get_all_generation_states(
    current_user_id: int = Depends(get_current_user)
):
    """获取所有生成状态（管理员功能）"""
    try:
        # 获取所有生成状态
        all_states = manager.get_all_generation_states()
        
        return {
            "total_generations": len(all_states),
            "generation_states": all_states,
            "active_connections": manager.get_connection_count()
        }

    except Exception as e:
        logger.error(f"获取所有生成状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
