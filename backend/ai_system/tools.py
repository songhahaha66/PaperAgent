"""
核心工具实现
包括文件修改工具和Python代码执行工具
"""
import os
import json
import subprocess
import tempfile
from typing import Dict, Any, List
from pathlib import Path
from .tool_framework import Tool


class FileModifyTool(Tool):
    """文件修改工具"""
    
    def __init__(self):
        super().__init__(
            name="file_modify",
            description="修改工作空间中的文件内容",
            parameters={
                "type": "object",
                "properties": {
                    "work_id": {
                        "type": "string",
                        "description": "工作ID"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "相对于工作空间的文件路径"
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["read", "write", "append", "delete"],
                        "description": "操作类型"
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容（写操作时需要）"
                    }
                },
                "required": ["work_id", "file_path", "operation"]
            }
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行文件操作"""
        work_id = kwargs.get("work_id")
        file_path = kwargs.get("file_path")
        operation = kwargs.get("operation")
        content = kwargs.get("content")
        
        # 构建完整文件路径
        base_path = Path(f"../pa_data/workspaces/{work_id}")
        full_path = base_path / file_path
        
        try:
            if operation == "read":
                if not full_path.exists():
                    return {"error": f"文件不存在: {file_path}"}
                
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                
                return {
                    "success": True,
                    "content": file_content,
                    "file_path": str(file_path),
                    "operation": operation
                }
            
            elif operation == "write":
                # 确保目录存在
                full_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content or "")
                
                return {
                    "success": True,
                    "message": f"文件 {file_path} 写入成功",
                    "operation": operation
                }
            
            elif operation == "append":
                if not full_path.exists():
                    return {"error": f"文件不存在: {file_path}"}
                
                with open(full_path, 'a', encoding='utf-8') as f:
                    f.write(content or "")
                
                return {
                    "success": True,
                    "message": f"文件 {file_path} 追加成功",
                    "operation": operation
                }
            
            elif operation == "delete":
                if not full_path.exists():
                    return {"error": f"文件不存在: {file_path}"}
                
                full_path.unlink()
                
                return {
                    "success": True,
                    "message": f"文件 {file_path} 删除成功",
                    "operation": operation
                }
            
            else:
                return {"error": f"不支持的操作类型: {operation}"}
                
        except Exception as e:
            return {"error": f"文件操作失败: {str(e)}"}


class PythonCodeExecutionTool(Tool):
    """Python代码执行工具"""
    
    def __init__(self):
        super().__init__(
            name="python_execute",
            description="执行Python代码并返回结果",
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的Python代码"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "执行超时时间（秒），默认30秒",
                        "default": 30
                    }
                },
                "required": ["code"]
            }
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行Python代码"""
        code = kwargs.get("code")
        timeout = kwargs.get("timeout", 30)
        
        if not code:
            return {"error": "代码内容不能为空"}
        
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_file:
                temp_file.write(code)
                temp_file_path = temp_file.name
            
            try:
                # 执行Python代码
                result = subprocess.run(
                    ["python", temp_file_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=temp_file_path.parent
                )
                
                # 清理临时文件
                os.unlink(temp_file_path)
                
                return {
                    "success": True,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "return_code": result.returncode,
                    "execution_time": "实时执行"
                }
                
            except subprocess.TimeoutExpired:
                # 清理临时文件
                os.unlink(temp_file_path)
                return {"error": f"代码执行超时（{timeout}秒）"}
                
        except Exception as e:
            return {"error": f"代码执行失败: {str(e)}"}


class FileListTool(Tool):
    """文件列表工具"""
    
    def __init__(self):
        super().__init__(
            name="file_list",
            description="列出工作空间中的文件和目录",
            parameters={
                "type": "object",
                "properties": {
                    "work_id": {
                        "type": "string",
                        "description": "工作ID"
                    },
                    "path": {
                        "type": "string",
                        "description": "要列出的路径，默认为根目录",
                        "default": "."
                    }
                },
                "required": ["work_id"]
            }
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """列出文件"""
        work_id = kwargs.get("work_id")
        path = kwargs.get("path", ".")
        
        # 构建完整路径
        base_path = Path(f"../pa_data/workspaces/{work_id}")
        target_path = base_path / path
        
        try:
            if not target_path.exists():
                return {"error": f"路径不存在: {path}"}
            
            if not target_path.is_dir():
                return {"error": f"路径不是目录: {path}"}
            
            # 列出文件和目录
            items = []
            for item in target_path.iterdir():
                item_info = {
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None
                }
                items.append(item_info)
            
            return {
                "success": True,
                "path": path,
                "items": items,
                "total_count": len(items)
            }
            
        except Exception as e:
            return {"error": f"列出文件失败: {str(e)}"}


# 注册所有工具
def register_core_tools():
    """注册核心工具"""
    from .tool_framework import tool_registry
    
    tool_registry.register(FileModifyTool())
    tool_registry.register(PythonCodeExecutionTool())
    tool_registry.register(FileListTool())
    
    print("核心工具注册完成")
