"""
聊天系统API路由
集成流式传输和聊天记录持久化功能
"""

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
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
    ChatStreamRequest, 
    ChatSessionResponse, 
    ChatMessageResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["聊天系统"])

# WebSocket连接管理器
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        logger.info(f"WebSocket连接建立: {session_id}")

    def disconnect(self, session_id: str):
        if session_id in self.active_connections:
            del self.active_connections[session_id]
            logger.info(f"WebSocket连接断开: {session_id}")

    async def send_message(self, session_id: str, message: str):
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_text(message)
            except Exception as e:
                logger.error(f"发送WebSocket消息失败: {e}")
                self.disconnect(session_id)

manager = ConnectionManager()


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


@router.post("/session/{session_id}/stream")
async def chat_stream(
    session_id: str,
    request: ChatStreamRequest,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """流式聊天接口"""
    try:
        # 验证会话权限
        chat_service = ChatService(db)
        session = chat_service.get_session(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="聊天会话不存在"
            )
        
        # 检查用户权限
        if session.created_by != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权限访问此会话"
            )
        
        # 创建流式响应
        async def generate_stream():
            try:
                # 初始化AI环境
                env_manager = setup_environment_from_db(db)
                
                # 根据会话类型初始化系统
                system_type = session.system_type
                env_manager.initialize_system(system_type)
                
                # 创建流式管理器
                stream_manager = PersistentStreamManager(
                    chat_service=chat_service,
                    session_id=session_id
                )
                
                # 设置消息角色
                stream_manager.set_role("assistant")
                
                # 添加用户消息到聊天记录
                chat_service.add_message(
                    session_id=session_id,
                    role="user",
                    content=request.problem
                )
                
                # 创建LLM处理器和主代理
                # 使用数据库中的模型配置
                model_config = env_manager.config_manager.get_model_config(system_type)
                llm_handler = LLMHandler(
                    model=request.model or model_config.model_id,
                    stream_manager=stream_manager
                )
                
                main_agent = MainAgent(llm_handler, stream_manager)
                
                # 执行AI任务
                await stream_manager.print_xml_open("ai_response")
                await stream_manager.print_content("正在分析您的问题...")
                await stream_manager.print_content(f"\n\n问题: {request.problem}")
                await stream_manager.print_content("\n\n开始AI分析...")
                await stream_manager.print_xml_close("ai_response")
                
                # 运行主代理
                result = await main_agent.run(request.problem)
                
                # 完成消息持久化
                await stream_manager.finalize_message()
                
            except Exception as e:
                logger.error(f"流式聊天处理失败: {e}")
                yield f"错误: {str(e)}"
        
        return StreamingResponse(generate_stream(), media_type="text/plain")
        
    except Exception as e:
        logger.error(f"聊天流式接口失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"聊天接口失败: {str(e)}"
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
                        
                        async def on_content(self, content: str):
                            """处理流式内容 - 立即发送到前端"""
                            self.content += content
                            try:
                                # 立即发送内容到前端
                                await self.websocket.send_text(json.dumps({
                                    'type': 'content',
                                    'content': content
                                }))
                                # 添加小延迟确保前端能接收到
                                await asyncio.sleep(0.01)
                            except Exception as e:
                                logger.error(f"发送流式内容失败: {e}")
                        
                        async def on_message_complete(self, role: str, content: str):
                            """消息完成时的回调"""
                            logger.info(f"消息完成，角色: {role}, 长度: {len(content)}")
                            try:
                                # 发送完成消息
                                await self.websocket.send_text(json.dumps({
                                    'type': 'complete',
                                    'message': 'AI分析完成'
                                }))
                            except Exception as e:
                                logger.error(f"发送完成消息失败: {e}")
                    
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
                            # 运行主代理
                            result = await main_agent.run(message_data['problem'])
                            return result
                        except Exception as e:
                            logger.error(f"Agent执行失败: {e}")
                            raise e
                    
                    # 启动异步任务
                    agent_task = asyncio.create_task(run_agent_async())
                    
                    # 等待任务完成，但允许实时流式输出
                    try:
                        result = await agent_task
                        logger.info("AI任务执行完成")
                    except Exception as e:
                        logger.error(f"AI任务执行失败: {e}")
                        await websocket.send_text(json.dumps({
                            'type': 'error',
                            'message': f'AI任务执行失败: {str(e)}'
                        }))
                        return
                    
                    # 完成消息持久化
                    await stream_manager.finalize_message()
                    
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
