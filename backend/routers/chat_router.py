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

from database.database import get_db
from auth.auth import get_current_user, verify_token
from services.chat_service import ChatService
from ai_system.config.environment import setup_environment_from_db
from ai_system.core.stream_manager import PersistentStreamManager, SimpleStreamCallback
from ai_system.core.main_agent import MainAgent
from ai_system.core.llm_handler import LLMHandler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["聊天系统"])


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
                await self.active_connections[work_id].send_text(message)
            except Exception as e:
                logger.error(f"发送WebSocket消息失败: {e}")
                self.disconnect(work_id)


manager = ConnectionManager()


@router.get("/work/{work_id}/history")
async def get_work_chat_history(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定工作的聊天记录（前端格式）"""
    try:
        chat_service = ChatService(db)

        # 验证用户权限（通过session验证）
        session = chat_service.get_session_by_work_id(work_id, current_user_id)
        if not session:
            # 如果没有session，尝试创建（可能是新work）
            from services.crud import get_work
            work = get_work(db, work_id)
            if not work or work.created_by != current_user_id:
                raise HTTPException(status_code=403, detail="无权限访问")

        # 获取聊天记录（前端格式，包含JSON卡片）
        messages = chat_service.get_work_chat_history_for_frontend(work_id)
        context = chat_service.get_work_context(work_id)

        return {
            "work_id": work_id,
            "messages": messages,
            "context": context
        }
    except Exception as e:
        logger.error(f"获取聊天记录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/work/{work_id}/history/raw")
async def get_work_chat_history_raw(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定工作的聊天记录（原始格式）"""
    try:
        chat_service = ChatService(db)

        # 验证用户权限
        session = chat_service.get_session_by_work_id(work_id, current_user_id)
        if not session:
            from services.crud import get_work
            work = get_work(db, work_id)
            if not work or work.created_by != current_user_id:
                raise HTTPException(status_code=403, detail="无权限访问")

        # 获取原始格式的聊天记录
        messages = chat_service.get_work_chat_history(work_id)
        context = chat_service.get_work_context(work_id)

        return {
            "work_id": work_id,
            "messages": messages,
            "context": context
        }
    except Exception as e:
        logger.error(f"获取原始聊天记录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/work/{work_id}/history/stats")
async def get_work_chat_statistics(
    work_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定工作的聊天统计信息"""
    try:
        chat_service = ChatService(db)

        # 验证用户权限
        session = chat_service.get_session_by_work_id(work_id, current_user_id)
        if not session:
            from services.crud import get_work
            work = get_work(db, work_id)
            if not work or work.created_by != current_user_id:
                raise HTTPException(status_code=403, detail="无权限访问")

        # 获取聊天统计信息
        stats = chat_service.get_chat_statistics(work_id)

        return {
            "work_id": work_id,
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"获取聊天统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
            from services.crud import get_work
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

                # 创建流式回调
                class WebSocketStreamCallback(SimpleStreamCallback):
                    def __init__(self, websocket: WebSocket, work_id: str, chat_service: ChatService):
                        super().__init__()
                        self.websocket = websocket
                        self.work_id = work_id
                        self.chat_service = chat_service
                        self.content = ""
                        self.current_message_id = None
                        self.json_blocks = []
                        logger.info(f"WebSocket回调初始化完成，work_id: {work_id}")

                    async def on_content(self, content: str):
                        """实时发送流式内容到WebSocket"""
                        self.content += content
                        try:
                            # 立即发送内容，不等待
                            await self.websocket.send_text(json.dumps({
                                'type': 'content',
                                'content': content
                            }))
                            logger.debug(f"发送流式内容: {repr(content[:30])}")
                            
                            # 让出控制权，确保其他任务能够执行
                            await asyncio.sleep(0.001)
                        except Exception as e:
                            logger.error(f"发送WebSocket内容失败: {e}")

                    async def on_message_complete(self, role: str, content: str):
                        """消息完成回调"""
                        try:
                            # 注意：这里不立即保存消息，因为MainAgent可能会多次调用这个方法
                            # 我们只在MainAgent完全执行完成后才保存消息
                            # 这里只是记录内容，不保存到数据库
                            logger.debug(
                                f"收到消息完成回调，角色: {role}, 长度: {len(content)}, JSON块数: {len(self.json_blocks)}")

                            # 不发送complete消息，因为MainAgent可能还会继续执行
                            # await self.websocket.send_text(json.dumps({
                            #     'type': 'complete',
                            #     'message': 'AI分析完成'
                            # }))

                        except Exception as e:
                            logger.error(f"处理消息完成失败: {e}")

                    async def on_json_block(self, block: dict):
                        """处理JSON格式的数据块"""
                        try:
                            # 添加到JSON块列表
                            self.json_blocks.append(block)

                            # 直接发送JSON块到WebSocket
                            await self.websocket.send_text(json.dumps({
                                'type': 'json_block',
                                'block': block
                            }))
                            logger.debug(
                                f"发送JSON块: {block.get('type', 'unknown')}")
                            
                            # 让出控制权，确保其他任务能够执行
                            await asyncio.sleep(0.001)
                        except Exception as e:
                            logger.error(f"发送JSON块失败: {e}")

                # 初始化AI环境
                env_manager = setup_environment_from_db(db)
                env_manager.initialize_system("brain")

                # 设置工作空间目录，确保代码执行器使用正确路径
                # 使用与其他服务一致的路径：../pa_data/workspaces/{work_id}
                workspace_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    "..",
                    "pa_data",
                    "workspaces",
                    work_id
                )
                env_manager.setup_workspace(workspace_path)

                # 创建流式回调和管理器
                ws_callback = WebSocketStreamCallback(
                    websocket, work_id, chat_service)
                stream_manager = PersistentStreamManager(
                    stream_callback=ws_callback,
                    chat_service=chat_service,  # 传入chat_service实例以支持消息持久化
                    session_id=str(session.session_id)
                )

                # 创建LLM处理器和主代理
                model_config = env_manager.config_manager.get_model_config(
                    "brain")
                llm_handler = LLMHandler(
                    model=str(model_config.model_id),
                    stream_manager=stream_manager
                )

                # 获取工作的模板ID
                template_id = None
                try:
                    from services.crud import get_work
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

                # 执行AI任务 - 使用异步任务避免阻塞
                try:
                    # 创建异步任务执行AI处理
                    ai_task = asyncio.create_task(
                        main_agent.run(message_data['problem'])
                    )
                    
                    # 等待AI任务完成
                    await ai_task

                    # AI处理完成后，手动保存最终的AI消息
                    # 获取最终的AI回复内容
                    final_content = ws_callback.content.strip()

                    # 保存最终的AI消息
                    if ws_callback.json_blocks:
                        # 保存JSON卡片格式的消息
                        chat_service.add_json_card_message(
                            work_id,
                            "assistant",
                            final_content,
                            ws_callback.json_blocks,
                            {"system_type": "brain"}
                        )
                        logger.info(
                            f"[PERSISTENCE] JSON卡片消息已保存，块数: {len(ws_callback.json_blocks)}")
                    else:
                        # 保存普通文本消息
                        chat_service.add_message(
                            work_id,
                            "assistant",
                            final_content,
                            {"system_type": "brain"}
                        )
                        logger.info(
                            f"[PERSISTENCE] 普通文本消息已保存，长度: {len(final_content)}")

                    # 发送完成消息到前端
                    await websocket.send_text(json.dumps({
                        'type': 'complete',
                        'message': 'AI分析完成'
                    }))
                    logger.info(f"[PERSISTENCE] AI处理完成，最终消息已保存到持久化存储")

                except Exception as e:
                    logger.error(f"AI任务执行失败: {e}")
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': f'AI处理失败: {str(e)}'
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
        from services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

        # 获取用户问题
        question = request.get("question", "")
        if not question:
            raise HTTPException(status_code=400, detail="缺少问题内容")

        # 初始化AI环境
        env_manager = setup_environment_from_db(db)
        env_manager.initialize_system("brain")

        # 创建LLM处理器
        model_config = env_manager.config_manager.get_model_config("brain")
        llm_handler = LLMHandler(
            model=str(model_config.model_id),
            stream_manager=None  # 不需要流式处理
        )

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
            from ai_system.core.stream_manager import SimpleStreamCallback

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
            from ai_system.core.stream_manager import StreamOutputManager
            title_stream_manager = StreamOutputManager(title_callback)

            # 创建新的LLMHandler实例用于标题生成
            title_llm_handler = LLMHandler(
                model=str(model_config.model_id),
                stream_manager=title_stream_manager
            )

            # 调用LLM生成标题
            assistant_message, _ = await title_llm_handler.process_stream(messages)
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
        from services.crud import get_work
        work = get_work(db, work_id)
        if not work or work.created_by != current_user_id:
            raise HTTPException(status_code=403, detail="无权限访问")

        # 获取新标题
        new_title = request.get("title", "")
        if not new_title or new_title.strip() == "":
            raise HTTPException(status_code=400, detail="标题不能为空")

        # 更新数据库中的标题
        from services.crud import update_work
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
