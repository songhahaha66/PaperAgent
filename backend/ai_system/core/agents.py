"""
AI代理系统
包含Agent基类、CodeAgent等核心代理类
"""

import logging
import json
import os
from typing import List, Dict, Any, Callable
from abc import ABC, abstractmethod
from datetime import datetime

from .llm_handler import LLMHandler
from .stream_manager import StreamOutputManager
from ..tools.code_executor import CodeExecutor

logger = logging.getLogger(__name__)


class Agent(ABC):
    """Agent 的基类，定义通用接口。"""

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager = None):
        self.llm_handler = llm_handler
        self.stream_manager = stream_manager
        self.messages: List[Dict[str, Any]] = []
        self.tools: List[Dict[str, Any]] = []
        self.available_functions: Dict[str, Callable] = {}

    def _register_tool(self, func: Callable, tool_definition: Dict):
        """注册一个工具及其实现函数。"""
        self.tools.append(tool_definition)
        # 使用工具定义中的名称作为键，而不是函数名
        tool_name = tool_definition["function"]["name"]
        self.available_functions[tool_name] = func
        logger.debug(f"注册工具: {tool_name} -> {func.__name__}")

    @abstractmethod
    async def run(self, *args, **kwargs):
        """每个 Agent 子类必须实现 run 方法。"""
        raise NotImplementedError("每个 Agent 子类必须实现 run 方法。")


class CodeAgent(Agent):
    """
    代码手 LLM Agent，负责生成、保存和执行代码。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager, workspace_dir: str):
        super().__init__(llm_handler, stream_manager)
        
        # 工作空间目录是必需的
        if not workspace_dir:
            raise ValueError("必须传入workspace_dir参数，指定具体的工作空间目录（包含work_id）")
        
        self.executor = CodeExecutor(stream_manager, workspace_dir)
        self._setup()
        logger.info("CodeAgent初始化完成")

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        self.messages = [{
            "role": "system",
            "content": (
                "你是一个专业的代码生成和执行助手。你的工作流程是：\n"
                "1. 分析用户任务，生成相应的Python代码\n"
                "2. 使用 save_and_execute 工具保存代码并立即执行\n"
                "3. 仔细分析执行结果和错误信息\n"
                "4. 如果代码有错误或需要优化：\n"
                "   - 使用 edit_code_file 工具修改代码\n"
                "   - 使用 execute_file 工具重新执行修改后的代码\n"
                "   - 重复修改和执行，直到得到正确结果\n"
                "5. 给出最终答案\n\n"
                "**重要策略：**\n"
                "**推荐使用 save_and_execute 工具，一次性完成保存和执行！**\n"
                "**当代码执行失败时，仔细分析错误信息，然后修改代码重试！**\n"
                "**代码应该包含必要的导入语句和完整的逻辑。**\n"
                "**文件操作使用相对路径即可，例如：plt.savefig('outputs/plots/filename.png')**\n\n"
                "**工具使用顺序：**\n"
                "- save_and_execute: 保存代码并立即执行（推荐）\n"
                "- execute_code: 直接执行代码内容（不保存）\n"
                "- execute_file: 执行已保存的代码文件\n"
                "- edit_code_file: 修改现有代码文件（修复错误时使用）\n"
                "- list_code_files: 列出工作空间中的所有代码文件\n\n"
                "**错误处理策略：**\n"
                "1. 当代码执行失败时，仔细阅读错误信息\n"
                "2. 识别错误类型：语法错误、导入错误、逻辑错误等\n"
                "3. 使用 edit_code_file 修复问题\n"
                "4. 使用 execute_file 重新执行修复后的代码\n"
                "5. 如果还有问题，继续修改直到成功\n"
                "6. 记录修改历史，避免重复错误\n\n"
                "**重复执行直到成功**"
            )
        }]
        
        # 注册代码保存并执行工具（推荐）
        save_and_execute_tool = {
            "type": "function",
            "function": {
                "name": "save_and_execute",
                "description": "保存Python代码到文件并立即执行，推荐使用此工具",
                "parameters": {
                    "type": "object", 
                    "properties": {
                        "code_content": {
                            "type": "string", 
                            "description": "要保存和执行的Python代码内容"
                        },
                        "filename": {
                            "type": "string", 
                            "description": "文件名（不需要.py后缀）"
                        }
                    },
                    "required": ["code_content", "filename"],
                },
            },
        }
        
        # 注册直接执行代码工具
        execute_code_tool = {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": "直接执行Python代码内容，不保存到文件",
                "parameters": {
                    "type": "object", 
                    "properties": {
                        "code_content": {
                            "type": "string", 
                            "description": "要执行的Python代码内容"
                        }
                    },
                    "required": ["code_content"],
                },
            },
        }
        
        # 注册执行文件工具
        execute_file_tool = {
            "type": "function",
            "function": {
                "name": "execute_file",
                "description": "执行指定的Python代码文件",
                "parameters": {
                    "type": "object", 
                    "properties": {
                        "file_path": {
                            "type": "string", 
                            "description": "要执行的代码文件路径（相对于工作空间，例如：code_files/calculation.py）"
                        }
                    },
                    "required": ["file_path"],
                },
            },
        }
        
        # 注册代码修改工具
        edit_code_tool = {
            "type": "function",
            "function": {
                "name": "edit_code_file",
                "description": "修改已存在的Python代码文件，主要用于修复代码错误、优化逻辑或添加新功能。当代码执行失败时，使用此工具修复问题后重新执行。",
                "parameters": {
                    "type": "object", 
                    "properties": {
                        "filename": {
                            "type": "string", 
                            "description": "要修改的文件名（不需要.py后缀），例如：calculation"
                        },
                        "new_code_content": {
                            "type": "string", 
                            "description": "修复后的完整代码内容，包含所有必要的导入语句和完整的逻辑"
                        }
                    },
                    "required": ["filename", "new_code_content"],
                },
            },
        }
        
        # 注册代码文件列表工具
        list_files_tool = {
            "type": "function",
            "function": {
                "name": "list_code_files",
                "description": "列出工作空间中的所有代码文件",
                "parameters": {
                    "type": "object", 
                    "properties": {},
                    "required": [],
                },
            },
        }
        
        # 注册工具
        self._register_tool(self.save_and_execute, save_and_execute_tool)
        self._register_tool(self.execute_code, execute_code_tool)
        self._register_tool(self.execute_file, execute_file_tool)
        self._register_tool(self.edit_code_file, edit_code_tool)
        self._register_tool(self.list_code_files, list_files_tool)

    async def save_and_execute(self, code_content: str, filename: str) -> str:
        """
        保存代码到文件并立即执行
        
        Args:
            code_content: Python代码内容
            filename: 文件名（不需要.py后缀）
            
        Returns:
            保存结果 + 执行结果
        """
        try:
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_call")
                    await self.stream_manager.print_content(f"CodeAgent正在执行工具调用: save_and_execute")
                    await self.stream_manager.print_xml_close("tool_call")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            # 调用executor的save_and_execute方法
            result = await self.executor.save_and_execute(code_content, filename)
            
            # 发送工具调用结果通知
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_result")
                    await self.stream_manager.print_content(f"代码保存并执行完成")
                    await self.stream_manager.print_xml_close("tool_result")
                except Exception as e:
                    logger.warning(f"发送工具调用结果通知失败: {e}")
            
            return result
            
        except Exception as e:
            error_msg = f"保存并执行代码失败: {str(e)}"
            logger.error(error_msg)
            
            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_error")
                    await self.stream_manager.print_content(f"工具调用失败: {error_msg}")
                    await self.stream_manager.print_xml_close("tool_error")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")
            
            return error_msg

    async def execute_code(self, code_content: str) -> str:
        """
        直接执行Python代码内容，不保存到文件
        
        Args:
            code_content: Python代码内容
            
        Returns:
            执行结果
        """
        try:
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_call")
                    await self.stream_manager.print_content(f"CodeAgent正在执行工具调用: execute_code")
                    await self.stream_manager.print_xml_close("tool_call")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            # 调用executor的execute_code方法
            result = await self.executor.execute_code(code_content)
            
            # 发送工具调用结果通知
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_result")
                    await self.stream_manager.print_content(f"代码执行完成")
                    await self.stream_manager.print_xml_close("tool_result")
                except Exception as e:
                    logger.warning(f"发送工具调用结果通知失败: {e}")
            
            return result
            
        except Exception as e:
            error_msg = f"执行代码失败: {str(e)}"
            logger.error(error_msg)
            
            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_error")
                    await self.stream_manager.print_content(f"工具调用失败: {error_msg}")
                    await self.stream_manager.print_xml_close("tool_error")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")
            
            return error_msg

    async def execute_file(self, file_path: str) -> str:
        """
        执行指定的Python代码文件
        
        Args:
            file_path: 文件路径（相对于工作空间或绝对路径）
            
        Returns:
            执行结果
        """
        try:
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_call")
                    await self.stream_manager.print_content(f"CodeAgent正在执行工具调用: execute_file")
                    await self.stream_manager.print_xml_close("tool_call")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            # 调用executor的execute_file方法
            result = await self.executor.execute_file(file_path)
            
            # 发送工具调用结果通知
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_result")
                    await self.stream_manager.print_content(f"文件执行完成")
                    await self.stream_manager.print_xml_close("tool_result")
                except Exception as e:
                    logger.warning(f"发送工具调用结果通知失败: {e}")
            
            return result
            
        except Exception as e:
            error_msg = f"执行文件失败: {str(e)}"
            logger.error(error_msg)
            
            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_error")
                    await self.stream_manager.print_content(f"工具调用失败: {error_msg}")
                    await self.stream_manager.print_xml_close("tool_error")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")
            
            return error_msg

    async def save_code_to_file(self, code_content: str, filename: str) -> str:
        """
        将代码内容保存到文件
        
        Args:
            code_content: Python代码内容
            filename: 文件名（不需要.py后缀）
            
        Returns:
            保存结果信息
        """
        try:
            # 参数验证
            if not code_content or not code_content.strip():
                return "错误：代码内容不能为空"
            
            if not filename or not filename.strip():
                return "错误：文件名不能为空"
            
            # 清理文件名，移除不安全的字符
            safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-")
            if not safe_filename:
                safe_filename = "code"
            
            # 确保文件名有.py后缀
            if not safe_filename.endswith('.py'):
                safe_filename = safe_filename + '.py'
            
            # 构建完整的文件路径
            code_files_dir = os.path.join(self.executor.workspace_dir, "code_files")
            os.makedirs(code_files_dir, exist_ok=True)
            
            file_path = os.path.join(code_files_dir, safe_filename)
            
            # 保存代码到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code_content)
            
            logger.info(f"代码已保存到文件: {file_path}")
            
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    # 发送工具调用开始通知
                    await self.stream_manager.print_xml_open("tool_call")
                    await self.stream_manager.print_content(f"CodeAgent正在执行工具调用: save_code_to_file")
                    await self.stream_manager.print_xml_close("tool_call")
                    
                    # 发送工具调用结果通知
                    await self.stream_manager.print_xml_open("tool_result")
                    await self.stream_manager.print_content(f"代码文件 {safe_filename} 保存成功")
                    await self.stream_manager.print_xml_close("tool_result")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            # 返回相对路径，这样execute_code_file就能正确找到文件
            relative_path = os.path.join("code_files", safe_filename)
            
            return f"代码已成功保存到文件: {safe_filename}\n文件路径: {file_path}\n相对路径: {relative_path}\n代码长度: {len(code_content)} 字符\n\n现在可以使用 execute_code_file 工具执行此文件，传入参数: {relative_path}"
            
        except Exception as e:
            error_msg = f"保存代码文件失败: {str(e)}"
            logger.error(error_msg)
            
            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_error")
                    await self.stream_manager.print_content(f"工具调用失败: {error_msg}")
                    await self.stream_manager.print_xml_close("tool_error")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")
            
            return error_msg

    async def edit_code_file(self, filename: str, new_code_content: str) -> str:
        """
        修改已存在的代码文件
        
        Args:
            filename: 文件名（不需要.py后缀）
            new_code_content: 新的代码内容
            
        Returns:
            修改结果信息
        """
        try:
            # 参数验证
            if not new_code_content or not new_code_content.strip():
                return "错误：新代码内容不能为空"
            
            if not filename or not filename.strip():
                return "错误：文件名不能为空"
            
            # 清理文件名，移除不安全的字符
            safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-")
            if not safe_filename:
                safe_filename = "code"
            
            # 确保文件名有.py后缀
            if not safe_filename.endswith('.py'):
                safe_filename = safe_filename + '.py'
            
            # 构建完整的文件路径
            code_files_dir = os.path.join(self.executor.workspace_dir, "code_files")
            file_path = os.path.join(code_files_dir, safe_filename)
            
            # 检查文件是否存在
            if not os.path.exists(file_path):
                return f"错误：文件 {safe_filename} 不存在，无法修改。请先使用 save_code_to_file 创建文件。"
            
            # 备份原文件
            backup_path = file_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            with open(file_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
            
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(original_content)
            
            # 写入新代码
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_code_content)
            
            logger.info(f"代码文件已修改: {file_path}")
            
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    # 发送工具调用开始通知
                    await self.stream_manager.print_xml_open("tool_call")
                    await self.stream_manager.print_content(f"CodeAgent正在执行工具调用: edit_code_file")
                    await self.stream_manager.print_xml_close("tool_call")
                    
                    # 发送工具调用结果通知
                    await self.stream_manager.print_xml_open("tool_result")
                    await self.stream_manager.print_content(f"代码文件 {safe_filename} 修改成功")
                    await self.stream_manager.print_xml_close("tool_result")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            # 返回相对路径，这样execute_code_file就能正确找到文件
            relative_path = os.path.join("code_files", safe_filename)
            
            return f"代码文件 {safe_filename} 已成功修改\n文件路径: {file_path}\n相对路径: {relative_path}\n新代码长度: {len(new_code_content)} 字符\n原文件已备份到: {os.path.basename(backup_path)}"
            
        except Exception as e:
            error_msg = f"修改代码文件失败: {str(e)}"
            logger.error(error_msg)
            
            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_error")
                    await self.stream_manager.print_content(f"工具调用失败: {error_msg}")
                    await self.stream_manager.print_xml_close("tool_error")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")
            
            return error_msg

    async def list_code_files(self) -> str:
        """
        列出工作空间中的所有代码文件
        
        Returns:
            代码文件列表信息
        """
        try:
            code_files_dir = os.path.join(self.executor.workspace_dir, "code_files")
            
            if not os.path.exists(code_files_dir):
                return "代码文件目录不存在，还没有创建任何代码文件。"
            
            files = os.listdir(code_files_dir)
            python_files = [f for f in files if f.endswith('.py')]
            
            if not python_files:
                return "代码文件目录为空，还没有创建任何Python代码文件。"
            
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    # 发送工具调用开始通知
                    await self.stream_manager.print_xml_open("tool_call")
                    await self.stream_manager.print_content(f"CodeAgent正在执行工具调用: list_code_files")
                    await self.stream_manager.print_xml_close("tool_call")
                    
                    # 发送工具调用结果通知
                    await self.stream_manager.print_xml_open("tool_result")
                    await self.stream_manager.print_content(f"找到 {len(python_files)} 个Python代码文件")
                    await self.stream_manager.print_xml_close("tool_result")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            # 构建文件列表信息
            file_info = []
            for file in python_files:
                file_path = os.path.join(code_files_dir, file)
                try:
                    file_size = os.path.getsize(file_path)
                    file_info.append(f"- {file} ({file_size} bytes)")
                except OSError:
                    file_info.append(f"- {file} (无法获取文件大小)")
            
            return f"代码文件目录: {code_files_dir}\n找到 {len(python_files)} 个Python代码文件:\n" + "\n".join(file_info)
            
        except Exception as e:
            error_msg = f"列出代码文件失败: {str(e)}"
            logger.error(error_msg)
            
            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_error")
                    await self.stream_manager.print_content(f"工具调用失败: {error_msg}")
                    await self.stream_manager.print_xml_close("tool_error")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")
            
            return error_msg

    async def run(self, task_prompt: str) -> str:
        """执行代码生成、保存和执行任务。"""
        logger.info(f"CodeAgent开始执行任务: {repr(task_prompt[:50])}...")

        if self.stream_manager:
            await self.stream_manager.print_xml_open("ret_code_agent")

        self.messages.append({"role": "user", "content": task_prompt})

        max_iterations = 5  # 最大迭代次数，防止无限循环
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"CodeAgent第{iteration}次迭代")

            # 调用LLM生成代码或分析结果
            assistant_message, tool_calls = await self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                # 没有工具调用，说明LLM认为任务完成，生成最终回答
                result = assistant_message.get("content", "代码手任务完成。")
                logger.info(f"CodeAgent在第{iteration}次迭代完成，无更多工具调用")
                if self.stream_manager:
                    await self.stream_manager.print_xml_close("ret_code_agent")
                return result

            # 执行所有工具调用
            for tool_call in tool_calls:
                function_name = tool_call["function"]["name"]
                logger.info(f"CodeAgent执行工具调用: {function_name}")

                if function_name in self.available_functions:
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        logger.debug(f"工具 {function_name} 参数: {args}")
                        
                        result = await self.available_functions[function_name](
                            **args)

                        # 将工具执行结果添加回消息历史
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": result,
                        })

                        logger.info(
                            f"工具 {function_name} 执行成功，结果长度: {len(result)} 字符")

                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析失败: {e}")
                        result = f"代码手LLM处理失败：JSON解析错误 - {str(e)}\n原始参数: {tool_call['function'].get('arguments', '')}"
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": result,
                        })
                    except Exception as e:
                        logger.error(f"工具 {function_name} 执行失败: {e}")
                        result = f"工具 {function_name} 执行失败: {str(e)}"
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": result,
                        })
                else:
                    logger.warning(f"未知工具: {function_name}")
                    result = f"未知工具: {function_name}"
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })

        # 达到最大迭代次数
        logger.warning(f"CodeAgent达到最大迭代次数({max_iterations})，强制结束")
        if self.stream_manager:
            await self.stream_manager.print_content(
                f"达到最大迭代次数({max_iterations})，任务结束")
            await self.stream_manager.print_xml_close("ret_code_agent")

        return "代码手任务完成（达到最大迭代次数）"

    def get_execution_stats(self) -> Dict[str, Any]:
        """获取执行统计信息"""
        return {
            "total_messages": len(self.messages),
            "tool_calls_count": sum(1 for msg in self.messages if msg.get("role") == "tool"),
            "workspace_files": self.executor.list_workspace_files()
        }
