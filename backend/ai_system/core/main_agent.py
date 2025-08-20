"""
主AI代理
主LLM Agent (Orchestrator)，负责分析问题并委派任务
"""

import logging
import json
import asyncio
from typing import List, Dict, Any, Optional
import os  # Added for workspace directory path

from .agents import Agent
from .llm_handler import LLMHandler
from .stream_manager import StreamOutputManager
from .context_manager import ContextManager
from ..tools.file_tools import FileTools
from ..tools.template_agent_tools import TemplateAgentTools

logger = logging.getLogger(__name__)


class MainAgent(Agent):
    """
    主 LLM Agent (Orchestrator)，负责分析问题并委派任务。
    支持session上下文维护，保持对话连续性。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager, work_id: Optional[str] = None, template_id: Optional[int] = None):
        super().__init__(llm_handler, stream_manager)

        # 构建工作空间目录路径
        if work_id:
            workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.dirname(__file__)))),
                "pa_data", "workspaces", work_id
            )
            # 设置环境变量，供FileTools使用
            os.environ["WORKSPACE_DIR"] = workspace_dir
        else:
            workspace_dir = None

        self.file_tools = FileTools(stream_manager)
        self.work_id = work_id  # 改为work_id，每个work对应一个MainAgent
        self.template_id = template_id  # 添加模板ID
        
        # 初始化模板工具，传入正确的工作空间目录
        if workspace_dir:
            self.template_agent_tools = TemplateAgentTools(workspace_dir)
        else:
            self.template_agent_tools = None

        # 初始化上下文管理器
        self.context_manager = ContextManager(
            max_tokens=20000,
            max_messages=50
        )

        self._setup()
        
        # 如果有模板ID，复制模板文件到工作空间
        if self.template_id and work_id:
            self._copy_template_to_workspace()
            
        logger.info(f"MainAgent初始化完成，work_id: {work_id}, template_id: {template_id}")

    def _copy_template_to_workspace(self):
        """复制模板文件到工作空间"""
        try:
            if not self.template_id:
                logger.warning("模板ID为空，无法复制模板文件")
                return
                
            import shutil
            from services.crud import get_paper_template
            from database.database import get_db
            
            # 获取数据库会话
            db = next(get_db())
            
            # 获取模板信息
            template = get_paper_template(db, self.template_id)
            if not template:
                logger.warning(f"模板 {self.template_id} 不存在")
                return
            
            # 构建模板文件路径
            template_file_path = os.path.join(
                os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.dirname(__file__)))),
                "pa_data", "templates", f"{self.template_id}_template.md"
            )
            
            # 检查模板文件是否存在
            if not os.path.exists(template_file_path):
                logger.warning(f"模板文件不存在: {template_file_path}")
                return
            
            # 构建目标路径 - 将模板复制为paper.md，明确这是最终论文文件
            target_path = os.path.join(self.file_tools.get_workspace_dir(), "paper.md")
            
            # 复制模板文件到工作空间
            shutil.copy2(template_file_path, target_path)
            logger.info(f"模板文件已复制到工作空间，重命名为paper.md: {target_path}")
            
        except Exception as e:
            logger.error(f"复制模板文件失败: {e}")

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        # 基础系统提示
        system_content = (
            "你是论文生成助手的中枢大脑，负责协调整个论文生成过程。**你使用的语言需要跟模板语言一致**\n"
            "你的职责：\n"
            "0. 请你生成论文为paper.md文档！！！\n"
            "1. 分析用户需求，制定论文生成计划\n"
            "2. 当需要代码执行、数据分析、图表生成时，调用CodeAgent工具\n"
            "3. 维护对话上下文，理解整个工作流程的连续性\n"
            "4. 最终使用tree工具检查生成的文件\n\n"
            "重要原则：\n"
            "- 保持对话连贯性，不重复询问已明确的信息\n"
            "- 你是中枢大脑，负责规划和协调，不能直接编写、执行代码\n"
            "- CodeAgent负责具体执行，你负责规划和协调\n"
            "- 所有生成的文件都要在最终论文中引用"
            "- 请自己执行迭代，直到任务完成\n"
            "- 生成的论文不要杜撰，确保科学性"
        )

        # 如果有模板，添加模板信息到系统提示
        if self.template_id:
            system_content += f"\n\n**你使用的语言需要跟模板语言一致**模板信息（已生成）：\n"
            system_content += f"- 模板文件为 'paper.md'（这是最终论文文件）\n"
            system_content += f"- 模板是一个大纲，你要填满大纲！\n"
            system_content += f"- 然后使用以下工具之一来操作论文文件：\n"
            system_content += f"  * writemd工具：支持多种模式（覆盖、追加、修改、插入、智能替换、章节更新）\n"
            system_content += f"  * update_template工具：专门用于模板操作，支持章节级别更新\n"
            system_content += f"- 模板操作工具，让AI更方便地操作模板：\n"
            system_content += f"  * extract_headers: 快速提取模板中的所有标题，了解结构\n"
            system_content += f"  * get_structure_summary: 获取模板结构摘要\n"
            system_content += f"  * analyze_template: 深度分析模板结构和内容\n"
            system_content += f"  * get_section_content: 查看指定章节内容\n"
            system_content += f"  * update_section_content: 更新章节内容（支持多种模式）\n"
            system_content += f"  * add_new_section: 添加新章节\n"
            system_content += f"- 生成论文时必须严格遵循模板的格式、结构和风格\n"
            system_content += f"- 如果模板有特定的章节要求，请保持这些章节结构\n"
            system_content += f"- 最终论文应该是一个完整的、格式规范的学术文档\n"
            system_content += f"- 建议使用smart_replace模式来保持模板结构，只替换内容部分\n"
        else:
            system_content += f"\n\n**不使用模板模式**：\n"
            system_content += f"- 你需要从头开始创建完整的论文结构\n"
            system_content += f"- 使用writemd工具创建paper.md文件\n"
            system_content += f"- 根据用户需求设计合适的论文章节结构\n"
            system_content += f"- 确保论文结构完整、逻辑清晰\n"

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
                "description": "将内容写入Markdown文件到workspace目录，支持多种写入模式",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文件名（不需要.md后缀）"},
                        "content": {"type": "string", "description": "Markdown格式的内容"},
                        "mode": {"type": "string", "description": "写入模式：overwrite(覆盖), append(追加), modify(修改), insert(插入), smart_replace(智能替换), section_update(章节更新)", "default": "overwrite"}
                    },
                    "required": ["filename", "content"],
                },
            },
        }

        update_template_tool = {
            "type": "function",
            "function": {
                "name": "update_template",
                "description": "专门用于更新论文文件的工具，支持章节级别的更新",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "template_name": {"type": "string", "description": "论文文件名，默认为paper.md"},
                        "content": {"type": "string", "description": "要更新的内容"},
                        "section": {"type": "string", "description": "要更新的章节名称（可选）"}
                    },
                    "required": ["content"],
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

        # 模板操作工具
        analyze_template_tool = {
            "type": "function",
            "function": {
                "name": "analyze_template",
                "description": "分析当前工作目录中模板文件的模板结构，识别所有标题层级和内容，为AI提供模板概览",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

        get_section_content_tool = {
            "type": "function",
            "function": {
                "name": "get_section_content",
                "description": "获取paper.md文件中指定章节的内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section_title": {"type": "string", "description": "要查看的章节标题"}
                    },
                    "required": ["section_title"],
                },
            },
        }

        update_section_content_tool = {
            "type": "function",
            "function": {
                "name": "update_section_content",
                "description": "更新paper.md文件中指定章节的内容，支持多种更新模式",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section_title": {"type": "string", "description": "要更新的章节标题"},
                        "new_content": {"type": "string", "description": "新内容"},
                        "mode": {"type": "string", "description": "更新模式：replace(替换), append(追加), prepend(插入), merge(合并)", "default": "replace"}
                    },
                    "required": ["section_title", "new_content"],
                },
            },
        }

        add_new_section_tool = {
            "type": "function",
            "function": {
                "name": "add_new_section",
                "description": "在paper.md文件中指定父章节下添加新章节",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "parent_section": {"type": "string", "description": "父章节标题"},
                        "section_title": {"type": "string", "description": "新章节标题"},
                        "content": {"type": "string", "description": "新章节内容", "default": ""}
                    },
                    "required": ["parent_section", "section_title"],
                },
            },
        }

        extract_headers_tool = {
            "type": "function",
            "function": {
                "name": "extract_headers",
                "description": "从paper.md文件中提取所有标题信息，快速了解文档结构",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

        get_structure_summary_tool = {
            "type": "function",
            "function": {
                "name": "get_structure_summary",
                "description": "获取paper.md文件的内容结构摘要，显示所有标题的层级关系",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

        # 注册工具到可用函数列表
        self.available_functions.update({
            "writemd": self.file_tools.writemd,
            "update_template": self.file_tools.update_template,
            "tree": self.file_tools.tree
        })
        
        # 根据是否有模板来决定是否注册模板操作工具
        if self.template_id and self.template_agent_tools:
            # 有模板时，注册所有模板操作工具
            self.available_functions.update({
                "analyze_template": self.template_agent_tools.analyze_template,
                "get_section_content": self.template_agent_tools.get_section_content,
                "update_section_content": self.template_agent_tools.update_section_content,
                "add_new_section": self.template_agent_tools.add_new_section,
                "remove_section": self.template_agent_tools.remove_section,
                "reorder_sections": self.template_agent_tools.reorder_sections,
                "format_template": self.template_agent_tools.format_template,
                "get_template_help": self.template_agent_tools.get_template_help,
                "extract_headers": self.template_agent_tools.extract_headers_from_content,
                "get_structure_summary": self.template_agent_tools.get_content_structure_summary
            })
            
            # 将模板工具定义添加到tools列表
            self.tools = [
                code_interpreter_tool,
                writemd_tool,
                update_template_tool,
                tree_tool,
                analyze_template_tool,
                get_section_content_tool,
                update_section_content_tool,
                add_new_section_tool,
                extract_headers_tool,
                get_structure_summary_tool
            ]
        else:
            # 没有模板时，只注册基础工具
            self.tools = [
                code_interpreter_tool,
                writemd_tool,
                update_template_tool,
                tree_tool
            ]
            
            logger.info("未使用模板，模板相关工具已禁用")


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

    def add_user_message(self, user_problem: str):
        """在正确位置添加用户消息到对话历史"""
        # 检查是否已存在相同的用户消息，避免重复添加
        existing_user_messages = [
            msg for msg in self.messages if msg.get('role') == 'user']
        is_duplicate = any(msg.get('content') ==
                           user_problem for msg in existing_user_messages)

        if is_duplicate:
            logger.warning(f"检测到重复的用户消息，跳过添加。问题: {user_problem[:50]}...")
            return False
        else:
            # 添加用户消息到对话历史
            self.messages.append({"role": "user", "content": user_problem})
            logger.info("用户消息已添加到对话历史")
            return True

    def _check_and_compress_context(self):
        """检查并压缩上下文"""
        context_status = self.context_manager.get_context_status(self.messages)

        if context_status["compression_needed"]:
            logger.info(
                f"上下文过长，开始压缩。当前token使用率: {context_status['token_usage_ratio']:.2%}")

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
            self.context_manager.compression_history.extend(
                compression_results)

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

        # 检查上下文状态
        self._check_and_compress_context()

        iteration_count = 0
        while True:
            iteration_count += 1
            logger.info(f"MainAgent第 {iteration_count} 次迭代")

            # 使用异步LLM处理
            assistant_message, tool_calls = await self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                logger.info("MainAgent没有工具调用，任务完成")
                # 不在这里调用finalize_message，而是在整个run方法结束时调用
                break

            logger.info(f"MainAgent执行 {len(tool_calls)} 个工具调用")

            # 顺序执行工具调用，但保持异步特性
            tool_results = []
            for i, tool_call in enumerate(tool_calls):
                function_name = tool_call["function"]["name"]
                logger.info(f"执行工具调用 {i+1}/{len(tool_calls)}: {function_name}")
                
                try:
                    # 执行单个工具调用
                    tool_result = await self._execute_tool_call(tool_call, i+1, len(tool_calls))
                    tool_results.append(tool_result)
                    
                    # 添加成功结果到历史
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result,
                    })
                    
                    # 使用配置参数优化延迟
                    from ..config.async_config import AsyncConfig
                    config = AsyncConfig.get_tool_call_config()
                    await asyncio.sleep(config["execution_yield_delay"])
                    
                except Exception as e:
                    logger.error(f"工具调用 {i+1} 执行失败: {e}")
                    # 添加错误消息到历史
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"工具执行失败: {str(e)}",
                    })
                    tool_results.append(f"工具执行失败: {str(e)}")
                    
                    # 使用配置参数优化延迟
                    await asyncio.sleep(config["execution_yield_delay"])

        logger.info(
            f"MainAgent执行完成，总共 {iteration_count} 次迭代，最终消息历史长度: {len(self.messages)}")

        # 确保在MainAgent执行完成后触发消息完成回调，保存所有JSON块
        if self.stream_manager:
            await self.stream_manager.finalize_message()

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
        user_count = sum(
            1 for msg in self.messages if msg.get("role") == "user")
        ai_count = sum(1 for msg in self.messages if msg.get(
            "role") == "assistant")

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
                    os.path.dirname(os.path.dirname(
                        os.path.dirname(os.path.dirname(__file__)))),
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
            logger.info(
                f"CodeAgent执行完成，工具调用ID: {tool_call_id}, 结果长度: {len(result)}")

            return result

        except Exception as e:
            error_msg = f"CodeAgent执行失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def _execute_tool_call(self, tool_call: Dict[str, Any], index: int, total: int) -> str:
        """执行单个工具调用的异步方法"""
        function_name = tool_call["function"]["name"]
        logger.info(f"执行工具调用 {index}/{total}: {function_name}")

        try:
            if function_name == "CodeAgent":
                args = json.loads(tool_call["function"]["arguments"])
                task_prompt = args.get("task_prompt", "")

                if self.stream_manager:
                    await self.stream_manager.print_code_agent_call(task_prompt)
                    # 发送工具调用开始通知
                    await self.stream_manager.print_main_content("MainAgent正在调用CodeAgent执行任务")

                # 使用独立的CodeAgent执行方法，确保消息隔离
                tool_result = await self._execute_code_agent(task_prompt, tool_call["id"])

                # 发送工具调用完成通知
                if self.stream_manager:
                    try:
                        await self.stream_manager.print_main_content(f"CodeAgent任务执行完成，结果长度: {len(tool_result)} 字符")
                    except Exception as e:
                        logger.warning(f"发送CodeAgent完成通知失败: {e}")

                return tool_result

            elif function_name in self.available_functions:
                args = json.loads(tool_call["function"]["arguments"])

                # 发送工具调用开始通知
                if self.stream_manager:
                    try:
                        await self.stream_manager.print_main_content(f"MainAgent正在执行工具调用: {function_name}")
                    except Exception as e:
                        logger.warning(f"发送工具调用通知失败: {e}")

                # 检查函数是否是异步的
                func = self.available_functions[function_name]
                if asyncio.iscoroutinefunction(func):
                    tool_result = await func(**args)
                else:
                    # 同步函数在线程池中执行
                    loop = asyncio.get_event_loop()
                    tool_result = await loop.run_in_executor(None, lambda: func(**args))

                # 发送工具调用完成通知
                if self.stream_manager:
                    try:
                        await self.stream_manager.print_main_content(f"工具 {function_name} 执行完成，结果长度: {len(tool_result)} 字符")
                    except Exception as e:
                        logger.warning(f"发送工具完成通知失败: {e}")

                return tool_result

            else:
                # 处理未知工具调用
                logger.warning(f"未知工具: {function_name}")
                return f"未知工具: {function_name}"

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            # 尝试修复不完整的JSON
            try:
                import json
                # 获取原始参数
                raw_args = tool_call["function"]["arguments"]
                logger.info(f"尝试修复JSON参数，原始长度: {len(raw_args)}")
                
                # 尝试修复常见的JSON问题
                fixed_args = self._try_fix_incomplete_json(raw_args)
                if fixed_args != raw_args:
                    logger.info(f"JSON修复成功，重新执行工具调用")
                    # 重新解析修复后的参数
                    args = json.loads(fixed_args)
                    
                    # 重新执行工具调用
                    if function_name == "CodeAgent":
                        task_prompt = args.get("task_prompt", "")
                        if self.stream_manager:
                            await self.stream_manager.print_code_agent_call(task_prompt)
                            await self.stream_manager.print_main_content("MainAgent正在调用CodeAgent执行任务")
                        tool_result = await self._execute_code_agent(task_prompt, tool_call["id"])
                        if self.stream_manager:
                            try:
                                await self.stream_manager.print_main_content(f"CodeAgent任务执行完成，结果长度: {len(tool_result)} 字符")
                            except Exception as e:
                                logger.warning(f"发送CodeAgent完成通知失败: {e}")
                        return tool_result
                    elif function_name in self.available_functions:
                        if self.stream_manager:
                            try:
                                await self.stream_manager.print_main_content(f"MainAgent正在执行工具调用: {function_name}")
                            except Exception as e:
                                logger.warning(f"发送工具调用通知失败: {e}")
                        
                        func = self.available_functions[function_name]
                        if asyncio.iscoroutinefunction(func):
                            tool_result = await func(**args)
                        else:
                            loop = asyncio.get_event_loop()
                            tool_result = await loop.run_in_executor(None, lambda: func(**args))
                        
                        if self.stream_manager:
                            try:
                                await self.stream_manager.print_main_content(f"工具 {function_name} 执行完成，结果长度: {len(tool_result)} 字符")
                            except Exception as e:
                                logger.warning(f"发送工具完成通知失败: {e}")
                        
                        return tool_result
                
            except Exception as fix_error:
                logger.error(f"JSON修复失败: {fix_error}")
            
            return f"工具参数解析失败: {e}"
        except Exception as e:
            logger.error(f"工具 {function_name} 执行失败: {e}")
            return f"工具执行失败: {str(e)}"

    def _try_fix_incomplete_json(self, json_str: str) -> str:
        """尝试修复不完整的JSON字符串"""
        if not json_str.strip():
            return json_str
            
        try:
            # 尝试直接解析
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            logger.debug(f"JSON解析失败，尝试修复: {e}")
            
            # 常见的修复策略
            fixed_str = json_str
            
            # 1. 检查未闭合的引号
            quote_count = json_str.count('"') - json_str.count('\\"')
            if quote_count % 2 != 0:
                # 找到最后一个未闭合的引号位置
                last_quote_pos = json_str.rfind('"')
                if last_quote_pos > 0:
                    # 检查是否在字符串内部
                    before_quote = json_str[:last_quote_pos]
                    if before_quote.count('"') % 2 == 0:
                        # 在字符串末尾添加引号
                        fixed_str = json_str + '"'
                        logger.debug("修复未闭合的引号")
            
            # 2. 检查未闭合的大括号
            brace_count = json_str.count('{') - json_str.count('}')
            if brace_count > 0:
                fixed_str = json_str + '}' * brace_count
                logger.debug(f"修复未闭合的大括号，添加 {brace_count} 个")
            
            # 3. 检查未闭合的方括号
            bracket_count = json_str.count('[') - json_str.count(']')
            if bracket_count > 0:
                fixed_str = json_str + ']' * bracket_count
                logger.debug(f"修复未闭合的方括号，添加 {bracket_count} 个")
            
            # 4. 尝试解析修复后的字符串
            try:
                json.loads(fixed_str)
                logger.info(f"JSON修复成功，原始长度: {len(json_str)}, 修复后长度: {len(fixed_str)}")
                return fixed_str
            except json.JSONDecodeError:
                logger.warning(f"JSON修复失败，原始字符串: {repr(json_str[:100])}")
                return json_str
