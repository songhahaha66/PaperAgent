"""
主AI代理 - 重构版本
论文生成的中枢大脑，负责协调和规划整个论文生成过程
"""

import logging
import json
import os
import asyncio
import shutil
from typing import List, Dict, Any, Optional

from .agent_base import BaseAgent

logger = logging.getLogger(__name__)


class MainAgent(BaseAgent):
    """
    主LLM Agent (Orchestrator)，负责分析问题并委派任务
    支持session上下文维护，保持对话连续性
    """

    def __init__(self, llm_handler: 'LLMHandler', stream_manager: 'StreamOutputManager',
                 work_id: Optional[str] = None, template_id: Optional[int] = None):
        """
        初始化MainAgent

        Args:
            llm_handler: LLM处理器
            stream_manager: 流式输出管理器
            work_id: 工作ID
            template_id: 模板ID
        """
        # MainAgent特有属性必须在基类初始化之前设置
        self.template_id = template_id

        # 构建工作空间目录路径
        workspace_dir = None
        if work_id:
            workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                "pa_data", "workspaces", work_id
            )
            # 设置环境变量，供工具使用
            os.environ["WORKSPACE_DIR"] = workspace_dir

        # 初始化基类
        super().__init__(llm_handler, stream_manager, workspace_dir, work_id)

        # 如果有模板ID，复制模板文件到工作空间
        if self.template_id and work_id:
            self._copy_template_to_workspace()

        logger.info(f"MainAgent初始化完成，work_id: {work_id}, template_id: {template_id}")

    def get_system_prompt(self) -> str:
        """获取MainAgent的系统提示词"""
        # 基础系统提示
        system_content = (
            "你是论文生成助手的中枢大脑，负责协调整个论文生成过程。**你使用的语言需要跟模板语言一致**\n"
            "请你记住：论文尽可能使用图表等清晰表示！涉及图表等务必使用代码执行得到！\n"
            "请你记住：如果最后tree发现没找到代码或者图片就重新调用CodeAgent生成！\n"
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
            "- 所有生成的文件都要在最终论文中引用\n"
            "- 请自己执行迭代，直到任务完成\n"
            "- 生成的论文不要杜撰，确保科学性"
        )

        # 根据模板添加额外信息
        if self.template_id:
            system_content += f"\n\n**使用模板模式**：\n"
            system_content += f"- 模板文件为 'paper.md'（这是最终论文文件）\n"
            system_content += f"- 模板是一个大纲，你要填满大纲！\n"
            system_content += f"- 生成论文时必须严格遵循模板的格式、结构和风格\n"
            system_content += f"- 最终论文应该是一个完整的、格式规范的学术文档\n"
        else:
            system_content += f"\n\n**不使用模板模式**：\n"
            system_content += f"- 你需要从头开始创建完整的论文结构\n"
            system_content += f"- 根据用户需求设计合适的论文章节结构\n"
            system_content += f"- 确保论文结构完整、逻辑清晰\n"

        return system_content

    def _setup_tools(self):
        """设置MainAgent的工具定义"""
        # CodeAgent工具
        code_interpreter_tool = {
            "type": "function",
            "function": {
                "name": "CodeAgent",
                "description": "当需要数学计算、数据分析或执行编程任务时调用。提供清晰、具体的任务描述。不要提供代码。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_prompt": {"type": "string", "description": "需要执行的具体任务描述。不要提供代码。"}
                    },
                    "required": ["task_prompt"],
                },
            },
        }

        # writemd工具
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
                        "mode": {
                            "type": "string",
                            "description": "写入模式：overwrite(覆盖), append(追加), modify(修改), insert(插入), smart_replace(智能替换), section_update(章节更新)",
                            "default": "overwrite"
                        }
                    },
                    "required": ["filename", "content"],
                },
            },
        }

        # update_template工具
        update_template_tool = {
            "type": "function",
            "function": {
                "name": "update_template",
                "description": "专门用于更新论文文件的工具，只支持章节级别更新，必须指定章节名称",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "template_name": {"type": "string", "description": "论文文件名，默认为paper.md"},
                        "content": {"type": "string", "description": "要更新的内容"},
                        "section": {"type": "string", "description": "要更新的章节名称（必需）"}
                    },
                    "required": ["content", "section"],
                },
            },
        }

        # tree工具
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

        # 模板操作工具（仅在有模板时添加）
        tools = [code_interpreter_tool, writemd_tool, update_template_tool, tree_tool]

        if self.template_id:
            # analyze_template工具
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

            # get_section_content工具
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

            # update_section_content工具
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
                            "mode": {
                                "type": "string",
                                "description": "更新模式：replace(替换), append(追加), prepend(插入), merge(合并)",
                                "default": "replace"
                            }
                        },
                        "required": ["section_title", "new_content"],
                    },
                },
            }

            # add_section工具
            add_section_tool = {
                "type": "function",
                "function": {
                    "name": "add_section",
                    "description": "在paper.md文件末尾添加新章节",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "section_title": {"type": "string", "description": "新章节标题"},
                            "content": {"type": "string", "description": "新章节内容", "default": ""}
                        },
                        "required": ["section_title"],
                    },
                },
            }

            tools.extend([
                analyze_template_tool,
                get_section_content_tool,
                update_section_content_tool,
                add_section_tool
            ])

        self.tools = tools

    def _register_tool_functions(self):
        """注册MainAgent的工具函数"""
        if not self.tool_manager:
            raise ValueError("MainAgent需要工具管理器")

        # 获取工具实例
        file_tool = self.tool_manager.file()

        # 基础工具函数
        self.available_functions = {
            "writemd": file_tool.writemd,
            "update_template": file_tool.update_template,
            "tree": file_tool.tree,
            "CodeAgent": self._execute_code_agent_wrapper
        }

        # 模板工具函数（仅在有模板时注册）
        if self.template_id:
            template_tool = self.tool_manager.template()
            self.available_functions.update({
                "analyze_template": template_tool.analyze_template,
                "get_section_content": template_tool.get_section_content,
                "update_section_content": template_tool.update_section_content,
                "add_section": template_tool.add_section
            })

    async def _execute_code_agent_wrapper(self, task_prompt: str) -> str:
        """
        CodeAgent工具的包装函数

        Args:
            task_prompt: 任务提示词

        Returns:
            CodeAgent执行结果
        """
        return await self._execute_code_agent(task_prompt, "main_agent_call")

    def _copy_template_to_workspace(self):
        """复制模板文件到工作空间"""
        try:
            if not self.template_id:
                logger.warning("模板ID为空，无法复制模板文件")
                return

            # 获取模板信息
            from services.data_services.crud import get_paper_template
            from database.database import get_db

            db = next(get_db())
            template = get_paper_template(db, self.template_id)
            if not template:
                logger.warning(f"模板 {self.template_id} 不存在")
                return

            # 构建模板文件路径
            template_file_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                "pa_data", "templates", f"{self.template_id}_template.md"
            )

            # 检查模板文件是否存在
            if not os.path.exists(template_file_path):
                logger.warning(f"模板文件不存在: {template_file_path}")
                return

            # 构建目标路径
            target_path = os.path.join(self.workspace_dir, "paper.md")

            # 复制模板文件到工作空间
            shutil.copy2(template_file_path, target_path)
            logger.info(f"模板文件已复制到工作空间，重命名为paper.md: {target_path}")

        except Exception as e:
            logger.error(f"复制模板文件失败: {e}")

    async def run(self, user_problem: str) -> str:
        """
        执行主Agent逻辑，循环处理直到任务完成

        Args:
            user_problem: 用户问题

        Returns:
            执行结果
        """
        logger.info(f"MainAgent开始执行，问题长度: {len(user_problem)} 字符")

        # 检查上下文状态
        self._check_and_compress_context()

        # 添加用户消息
        if not self.add_user_message(user_problem):
            # 如果是重复消息，直接返回
            return "检测到重复消息，已跳过处理"

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
                break

            logger.info(f"MainAgent执行 {len(tool_calls)} 个工具调用")

            # 并行执行工具调用
            tool_results = await self._execute_tool_calls_parallel(tool_calls)

            # 添加工具结果到消息历史
            for tool_call, tool_result in zip(tool_calls, tool_results):
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": tool_result,
                })

        logger.info(f"MainAgent执行完成，总共 {iteration_count} 次迭代")

        # 确保在MainAgent执行完成后触发消息完成回调
        if self.stream_manager:
            await self.stream_manager.finalize_message()

        return assistant_message.get('content', 'MainAgent任务完成')

    async def _execute_tool_calls_parallel(self, tool_calls: List[Dict[str, Any]]) -> List[str]:
        """
        并行执行多个工具调用

        Args:
            tool_calls: 工具调用列表

        Returns:
            工具执行结果列表
        """
        tasks = []
        for i, tool_call in enumerate(tool_calls):
            task = self._execute_tool_call(tool_call, i + 1, len(tool_calls))
            tasks.append(task)

        # 并行执行所有工具调用
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常结果
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"工具调用 {i+1} 执行失败: {result}")
                processed_results.append(f"工具执行失败: {str(result)}")
            else:
                processed_results.append(result)

        return processed_results

    def get_execution_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        base_summary = super().get_execution_summary()

        # 添加MainAgent特有的摘要信息
        main_agent_summary = {
            "template_id": self.template_id,
            "workspace_files": self._list_workspace_files() if self.tool_manager else []
        }

        base_summary.update(main_agent_summary)
        return base_summary

    def _list_workspace_files(self) -> List[str]:
        """列出工作空间中的文件"""
        if not self.workspace_dir or not os.path.exists(self.workspace_dir):
            return []

        files = []
        for root, dirs, filenames in os.walk(self.workspace_dir):
            for filename in filenames:
                rel_path = os.path.relpath(os.path.join(root, filename), self.workspace_dir)
                files.append(rel_path)

        return sorted(files)