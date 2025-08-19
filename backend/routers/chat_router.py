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
    """获取指定工作的聊天记录"""
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
        
        # 获取聊天记录
        messages = chat_service.get_work_chat_history(work_id)
        context = chat_service.get_work_context(work_id)
        
        return {
            "work_id": work_id,
            "messages": messages,
            "context": context
        }
    except Exception as e:
        logger.error(f"获取聊天记录失败: {e}")
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
                        except Exception as e:
                            logger.error(f"发送WebSocket内容失败: {e}")
                    
                    async def on_message_complete(self, role: str, content: str):
                        """消息完成回调"""
                        try:
                            # 注意：不在这里保存AI回复，由PersistentStreamManager统一处理
                            # 避免重复保存同一条消息
                            await self.websocket.send_text(json.dumps({
                                'type': 'complete',
                                'message': 'AI分析完成'
                            }))
                            logger.info(f"AI消息完成，角色: {role}, 长度: {len(content)}")
                        except Exception as e:
                            logger.error(f"处理消息完成失败: {e}")
                
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
                ws_callback = WebSocketStreamCallback(websocket, work_id, chat_service)
                stream_manager = PersistentStreamManager(
                    stream_callback=ws_callback,
                    chat_service=chat_service,  # 传入chat_service实例以支持消息持久化
                    session_id=str(session.session_id)
                )
                
                # 创建LLM处理器和主代理
                model_config = env_manager.config_manager.get_model_config("brain")
                llm_handler = LLMHandler(
                    model=str(model_config.model_id),
                    stream_manager=stream_manager
                )
                
                # 创建MainAgent，传入work_id
                main_agent = MainAgent(llm_handler, stream_manager, work_id)
                
                # 先加载真正的历史消息（排除当前这次对话）
                history_messages = chat_service.get_work_chat_history(work_id, limit=20)
                if history_messages:
                    # 过滤掉可能包含当前消息的历史记录
                    # 只加载真正属于历史的消息
                    history_data = [{"role": msg["role"], "content": msg["content"]} 
                                   for msg in history_messages]
                    main_agent.load_conversation_history(history_data)
                
                # 先执行AI任务，处理完成后保存用户消息到历史
                try:
                    # 检查main_agent.run是否是异步函数
                    if asyncio.iscoroutinefunction(main_agent.run):
                        await main_agent.run(message_data['problem'])
                    else:
                        # 如果是同步函数，使用线程池执行
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, lambda: main_agent.run(message_data['problem']))
                    
                    # AI处理完成后，保存用户消息到历史
                    await stream_manager.save_user_message(message_data['problem'])
                    logger.info(f"[PERSISTENCE] AI处理完成，用户消息已保存到历史")
                    
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