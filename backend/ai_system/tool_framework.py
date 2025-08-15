"""
工具调用框架
实现工具注册、管理和执行系统
"""
from typing import Dict, Any, Callable, List, Optional, Union
from abc import ABC, abstractmethod
import json
import asyncio
from datetime import datetime


class Tool(ABC):
    """工具基类"""
    
    def __init__(self, name: str, description: str, parameters: Dict[str, Any]):
        self.name = name
        self.description = description
        self.parameters = parameters
    
    @abstractmethod
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行工具"""
        pass
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为OpenAI工具格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self.tools: Dict[str, Tool] = {}
    
    def register(self, tool: Tool):
        """注册工具"""
        self.tools[tool.name] = tool
    
    def unregister(self, tool_name: str):
        """注销工具"""
        if tool_name in self.tools:
            del self.tools[tool_name]
    
    def get_tool(self, tool_name: str) -> Optional[Tool]:
        """获取工具"""
        return self.tools.get(tool_name)
    
    def get_all_tools(self) -> List[Tool]:
        """获取所有工具"""
        return list(self.tools.values())
    
    def get_tools_dict(self) -> List[Dict[str, Any]]:
        """获取所有工具的OpenAI格式"""
        return [tool.to_dict() for tool in self.tools.values()]


class ToolExecutor:
    """工具执行器"""
    
    def __init__(self, tool_registry: ToolRegistry):
        self.tool_registry = tool_registry
    
    async def execute_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行工具调用
        
        Args:
            tool_call: OpenAI格式的工具调用
        
        Returns:
            执行结果
        """
        try:
            tool_name = tool_call.get("function", {}).get("name")
            if not tool_name:
                return {"error": "工具名称缺失"}
            
            tool = self.tool_registry.get_tool(tool_name)
            if not tool:
                return {"error": f"未找到工具: {tool_name}"}
            
            # 解析参数
            arguments = tool_call.get("function", {}).get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    return {"error": f"参数解析失败: {arguments}"}
            
            # 执行工具
            result = await tool.execute(**arguments)
            
            return {
                "tool_call_id": tool_call.get("id"),
                "tool_name": tool_name,
                "result": result,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "error": f"工具执行失败: {str(e)}",
                "tool_call_id": tool_call.get("id"),
                "timestamp": datetime.now().isoformat()
            }
    
    async def execute_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        执行多个工具调用
        
        Args:
            tool_calls: 工具调用列表
        
        Returns:
            执行结果列表
        """
        if not tool_calls:
            return []
        
        # 并发执行所有工具调用
        tasks = [self.execute_tool_call(tool_call) for tool_call in tool_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常结果
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append({
                    "error": f"工具执行异常: {str(result)}",
                    "tool_call_id": tool_calls[i].get("id"),
                    "timestamp": datetime.now().isoformat()
                })
            else:
                processed_results.append(result)
        
        return processed_results


# 全局工具注册表和执行器
tool_registry = ToolRegistry()
tool_executor = ToolExecutor(tool_registry)
