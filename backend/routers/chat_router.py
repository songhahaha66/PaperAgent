"""
聊天系统API路由
集成流式传输和聊天记录持久化功能
"""

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect

from sqlalchemy.orm import Session
from typing import Optional
import asyncio
import logging
import json

from database.database import get_db
from auth.auth import get_current_user
from models.models import User
from services.chat_service import ChatService
from ai_system.config.environment import setup_environment_from_db
from ai_system.core.stream_manager import PersistentStreamManager, SimpleStreamCallback
from ai_system.core.main_agent import MainAgent
from ai_system.core.llm_handler import LLMHandler
from schemas.schemas import (
    ChatSessionCreateRequest, 
    ChatSessionResponse, 
    ChatMessageResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["聊天系统"])

# WebSocket连接管理器
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.connection_states: dict[str, dict] = {}
        self.background_tasks: dict[str, asyncio.Task] = {}  # 存储后台任务

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        self.connection_states[session_id] = {
            'connected_at': asyncio.get_event_loop().time(),
            'last_activity': asyncio.get_event_loop().time(),
            'is_active': True
        }
        logger.info(f"WebSocket连接建立: {session_id}")

    def disconnect(self, session_id: str):
        # 取消相关的后台任务
        if session_id in self.background_tasks:
            task = self.background_tasks[session_id]
            if not task.done():
                task.cancel()
            del self.background_tasks[session_id]
        
        if session_id in self.active_connections:
            del self.active_connections[session_id]
        if session_id in self.connection_states:
            del self.connection_states[session_id]
        logger.info(f"WebSocket连接断开: {session_id}")

    async def send_message(self, session_id: str, message: str):
        if session_id in self.active_connections:
            websocket = self.active_connections[session_id]
            try:
                # 直接发送，让WebSocket自己处理连接状态
                await websocket.send_text(message)
                # 发送成功，更新最后活动时间
                if session_id in self.connection_states:
                    self.connection_states[session_id]['last_activity'] = asyncio.get_event_loop().time()
            except Exception as e:
                logger.error(f"发送WebSocket消息失败: {e}")
                # 发送失败，认为连接已断开
                self.disconnect(session_id)
        else:
            logger.warning(f"WebSocket连接 {session_id} 不存在")

    def is_connected(self, session_id: str) -> bool:
        """检查连接是否有效 - 简化版本，避免误判"""
        return session_id in self.active_connections

    def add_background_task(self, session_id: str, task: asyncio.Task):
        """添加后台任务"""
        self.background_tasks[session_id] = task
        logger.info(f"为会话 {session_id} 添加后台任务")

    def cleanup_inactive_connections(self, timeout: int = 300):
        """清理不活跃的连接（5分钟无活动）"""
        current_time = asyncio.get_event_loop().time()
        to_remove = []
        
        for session_id, state in self.connection_states.items():
            if current_time - state['last_activity'] > timeout:
                to_remove.append(session_id)
        
        for session_id in to_remove:
            logger.info(f"清理不活跃连接: {session_id}")
            self.disconnect(session_id)

    def cleanup_completed_tasks(self):
        """清理已完成的后台任务"""
        to_remove = []
        for session_id, task in self.background_tasks.items():
            if task.done():
                to_remove.append(session_id)
        
        for session_id in to_remove:
            del self.background_tasks[session_id]
            logger.debug(f"清理已完成任务: {session_id}")

    def get_active_connections_count(self) -> int:
        """获取活跃连接数"""
        return len(self.active_connections)

    def get_background_tasks_count(self) -> int:
        """获取后台任务数"""
        return len(self.background_tasks)

manager = ConnectionManager()

# 定期清理任务
async def cleanup_task():
    """定期清理任务，确保系统资源得到正确管理"""
    while True:
        try:
            await asyncio.sleep(60)  # 每分钟执行一次
            manager.cleanup_inactive_connections()
            manager.cleanup_completed_tasks()
            
            # 记录连接状态
            active_connections = manager.get_active_connections_count()
            background_tasks = manager.get_background_tasks_count()
            if active_connections > 0 or background_tasks > 0:
                logger.info(f"当前活跃连接: {active_connections}, 后台任务: {background_tasks}")
                
        except Exception as e:
            logger.error(f"清理任务执行失败: {e}")

# 启动清理任务
@router.on_event("startup")
async def startup_event():
    """应用启动时启动清理任务"""
    asyncio.create_task(cleanup_task())
    logger.info("WebSocket连接管理器清理任务已启动")


@router.post("/session/create", response_model=ChatSessionResponse)
async def create_chat_session(
    request: ChatSessionCreateRequest,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建新的聊天会话"""
    try:
        chat_service = ChatService(db)
        session = chat_service.create_chat_session(
            work_id=request.work_id,
            system_type=request.system_type,
            user_id=current_user_id,
            title=request.title
        )
        return ChatSessionResponse.from_orm(session)
    except Exception as e:
        logger.error(f"创建聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建聊天会话失败: {str(e)}"
        )





@router.get("/session/{session_id}/history", response_model=list[ChatMessageResponse])
async def get_chat_history(
    session_id: str,
    limit: int = 50,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取聊天历史"""
    try:
        # 验证会话权限
        chat_service = ChatService(db)
        session = chat_service.get_session(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="聊天会话不存在"
            )
        
        if session.created_by != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权限访问此会话"
            )
        
        history = chat_service.get_chat_history(session_id, limit)
        return [ChatMessageResponse.from_orm(msg) for msg in history]
    except Exception as e:
        logger.error(f"获取聊天历史失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取聊天历史失败: {str(e)}"
        )


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_chat_sessions(
    work_id: Optional[str] = None,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """列出用户的聊天会话"""
    try:
        chat_service = ChatService(db)
        sessions = chat_service.list_user_sessions(current_user_id, work_id)
        
        # 调试信息
        logger.info(f"找到 {len(sessions)} 个会话，用户ID: {current_user_id}")
        if sessions:
            logger.info(f"第一个会话类型: {type(sessions[0])}")
        
        return [ChatSessionResponse.from_orm(session) for session in sessions]
    except Exception as e:
        logger.error(f"列出聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"列出聊天会话失败: {str(e)}"
        )


@router.put("/session/{session_id}/title")
async def update_session_title(
    session_id: str,
    title: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新会话标题"""
    try:
        chat_service = ChatService(db)
        success = chat_service.update_session_title(session_id, title)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权限"
            )
        return {"status": "success", "message": "标题更新成功"}
    except Exception as e:
        logger.error(f"更新会话标题失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新会话标题失败: {str(e)}"
        )


@router.delete("/session/{session_id}")
async def delete_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除聊天会话"""
    try:
        chat_service = ChatService(db)
        success = chat_service.delete_session(session_id, current_user.id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权限"
            )
        return {"status": "success", "message": "会话删除成功"}
    except Exception as e:
        logger.error(f"删除聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除聊天会话失败: {str(e)}"
        )


@router.post("/session/{session_id}/reset")
async def reset_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """重置聊天会话（清空消息历史）"""
    try:
        chat_service = ChatService(db)
        session = chat_service.get_session(session_id)
        if not session or session.created_by != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权限"
            )
        
        # 删除所有消息
        messages = chat_service.get_chat_history(session_id, limit=1000)
        for message in messages:
            # 这里需要实现删除消息的方法
            pass
        
        return {"status": "success", "message": "会话已重置"}
    except Exception as e:
        logger.error(f"重置聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"重置聊天会话失败: {str(e)}"
        )


@router.websocket("/ws/{session_id}")
async def websocket_chat(
    websocket: WebSocket,
    session_id: str
):
    """WebSocket聊天接口"""
    try:
        # 接受连接
        await websocket.accept()
        
        # 等待客户端发送认证信息
        try:
            auth_data = await websocket.receive_text()
            auth_info = json.loads(auth_data)
            
            if 'token' not in auth_info:
                await websocket.send_text(json.dumps({
                    'type': 'error',
                    'message': '缺少认证token'
                }))
                await websocket.close()
                return
                
            token = auth_info['token']
            
            # 这里应该验证JWT token，暂时简化处理
            # TODO: 实现完整的JWT验证逻辑
            
        except Exception as e:
            await websocket.send_text(json.dumps({
                'type': 'error',
                'message': '认证信息格式错误'
            }))
            await websocket.close()
            return
        
        try:
            while True:
                # 接收用户消息
                data = await websocket.receive_text()
                message_data = json.loads(data)
                
                # 验证消息格式
                if 'problem' not in message_data:
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': '消息格式错误'
                    }))
                    continue
                
                # 获取数据库会话
                db = next(get_db())
                chat_service = ChatService(db)
                session = chat_service.get_session(session_id)
                
                if not session:
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': '聊天会话不存在'
                    }))
                    continue
                
                # 添加用户消息到聊天记录
                chat_service.add_message(
                    session_id=session_id,
                    role="user",
                    content=message_data['problem']
                )
                
                # 发送开始消息
                await websocket.send_text(json.dumps({
                    'type': 'start',
                    'message': '开始AI分析...'
                }))
                
                try:
                    # 初始化AI环境
                    env_manager = setup_environment_from_db(db)
                    system_type = session.system_type
                    env_manager.initialize_system(system_type)
                    
                    # 创建流式管理器（适配WebSocket）
                    class WebSocketStreamCallback:
                        """WebSocket专用的流式输出回调"""
                        
                        def __init__(self, websocket: WebSocket, session_id: str):
                            self.websocket = websocket
                            self.session_id = session_id
                            self.content = ""
                            self.connection_manager = manager
                        
                        async def on_content(self, content: str):
                            """处理流式内容 - 立即发送到前端"""
                            self.content += content
                            try:
                                # 直接发送内容到前端，让WebSocket自己处理连接状态
                                await self.websocket.send_text(json.dumps({
                                    'type': 'content',
                                    'content': content
                                }))
                                # 添加小延迟确保前端能接收到
                                await asyncio.sleep(0.01)
                            except Exception as e:
                                logger.error(f"发送流式内容失败: {e}")
                                # 连接断开，停止发送
                                self.connection_manager.disconnect(self.session_id)
                                raise e
                        
                        async def on_message_complete(self, role: str, content: str):
                            """消息完成时的回调"""
                            logger.info(f"消息完成，角色: {role}, 长度: {len(content)}")
                            try:
                                # 直接发送完成消息，让WebSocket自己处理连接状态
                                await self.websocket.send_text(json.dumps({
                                    'type': 'complete',
                                    'message': 'AI分析完成'
                                }))
                            except Exception as e:
                                logger.error(f"发送完成消息失败: {e}")
                                # 连接断开，停止发送
                                self.connection_manager.disconnect(self.session_id)
                                raise e
                    
                    # 创建WebSocket流式回调
                    ws_callback = WebSocketStreamCallback(websocket, session_id)
                    
                    # 创建流式管理器，使用WebSocket回调
                    stream_manager = PersistentStreamManager(
                        stream_callback=ws_callback,
                        chat_service=chat_service,
                        session_id=session_id
                    )
                    
                    # 设置消息角色
                    stream_manager.set_role("assistant")
                    
                    # 创建LLM处理器和主代理
                    model_config = env_manager.config_manager.get_model_config(system_type)
                    model_id = message_data.get('model') or model_config.model_id
                    llm_handler = LLMHandler(
                        model=model_id,
                        stream_manager=stream_manager
                    )
                    
                    main_agent = MainAgent(llm_handler, stream_manager)
                    
                    # 执行AI任务 - 使用异步方式
                    await stream_manager.print_xml_open("ai_response")
                    await stream_manager.print_content("正在分析您的问题...")
                    await stream_manager.print_content(f"\n\n问题: {message_data['problem']}")
                    await stream_manager.print_content("\n\n开始AI分析...")
                    await stream_manager.print_xml_close("ai_response")
                    
                    # 在后台异步运行主代理，避免阻塞WebSocket
                    async def run_agent_async():
                        try:
                            # 使用线程池执行可能阻塞的AI任务
                            loop = asyncio.get_event_loop()
                            
                            # 检查main_agent.run是否是异步方法
                            if asyncio.iscoroutinefunction(main_agent.run):
                                # 如果是异步方法，直接调用
                                result = await main_agent.run(message_data['problem'])
                            else:
                                # 如果是同步方法，使用线程池
                                result = await loop.run_in_executor(
                                    None,  # 使用默认线程池
                                    lambda: main_agent.run(message_data['problem'])
                                )
                            return result
                        except asyncio.CancelledError:
                            logger.info("AI任务被取消")
                            raise
                        except Exception as e:
                            logger.error(f"Agent执行失败: {e}")
                            raise e
                    
                    # 启动异步任务，不等待完成
                    agent_task = asyncio.create_task(run_agent_async())
                    
                    # 将任务添加到连接管理器，便于管理
                    manager.add_background_task(session_id, agent_task)
                    
                    # 不等待任务完成，立即返回，让AI在后台运行
                    # 这样可以避免阻塞其他API请求
                    logger.info("AI任务已在后台启动，WebSocket连接保持活跃")
                    
                    # 设置任务完成后的清理
                    def cleanup_agent_task(task):
                        try:
                            if not task.cancelled():
                                result = task.result()
                                logger.info("AI任务执行完成")
                        except asyncio.CancelledError:
                            logger.info("AI任务被取消")
                        except Exception as e:
                            logger.error(f"AI任务执行失败: {e}")
                            # 如果WebSocket还连接着，发送错误消息
                            if manager.is_connected(session_id):
                                asyncio.create_task(websocket.send_text(json.dumps({
                                    'type': 'error',
                                    'message': f'AI任务执行失败: {str(e)}'
                                })))
                    
                    agent_task.add_done_callback(cleanup_agent_task)
                    
                    # 完成消息持久化
                    await stream_manager.finalize_message()
                    
                    # 立即返回，不等待AI任务完成
                    logger.info(f"WebSocket处理完成，AI任务在后台运行，会话ID: {session_id}")
                    
                except Exception as e:
                    logger.error(f"AI处理失败: {e}")
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': f'AI处理失败: {str(e)}'
                    }))
                
        except WebSocketDisconnect:
            logger.info(f"WebSocket连接断开: {session_id}")
        except Exception as e:
            logger.error(f"WebSocket处理失败: {e}")
            try:
                await websocket.send_text(json.dumps({
                    'type': 'error',
                    'message': f'处理失败: {str(e)}'
                }))
            except:
                pass
            
    except Exception as e:
        logger.error(f"WebSocket连接失败: {e}")
        try:
            await websocket.close()
        except:
            pass
