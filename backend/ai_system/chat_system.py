"""
AI对话系统
实现多系统协作对话、上下文管理和工具调用集成
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import json
from .litellm_client import litellm_client
from .tool_framework import tool_executor, tool_registry
from .tools import register_core_tools


class ChatMessage:
    """聊天消息"""
    
    def __init__(self, role: str, content: str, timestamp: datetime = None, tool_calls: List[Dict] = None, tool_results: List[Dict] = None):
        self.role = role  # user, assistant, system, tool
        self.content = content
        self.timestamp = timestamp or datetime.now()
        self.tool_calls = tool_calls or []
        self.tool_results = tool_results or []
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results
        }
    
    def to_openai_format(self) -> Dict[str, Any]:
        """转换为OpenAI格式"""
        message = {
            "role": self.role,
            "content": self.content
        }
        
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        
        if self.role == "tool" and self.tool_results:
            message["tool_call_id"] = self.tool_results[0].get("tool_call_id")
        
        return message


class ChatSession:
    """聊天会话"""
    
    def __init__(self, session_id: str, work_id: str, system_type: str = "brain"):
        self.session_id = session_id
        self.work_id = work_id
        self.system_type = system_type  # brain, code, writing
        self.messages: List[ChatMessage] = []
        self.context: Dict[str, Any] = {}
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def add_message(self, message: ChatMessage):
        """添加消息"""
        self.messages.append(message)
        self.updated_at = datetime.now()
    
    def get_context_messages(self, max_messages: int = 10) -> List[Dict[str, Any]]:
        """获取上下文消息（OpenAI格式）"""
        # 添加系统消息
        system_message = {
            "role": "system",
            "content": f"你是一个{self.system_type}系统，负责处理用户请求。你可以使用可用的工具来完成任务。"
        }
        
        # 获取最近的用户和助手消息
        recent_messages = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        
        # 转换为OpenAI格式
        openai_messages = [system_message]
        for msg in recent_messages:
            if msg.role in ["user", "assistant"]:
                openai_messages.append(msg.to_openai_format())
        
        return openai_messages
    
    def get_tools(self) -> List[Dict[str, Any]]:
        """获取可用工具"""
        return tool_registry.get_tools_dict()


class ChatSystem:
    """聊天系统管理器"""
    
    def __init__(self):
        self.sessions: Dict[str, ChatSession] = {}
        # 注册核心工具
        register_core_tools()
    
    def create_session(self, session_id: str, work_id: str, system_type: str = "brain") -> ChatSession:
        """创建新的聊天会话"""
        session = ChatSession(session_id, work_id, system_type)
        self.sessions[session_id] = session
        return session
    
    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """获取聊天会话"""
        return self.sessions.get(session_id)
    
    def delete_session(self, session_id: str):
        """删除聊天会话"""
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    async def process_message(
        self,
        session_id: str,
        user_message: str,
        work_id: str,
        system_type: str = "brain"
    ) -> Dict[str, Any]:
        """
        处理用户消息
        
        Args:
            session_id: 会话ID
            user_message: 用户消息
            work_id: 工作ID
            system_type: 系统类型
        
        Returns:
            处理结果
        """
        # 获取或创建会话
        session = self.get_session(session_id)
        if not session:
            session = self.create_session(session_id, work_id, system_type)
        
        # 添加用户消息
        user_msg = ChatMessage("user", user_message)
        session.add_message(user_msg)
        
        try:
            # 获取上下文消息和工具
            context_messages = session.get_context_messages()
            tools = session.get_tools()
            
            # 调用AI模型
            ai_response = await litellm_client.chat_completion(
                model_type=system_type,
                messages=context_messages,
                tools=tools,
                tool_choice="auto"
            )
            
            # 处理工具调用
            tool_results = []
            if ai_response.get("tool_calls"):
                tool_results = await tool_executor.execute_tool_calls(ai_response["tool_calls"])
                
                # 如果有工具调用结果，再次调用AI模型
                if tool_results:
                    # 添加工具调用结果到消息
                    tool_message = ChatMessage(
                        role="tool",
                        content="工具执行完成",
                        tool_results=tool_results
                    )
                    session.add_message(tool_message)
                    
                    # 构建包含工具结果的上下文
                    tool_context = context_messages + [
                        {"role": "assistant", "content": ai_response["content"], "tool_calls": ai_response["tool_calls"]},
                        {"role": "tool", "content": "工具执行完成", "tool_call_id": tool_results[0].get("tool_call_id")}
                    ]
                    
                    # 再次调用AI模型获取最终响应
                    final_response = await litellm_client.chat_completion(
                        model_type=system_type,
                        messages=tool_context
                    )
                    
                    ai_response = final_response
            
            # 添加AI响应消息
            assistant_msg = ChatMessage(
                role="assistant",
                content=ai_response["content"],
                tool_calls=ai_response.get("tool_calls", [])
            )
            session.add_message(assistant_msg)
            
            return {
                "success": True,
                "response": ai_response["content"],
                "tool_calls": ai_response.get("tool_calls", []),
                "tool_results": tool_results,
                "session_id": session_id,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            error_msg = ChatMessage("assistant", f"处理消息时发生错误: {str(e)}")
            session.add_message(error_msg)
            
            return {
                "success": False,
                "error": str(e),
                "session_id": session_id,
                "timestamp": datetime.now().isoformat()
            }
    
    def get_session_history(self, session_id: str, max_messages: int = 50) -> List[Dict[str, Any]]:
        """获取会话历史"""
        session = self.get_session(session_id)
        if not session:
            return []
        
        messages = session.messages[-max_messages:] if len(session.messages) > max_messages else session.messages
        return [msg.to_dict() for msg in messages]


# 全局聊天系统实例
chat_system = ChatSystem()
