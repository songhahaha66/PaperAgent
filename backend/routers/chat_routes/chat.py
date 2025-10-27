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
from services.chat_services.chat_service import ChatService
from ai_system.config.environment import setup_environment_from_db
from ai_system.core_managers.stream_manager import PersistentStreamManager, SimpleStreamCallback
from ai_system.core_agents.main_agent import MainAgent
from ai_system.core_handlers.llm_handler import LLMHandler
from langchain_core.messages import HumanMessage
from ..utils import route_guard

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

        # 验证work权限 - 使用线程池避免阻塞事件循环
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

        # 注册连接
        manager.active_connections[work_id] = websocket

        # 创建聊天服务 - 使用线程池避免阻塞事件循环

        # 在线程池中执行同步数据库操作
        def init_chat_service():
            db = next(get_db())
            chat_service = ChatService(db)
            session = chat_service.create_or_get_work_session(work_id, user_id)
            return db, chat_service, session

        db, chat_service, session = await loop.run_in_executor(None, init_chat_service)

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

                        # 使用配置参数优化延迟
                        from ai_system.config.async_config import AsyncConfig
                        config = AsyncConfig.get_websocket_config()
                        if len(content) > config["content_yield_threshold"]:
                            await asyncio.sleep(config["content_yield_delay"])
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

                        # 减少延迟，JSON块通常较小，不需要额外延迟
                        # await asyncio.sleep(0.001)  # 移除不必要的延迟
                    except Exception as e:
                        logger.error(f"发送JSON块失败: {e}")

            # 初始化AI环境与工作空间
            env_manager = setup_environment_from_db(db, work_id)
            model_config = env_manager.config_manager.get_model_config("brain")
            codeagent_model_config = env_manager.config_manager.get_model_config("code")

            # 创建工作空间目录 - 使用绝对路径
            backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            project_root = os.path.dirname(backend_dir)
            workspace_dir = os.path.join(project_root, "pa_data", "workspaces", work_id)
            os.makedirs(workspace_dir, exist_ok=True)

            # 创建流式回调和管理器
            ws_callback = WebSocketStreamCallback(
                websocket, work_id, chat_service)
            stream_manager = PersistentStreamManager(
                stream_callback=ws_callback,
                chat_service=chat_service,  # 传入chat_service实例以支持消息持久化
                session_id=str(session.session_id)
            )

            # 创建支持多AI提供商的LLM处理器
            llm_handler = LLMHandler(
                model_config=model_config,
                stream_manager=stream_manager
            )

            # 获取codeagent的LLM实例
            codeagent_llm = None
            if codeagent_model_config:
                from ai_system.core_handlers.llm_providers import create_smolagents_model_from_config
                try:
                    codeagent_llm = create_smolagents_model_from_config(codeagent_model_config)
                    logger.info(f"成功创建SmolAgents模型: {codeagent_llm}")
                except Exception as e:
                    logger.warning(f"创建SmolAgents模型失败: {e}")
                    # 回退到LangChain LLM，但MainAgent会处理兼容性
                    from ai_system.core_handlers.llm_providers import create_llm_from_model_config
                    try:
                        codeagent_llm = create_llm_from_model_config(codeagent_model_config)
                        logger.info(f"回退使用LangChain LLM: {codeagent_llm}")
                    except Exception as e2:
                        logger.error(f"创建LangChain LLM也失败: {e2}")
                        codeagent_llm = llm_handler.get_llm_instance()  # 备用
            else:
                logger.warning("未找到codeagent配置，使用brain LLM")
                codeagent_llm = llm_handler.get_llm_instance()

            # 最终确保codeagent_llm不为None
            if codeagent_llm is None:
                logger.error("codeagent_llm仍然为None，强制使用brain LLM")
                # brain LLM是LangChain格式，需要适配器处理
                codeagent_llm = llm_handler.get_llm_instance()

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

            # 创建MainAgent，传入workspace_dir、work_id、template_id和codeagent_llm
            main_agent = MainAgent(llm_handler.get_llm_instance(), stream_manager, workspace_dir, work_id, template_id, codeagent_llm)

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

            # 立即保存用户消息到持久化存储，确保历史记录顺序正确
            await stream_manager.save_user_message(message_data['problem'])
            logger.info(f"[PERSISTENCE] 用户消息已立即保存到持久化存储")

            # 执行AI任务 - 使用异步任务避免阻塞
            try:
                # 检查WebSocket连接状态
                if not manager.is_connected(work_id):
                    logger.error(f"WebSocket连接已断开，无法执行AI任务: {work_id}")
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': 'WebSocket连接已断开，请重新连接'
                    }))
                    break

                # 创建异步任务执行AI处理（MainAgent.run内部会添加用户消息）
                ai_task = asyncio.create_task(
                    main_agent.run(message_data['problem'])
                )

                # 等待AI任务完成，添加超时保护
                try:
                    await asyncio.wait_for(ai_task, timeout=300)  # 5分钟超时
                except asyncio.TimeoutError:
                    logger.error(f"AI任务执行超时: {work_id}")
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': 'AI任务执行超时，请重试'
                    }))
                    break

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
        model_config = env_manager.config_manager.get_model_config("brain")

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


