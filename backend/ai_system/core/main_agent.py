"""
主AI代理
主LLM Agent (Orchestrator)，负责分析问题并委派任务
"""

import logging
import json
from typing import List, Dict, Any, Optional
import os # Added for workspace directory path

from .agents import Agent
from .llm_handler import LLMHandler
from .stream_manager import StreamOutputManager
from .context_manager import ContextManager
from ..tools.file_tools import FileTools

logger = logging.getLogger(__name__)


class MainAgent(Agent):
    """
    主 LLM Agent (Orchestrator)，负责分析问题并委派任务。
    支持session上下文维护，保持对话连续性。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager, work_id: Optional[str] = None):
        super().__init__(llm_handler, stream_manager)
        
        # 构建工作空间目录路径
        if work_id:
            workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 
                "pa_data", "workspaces", work_id
            )
            # 设置环境变量，供FileTools使用
            os.environ["WORKSPACE_DIR"] = workspace_dir
        else:
            workspace_dir = None
        
        self.file_tools = FileTools(stream_manager)
        self.work_id = work_id  # 改为work_id，每个work对应一个MainAgent
        
        # 初始化上下文管理器
        self.context_manager = ContextManager(
            max_tokens=20000,
            max_messages=50
        )
        
        self._setup()
        logger.info(f"MainAgent初始化完成，work_id: {work_id}")

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        # 简化系统提示，明确角色定位
        system_content = (
            "你是论文生成助手的中枢大脑，负责协调整个论文生成过程。\n"
            "你的职责：\n"
            "1. 分析用户需求，制定论文生成计划\n"
            "2. 当需要代码执行、数据分析、图表生成时，调用CodeAgent工具\n"
            "3. 维护对话上下文，理解整个工作流程的连续性\n"
            "4. 最终使用tree工具检查生成的文件，用writemd工具生成论文\n\n"
            "重要原则：\n"
            "- 保持对话连贯性，不重复询问已明确的信息\n"
            "- CodeAgent负责具体执行，你负责规划和协调\n"
            "- 所有生成的文件都要在最终论文中引用"
        )

        self.messages = [{
            "role": "system",
            "content": system_content
        }]

        # 注册工具
        code_interpreter_tool = {
            "type": "function",
            "function": {
                "name": "CodeAgent",
                "description": "当需要数学计算、数据分析或执行编程任务时调用。提供清晰、具体的任务描述。不要提供代码。",
                "parameters": {
                    "type": "object",
                    "properties": {"task_prompt": {"type": "string", "description": "需要执行的具体任务描述。不要提供代码。"}},
                    "required": ["task_prompt"],
                },
            },
        }

        writemd_tool = {
            "type": "function",
            "function": {
                "name": "writemd",
                "description": "将内容写入Markdown文件到workspace目录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文件名（不需要.md后缀）"},
                        "content": {"type": "string", "description": "Markdown格式的内容"}
                    },
                    "required": ["filename", "content"],
                },
            },
        }

        tree_tool = {
            "type": "function",
            "function": {
                "name": "tree",
                "description": "显示workspace目录的树形结构",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "要显示的目录路径，默认为workspace目录"}
                    },
                    "required": [],
                },
            },
        }

        # 注册工具到可用函数列表
        self.available_functions.update({
            "writemd": self.file_tools.writemd,
            "tree": self.file_tools.tree
        })
        
        # 将工具定义添加到tools列表
        self.tools = [
            code_interpreter_tool,
            writemd_tool,
            tree_tool
        ]

    def load_conversation_history(self, history_messages: List[Dict[str, Any]]):
        """加载对话历史，维护上下文连续性"""
        if not history_messages:
            return
        
        # 保留system message，添加历史消息
        system_message = self.messages[0]
        self.messages = [system_message]
        
        # 添加历史消息，但过滤掉tool消息（避免上下文过长）
        for msg in history_messages:
            if msg.get('role') in ["user", "assistant"]:
                self.messages.append(msg)
        
        # 检查是否需要压缩上下文
        self._check_and_compress_context()
        
        logger.info(f"已加载 {len(self.messages) - 1} 条历史消息，维护上下文连续性")

    def _check_and_compress_context(self):
        """检查并压缩上下文"""
        context_status = self.context_manager.get_context_status(self.messages)
        
        if context_status["compression_needed"]:
            logger.info(f"上下文过长，开始压缩。当前token使用率: {context_status['token_usage_ratio']:.2%}")
            
            # 选择压缩策略
            if context_status["token_usage_ratio"] > 0.8:
                strategy = "high"  # 高压缩
            elif context_status["token_usage_ratio"] > 0.6:
                strategy = "medium"  # 中等压缩
            else:
                strategy = "low"  # 低压缩
            
            # 执行压缩
            compressed_messages, compression_results = self.context_manager.compress_context(
                self.messages, strategy
            )
            
            # 更新消息列表
            self.messages = compressed_messages
            
            # 记录压缩结果
            self.context_manager.compression_history.extend(compression_results)
            
            logger.info(f"上下文压缩完成，压缩后消息数: {len(self.messages)}")
            
            # 生成摘要（如果是中枢大脑模式）
            if self.work_id:
                # 由于这是同步方法，我们创建一个任务但不等待
                import asyncio
                try:
                    asyncio.create_task(self._generate_context_summary_async())
                except RuntimeError:
                    # 如果没有事件循环，就跳过摘要生成
                    logger.warning("无法生成上下文摘要：没有运行的事件循环")

    async def _generate_context_summary_async(self):
        """异步生成上下文摘要"""
        try:
            summary = await self.context_manager.generate_context_summary(
                self.messages, self.work_id or "default"
            )
            logger.info(f"上下文摘要生成成功: {summary.summary_id}")
        except Exception as e:
            logger.error(f"生成上下文摘要失败: {e}")

    async def run(self, user_problem: str):
        """执行主 Agent 逻辑，循环处理直到任务完成。"""
        logger.info(f"MainAgent开始执行，问题长度: {len(user_problem)} 字符")
        logger.info(f"当前消息历史长度: {len(self.messages)}")

        # 添加用户消息到对话历史
        self.messages.append({"role": "user", "content": user_problem})

        # 检查上下文状态
        self._check_and_compress_context()

        iteration_count = 0
        while True:
            iteration_count += 1
            logger.info(f"MainAgent第 {iteration_count} 次迭代")

            assistant_message, tool_calls = await self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                logger.info("MainAgent没有工具调用，任务完成")
                if self.stream_manager:
                    # 确保触发消息完成回调
                    await self.stream_manager.finalize_message()
                break

            logger.info(f"MainAgent执行 {len(tool_calls)} 个工具调用")

            # 处理所有工具调用
            for i, tool_call in enumerate(tool_calls):
                function_name = tool_call["function"]["name"]
                logger.info(f"处理工具调用 {i+1}/{len(tool_calls)}: {function_name}")

                if function_name == "CodeAgent":
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        task_prompt = args.get("task_prompt", "")

                        if self.stream_manager:
                            await self.stream_manager.print_xml_open(
                                "call_code_agent")
                            await self.stream_manager.print_content(task_prompt)
                            await self.stream_manager.print_xml_close(
                                "call_code_agent")
                            
                            # 发送工具调用开始通知
                            await self.stream_manager.print_xml_open("tool_call")
                            await self.stream_manager.print_content(f"MainAgent正在调用CodeAgent执行任务")
                            await self.stream_manager.print_xml_close("tool_call")

                        # 使用独立的CodeAgent执行方法，确保消息隔离
                        tool_result = await self._execute_code_agent(task_prompt, tool_call["id"])

                        # 发送工具调用完成通知
                        if self.stream_manager:
                            try:
                                await self.stream_manager.print_xml_open("tool_result")
                                await self.stream_manager.print_content(f"CodeAgent任务执行完成，结果长度: {len(tool_result)} 字符")
                                await self.stream_manager.print_xml_close("tool_result")
                            except Exception as e:
                                logger.warning(f"发送CodeAgent完成通知失败: {e}")

                        # 将子 Agent 的结果添加回主 Agent 的消息历史
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": tool_result,
                        })
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析失败: {e}")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": f"工具参数解析失败: {e}",
                        })

                elif function_name in self.available_functions:
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        
                        # 发送工具调用开始通知
                        if self.stream_manager:
                            try:
                                await self.stream_manager.print_xml_open("tool_call")
                                await self.stream_manager.print_content(f"MainAgent正在执行工具调用: {function_name}")
                                await self.stream_manager.print_xml_close("tool_call")
                            except Exception as e:
                                logger.warning(f"发送工具调用通知失败: {e}")
                        
                        tool_result = await self.available_functions[function_name](
                            **args)

                        # 发送工具调用完成通知
                        if self.stream_manager:
                            try:
                                await self.stream_manager.print_xml_open("tool_result")
                                await self.stream_manager.print_content(f"工具 {function_name} 执行完成，结果长度: {len(tool_result)} 字符")
                                await self.stream_manager.print_xml_close("tool_result")
                            except Exception as e:
                                logger.warning(f"发送工具完成通知失败: {e}")

                        # 将工具执行结果添加回消息历史
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": tool_result,
                        })
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析失败: {e}")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": f"工具参数解析失败: {e}",
                        })
                        
                else:
                    # 处理未知工具调用
                    logger.warning(f"未知工具: {function_name}")
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"未知工具: {function_name}",
                    })

        logger.info(f"MainAgent执行完成，总共 {iteration_count} 次迭代，最终消息历史长度: {len(self.messages)}")

    def get_execution_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        # 获取上下文状态
        context_status = self.context_manager.get_context_status(self.messages)
        
        return {
            "total_messages": len(self.messages),
            "tool_calls_count": sum(1 for msg in self.messages if msg.get("role") == "tool"),
            "user_messages": sum(1 for msg in self.messages if msg.get("role") == "user"),
            "assistant_messages": sum(1 for msg in self.messages if msg.get("role") == "assistant"),
            "workspace_files": self.file_tools.list_files(),
            "work_id": self.work_id,
            "is_brain_mode": bool(self.work_id),
            "context_status": context_status
        }

    def reset_conversation(self):
        """重置对话历史"""
        if self.work_id:
            # 工作模式：只保留system message，不清空历史
            logger.info("工作模式：重置对话历史，保留system message")
            self.messages = [self.messages[0]]
        else:
            # 普通模式：完全重置
            logger.info("普通模式：完全重置对话历史")
            self.messages = [self.messages[0]]
        
        logger.info("MainAgent对话历史已重置")

    def export_conversation(self) -> List[Dict[str, Any]]:
        """导出对话历史"""
        return self.messages.copy()

    def get_context_summary(self) -> str:
        """获取上下文摘要，用于理解当前对话状态"""
        if len(self.messages) <= 1:
            return "对话刚开始，暂无上下文"
        
        # 使用ContextManager生成摘要
        try:
            # 同步调用摘要生成（简化版本）
            summary = self.context_manager._format_summary_content(
                self.messages,
                self.context_manager.extract_key_topics(self.messages),
                self.context_manager._extract_important_points(self.messages)
            )
            return summary
        except Exception as e:
            logger.error(f"生成上下文摘要失败: {e}")
            # 回退到原来的简单摘要
            return self._get_simple_context_summary()
    
    def _get_simple_context_summary(self) -> str:
        """获取简单的上下文摘要（回退方法）"""
        # 统计用户和AI的交互次数
        user_count = sum(1 for msg in self.messages if msg.get("role") == "user")
        ai_count = sum(1 for msg in self.messages if msg.get("role") == "assistant")
        
        # 获取最近的几个用户问题
        recent_questions = []
        for msg in reversed(self.messages):
            if msg.get("role") == "user" and len(recent_questions) < 3:
                recent_questions.append(msg.get("content", "")[:100])
        
        summary = f"当前对话已进行 {user_count} 轮交互，用户提问 {user_count} 次，AI回答 {ai_count} 次。"
        if recent_questions:
            summary += f" 最近的用户问题：{' | '.join(reversed(recent_questions))}"
        
        return summary

    def get_context_manager(self) -> ContextManager:
        """获取上下文管理器实例"""
        return self.context_manager

    async def _execute_code_agent(self, task_prompt: str, tool_call_id: str) -> str:
        """执行CodeAgent的独立方法，使用转发StreamManager确保消息隔离"""
        try:
            # 创建独立的StreamManager给CodeAgent使用
            from .stream_manager import CodeAgentStreamManager
            
            # 构建工作空间目录路径
            if self.work_id:
                workspace_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 
                    "pa_data", "workspaces", self.work_id
                )
            else:
                raise ValueError("MainAgent必须有work_id才能创建CodeAgent")
            
            # 创建转发StreamManager，将CodeAgent输出转发到MainAgent但保持消息隔离
            code_agent_stream = CodeAgentStreamManager(
                main_stream_manager=self.stream_manager,  # 转发到MainAgent的StreamManager
                agent_name="CodeAgent"
            )
            
            # 创建CodeAgent实例，传入独立的StreamManager
            from .agents import CodeAgent
            code_agent = CodeAgent(
                self.llm_handler, 
                code_agent_stream,  # 使用转发StreamManager
                workspace_dir
            )
            
            # 执行任务
            result = await code_agent.run(task_prompt)
            
            # 记录执行统计
            logger.info(f"CodeAgent执行完成，工具调用ID: {tool_call_id}, 结果长度: {len(result)}")
            
            return result
            
        except Exception as e:
            error_msg = f"CodeAgent执行失败: {str(e)}"
            logger.error(error_msg)
            return error_msg
