"""
主AI代理 - LangChain 重构版本
论文生成的中枢大脑，负责协调和规划整个论文生成过程
"""

import logging
import os
import asyncio
from typing import List, Dict, Any, Optional
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage

from ..core_managers.langchain_tools import LangChainToolFactory
from config.paths import get_workspace_path

logger = logging.getLogger(__name__)


class MainAgent:
    """
    主LLM Agent (Orchestrator)，负责分析问题并委派任务
    基于 LangChain Agent，极简实现
    """

    def __init__(self, llm: BaseLanguageModel, stream_manager=None,
                 workspace_dir: str = None, work_id: Optional[str] = None,
                 template_id: Optional[int] = None, codeagent_llm=None,
                 output_mode: str = "markdown"):
        """
        初始化MainAgent

        Args:
            llm: LangChain LLM 实例
            stream_manager: 流式输出管理器
            workspace_dir: 工作空间目录路径
            work_id: 工作ID
            template_id: 模板ID
            codeagent_llm: CodeAgent使用的LLM实例
            output_mode: 输出模式 ("markdown", "word", "latex")
        """
        logger.info(f"MainAgent初始化开始，output_mode: {output_mode}, codeagent_llm: {codeagent_llm}")
        self.llm = llm
        self.stream_manager = stream_manager
        self.work_id = work_id
        self.template_id = template_id
        self.workspace_dir = workspace_dir
        self.output_mode = output_mode

        # 如果没有提供workspace_dir但有work_id，构建路径
        if not workspace_dir and work_id:
            # 使用统一的路径配置
            self.workspace_dir = str(get_workspace_path(work_id))
            # 设置环境变量，供工具使用
            os.environ["WORKSPACE_DIR"] = self.workspace_dir

        # 加载基础工具（不包括文档写作工具，文档操作由WriterAgent处理）
        # 基础工具包括：tree, list_attachments, web_search 等通用工具
        self.tools = LangChainToolFactory.create_base_tools(
            self.workspace_dir, stream_manager
        )
        logger.info("MainAgent加载基础工具（文档操作由WriterAgent处理）")

        # 添加代码执行工具（使用CodeAgent，默认复用主LLM，可指定codeagent_llm）
        code_llm = codeagent_llm or self.llm
        code_agent_tool = LangChainToolFactory.create_code_agent_tool(
            self.workspace_dir, stream_manager, code_llm
        )
        if code_agent_tool:
            self.tools.append(code_agent_tool)
            logger.info("成功添加CodeAgent工具，使用langchain实现")
        else:
            logger.warning("CodeAgent工具创建失败，代码能力可能受限")

        # 添加WriterAgent工具（用于文档写作任务）
        writer_agent_tool = LangChainToolFactory.create_writer_agent_tool(
            workspace_dir=self.workspace_dir,
            output_mode=self.output_mode,
            stream_manager=stream_manager,
            llm=self.llm
        )
        if writer_agent_tool:
            self.tools.append(writer_agent_tool)
            logger.info("成功添加WriterAgent工具，output_mode: %s", self.output_mode)
        else:
            logger.warning("WriterAgent工具创建失败，文档写作能力可能受限")

        # 创建 LangChain Agent
        self.system_prompt = self._create_system_prompt()
        
        # 检查 LLM 是否支持工具调用
        logger.info(f"LLM 类型: {type(llm).__name__}")
        logger.info(f"LLM 模型: {getattr(llm, 'model_name', getattr(llm, 'model', 'unknown'))}")
        
        # 检查是否有 bind_tools 方法（表示支持工具调用）
        if hasattr(llm, 'bind_tools'):
            logger.info("✓ LLM 支持 bind_tools 方法")
            # 测试绑定工具
            try:
                test_bound = llm.bind_tools(self.tools[:1])
                logger.info("✓ 工具绑定测试成功")
            except Exception as e:
                logger.error(f"✗ 工具绑定测试失败: {e}")
        else:
            logger.warning("⚠️ LLM 不支持 bind_tools 方法，工具调用可能不可用")
        
        # 检查 LLM 的配置
        if hasattr(llm, 'model_kwargs'):
            logger.info(f"LLM model_kwargs: {llm.model_kwargs}")
        
        logger.info(f"创建 Agent，工具数量: {len(self.tools)}")
        self.agent = create_agent(
            model=llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
            debug=True  # 启用调试模式
        )

        logger.info(f"MainAgent初始化完成，work_id: {work_id}, template_id: {template_id}, output_mode: {output_mode}, 工具数量: {len(self.tools)}")
        logger.info(f"已注册工具: {[tool.name for tool in self.tools]}")

    def _create_system_prompt(self) -> str:
        """创建 MainAgent 的系统提示词"""
        # 基础系统提示
        system_content = (
            "你是基于 LangChain Agent 的学术论文写作助手（MainAgent），负责协调整个论文生成过程。**你使用的语言需要跟模板语言一致**\n\n"
            "**🔴 核心行为准则**：\n"
            "1. **主动执行，不要问用户要写什么内容！**\n"
            "2. **根据用户的需求描述，自己思考并生成完整的论文内容**\n"
            "3. **立即使用工具开始写作，不要只是回复文本说明！**\n"
            "4. **如果用户说\"写论文\"、\"生成论文\"，你要立即开始调用工具写入内容，而不是问用户要写什么**\n\n"
            "**重要：你必须使用提供的工具来完成任务，不要只是回复文本！**\n\n"
            "请你记住：论文尽可能使用图表等清晰表示！涉及图表等务必使用代码执行得到！\n"
            "请你记住：如果最后发现没找到代码或者图片就重新执行数据分析！\n\n"
            "**你的身份和职责**：\n"
            "- 你是MainAgent，负责论文写作的整体协调和文档生成\n"
            "- 你有一个助手CodeAgent，专门负责编程任务（数据分析、图表生成等）\n"
            "- 你需要明确区分哪些任务由你完成，哪些任务委派给CodeAgent\n"
            "- **你要主动思考论文内容，不要总是问用户要写什么**\n\n"
            "**核心工作流程**：\n"
            "1. 分析用户需求，**立即制定论文生成计划并开始执行**\n"
            "2. **委派编程任务给CodeAgent**：当需要数据分析、图表生成、统计计算时，使用code_agent_execute工具\n"
            "3. **你自己负责文档生成**：创建Word文档、添加内容、格式化等由你直接使用Word工具完成\n"
            "4. **主动生成内容**：根据用户需求和模板结构，自己思考并生成合适的论文内容\n"
        )

        # 根据输出模式添加文档生成指令
        if self.output_mode == "word":
            system_content += (
                "4. **委派文档写作任务给WriterAgent**：你正在Word模式下工作，必须使用WriterAgent来处理所有文档操作\n\n"
                "**🔴 核心原则：使用WriterAgent处理所有文档操作**\n"
                "- **你（MainAgent）不应该直接调用Word工具**\n"
                "- **所有文档写作任务必须委派给WriterAgent**\n"
                "- 使用 writer_agent_execute 工具来委派文档操作\n\n"
                "**WriterAgent工具使用方法**：\n"
                "- 工具名称：writer_agent_execute\n"
                "- 输入：自然语言写作指令\n"
                "- WriterAgent会自动选择合适的Word工具来完成任务\n\n"
                "**示例指令**：\n"
                "- \"添加一级标题Introduction\"\n"
                "- \"添加段落内容：This paper presents a novel approach...\"\n"
                "- \"添加3行4列的表格，包含以下数据：[[...], [...], [...]]\"\n"
                "- \"插入图片outputs/chart.png，宽度6英寸\"\n"
                "- \"添加分页符\"\n\n"
                "**Word模式工作流程（立即执行，不要问用户）**：\n"
                "1. 分析用户需求，确定需要写入的内容\n"
                "2. **使用writer_agent_execute委派文档操作**\n"
                "3. 提供清晰的写作指令给WriterAgent\n"
                "4. WriterAgent会处理所有Word文档操作（创建、添加标题、段落、表格、图片等）\n"
                "5. 如果需要图表，先使用code_agent_execute生成图片，然后委派给WriterAgent插入\n"
                "6. 文档会自动保存到 paper.docx\n\n"
                "**任务分工原则（重要）**：\n"
                "- **你（MainAgent）负责**：高层规划、任务协调、委派任务给专用代理\n"
                "  * 使用 writer_agent_execute 委派所有文档写作任务\n"
                "  * 使用 code_agent_execute 委派编程任务（数据分析、图表生成等）\n"
                "  * 不要直接调用Word工具\n"
                "- **WriterAgent负责**：所有文档操作（创建文档、添加内容、格式化等）\n"
                "  * 通过 writer_agent_execute 工具调用\n"
                "- **CodeAgent负责**：数据分析、图表生成、复杂计算、Python代码执行等编程任务\n"
                "  * 使用 code_agent_execute 工具委派这些任务\n"
                "  * 例如：\"分析数据并生成柱状图\"、\"计算统计指标\"、\"处理CSV文件\"\n\n"
            )
        else:
            system_content += (
                "4. **委派文档写作任务给WriterAgent**：你正在Markdown模式下工作，必须使用WriterAgent来处理所有文档操作\n\n"
                "**🔴 核心原则：使用WriterAgent处理所有文档操作**\n"
                "- **你（MainAgent）不应该直接调用writemd或update_template工具**\n"
                "- **所有文档写作任务必须委派给WriterAgent**\n"
                "- 使用 writer_agent_execute 工具来委派文档操作\n\n"
                "**WriterAgent工具使用方法**：\n"
                "- 工具名称：writer_agent_execute\n"
                "- 输入：自然语言写作指令\n"
                "- WriterAgent会自动选择合适的Markdown工具来完成任务\n\n"
                "**示例指令**：\n"
                "- \"将以下内容追加到paper.md：# Introduction\\n\\nThis paper...\"\n"
                "- \"更新Abstract章节的内容为：...\"\n"
                "- \"覆盖paper.md文件，写入完整的论文内容：...\"\n\n"
                "**Markdown模式工作流程（立即执行，不要问用户）**：\n"
                "1. 分析用户需求，确定需要写入的内容\n"
                "2. **使用writer_agent_execute委派文档操作**\n"
                "3. 提供清晰的写作指令给WriterAgent\n"
                "4. WriterAgent会处理所有Markdown文档操作\n"
                "5. 文档会自动保存到 paper.md\n\n"
                "**任务分工原则（重要）**：\n"
                "- **你（MainAgent）负责**：高层规划、任务协调、委派任务给专用代理\n"
                "  * 使用 writer_agent_execute 委派所有文档写作任务\n"
                "  * 使用 code_agent_execute 委派编程任务\n"
                "  * 不要直接调用writemd或update_template工具\n"
                "- **WriterAgent负责**：所有文档操作\n"
                "  * 通过 writer_agent_execute 工具调用\n"
                "- **CodeAgent负责**：数据分析、图表生成等编程任务\n"
                "  * 使用 code_agent_execute 工具委派\n"
            )

        # 通用工具
        system_content += (
            "\n**通用工具**：\n"
            "- list_attachments: 列出所有附件文件\n"
            "- web_search: 搜索最新的学术资料和背景信息\n"
            "- tree: 显示工作空间目录结构\n\n"
            "**WriterAgent工具（用于所有文档写作任务）**：\n"
            "- writer_agent_execute: 委派给专用WriterAgent执行文档写作任务\n"
            "  * ✅ 适用场景：创建文档、添加标题、添加段落、添加表格、插入图片、格式化文档等所有文档操作\n"
            "  * 示例任务：\"添加一级标题Introduction\"、\"添加段落：This paper...\"、\"插入图片outputs/chart.png\"\n"
            "  * **所有文档写作任务必须通过WriterAgent完成**\n"
            "  * WriterAgent会根据output_mode自动使用Word或Markdown工具\n\n"
            "**CodeAgent工具（仅用于编程任务）**：\n"
            "- code_agent_execute: 委派给专用CodeAgent执行编程任务\n"
            "  * ✅ 适用场景：数据分析、图表生成（matplotlib/seaborn）、统计计算、文件处理、Python脚本执行\n"
            "  * 示例任务：\"读取data.csv并生成销售趋势图\"、\"计算数据的均值和标准差\"、\"处理Excel文件并提取关键信息\"\n"
            "  * ❌ 禁止场景：**绝对不要使用CodeAgent来创建、编辑、修改文档**\n"
            "  * ❌ 禁止场景：**绝对不要使用CodeAgent来添加文档内容、格式化文档**\n"
            "  * 文档操作必须委派给WriterAgent\n\n"
            "**🚫 严格禁止事项**：\n"
            "- **永远不要让CodeAgent操作文档（Word或Markdown）！**\n"
            "- **永远不要让CodeAgent使用python-docx库或直接写入.md文件！**\n"
            "- **所有文档操作必须通过writer_agent_execute委派给WriterAgent！**\n"
            "- **你（MainAgent）不应该直接调用Word工具或writemd工具**\n"
            "- 如果需要编辑文档，使用writer_agent_execute委派给WriterAgent\n"
            "- CodeAgent只负责生成数据、图表等内容，不负责将内容写入文档\n"
        )

        # 根据模板添加额外信息
        if self.template_id:
            system_content += (
                f"\n\n**使用模板模式**（template_id: {self.template_id}）：\n"
                f"- 模板文件为 'paper.md'（这是最终论文文件）\n"
                f"- 模板是一个大纲，你要填满大纲！\n"
                f"- 生成论文时必须严格遵循模板的格式、结构和风格\n"
                f"- 优先使用update_template工具来更新特定章节\n"
                f"- 最终论文应该是一个完整的、格式规范的学术文档\n"
            )
        else:
            system_content += (
                f"\n\n**不使用模板模式**：\n"
                f"- 你需要从头开始创建完整的论文结构\n"
                f"- 根据用户需求设计合适的论文章节结构\n"
                f"- 使用writemd工具创建paper.md文件\n"
                f"- 确保论文结构完整、逻辑清晰\n"
            )

        system_content += (
            "\n\n**🎯 重要原则**：\n"
            "- **主动执行，不要问用户要写什么！根据需求自己思考并生成内容！**\n"
            "- **立即使用工具开始写作，不要只是说明你会怎么做！**\n"
            "- 保持对话连贯性，按步骤执行任务\n"
            "- 生成的图表要保存在outputs目录，并在论文中正确引用\n"
            "- 论文不要杜撰，确保科学性和准确性\n"
            "- 每完成一个重要章节，使用writemd保存一次\n"
            "- 最终输出应该是完整的paper.md或paper.docx文件\n"
            "\n**🔴 关键要求：任务完成标准**\n"
            "- **你的任务只有在将最终结果输出到文件后才算真正完成！**\n"
            "- Word模式：必须使用Word工具将所有内容写入paper.docx文件\n"
            "- Markdown模式：必须使用writemd工具将所有内容写入paper.md文件\n"
            "- **不要只是在对话中回复内容，必须调用相应的工具将内容保存到文件中**\n"
            "- 在完成文件输出后，向用户确认文件已生成并说明文件路径\n"
            "- 如果没有将内容写入docx或md文件，任务视为未完成\n"
        )

        return system_content

    async def run(self, user_input: str) -> str:
        """
        执行主Agent逻辑，使用 LangChain Agent 处理
        """
        logger.info(f"MainAgent开始执行任务: {user_input[:100]}...")

        try:
            # 发送开始通知
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "main_agent_start",
                        f"MainAgent开始执行: {user_input[:100]}..."
                    )
                except Exception as e:
                    logger.warning(f"发送开始通知失败: {e}")

            # 使用 LangChain Agent 执行
            logger.info(f"调用 Agent，可用工具数量: {len(self.tools)}")
            logger.info(f"工具列表: {[tool.name for tool in self.tools]}")
            
            inputs = {"messages": [HumanMessage(content=user_input)]}
            result = await self.agent.ainvoke(inputs)

            # 提取最后的AI回复
            messages = result.get("messages", [])
            output = ""
            
            # 记录所有消息用于调试
            logger.info(f"Agent返回了 {len(messages)} 条消息")
            tool_calls_count = 0
            for i, message in enumerate(messages):
                msg_type = type(message).__name__
                logger.info(f"消息 {i}: 类型={msg_type}")
                
                # 检查是否有工具调用
                if hasattr(message, 'tool_calls') and message.tool_calls:
                    tool_calls_count += len(message.tool_calls)
                    logger.info(f"  包含 {len(message.tool_calls)} 个工具调用")
                    for tc in message.tool_calls:
                        logger.info(f"    工具: {tc.get('name', 'unknown')}")
                
                if hasattr(message, 'content') and message.content:
                    content_preview = str(message.content)[:100]
                    logger.info(f"  内容预览: {content_preview}")
            
            if tool_calls_count == 0:
                logger.warning("⚠️ 没有检测到任何工具调用！")
            else:
                logger.info(f"✓ 总共执行了 {tool_calls_count} 个工具调用")
            
            for message in reversed(messages):
                if hasattr(message, 'content') and message.content:
                    output = message.content
                    break
                elif isinstance(message, dict) and message.get("role") == "assistant":
                    output = message.get("content", "")
                    break

            logger.info(f"MainAgent任务完成，结果长度: {len(output)}")

            # 发送完成通知
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "main_agent_complete",
                        f"任务完成，结果长度: {len(output)} 字符"
                    )
                except Exception as e:
                    logger.warning(f"发送完成通知失败: {e}")

            return output

        except Exception as e:
            logger.error(f"MainAgent执行失败: {e}", exc_info=True)
            error_msg = f"任务执行失败: {str(e)}"

            # 发送错误通知
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block("main_agent_error", error_msg)
                except Exception as e:
                    logger.warning(f"发送错误通知失败: {e}")

            return error_msg

    async def stream_run(self, user_input: str):
        """
        流式执行，逐个输出Agent步骤
        """
        logger.info(f"MainAgent开始流式执行: {user_input[:100]}...")

        try:
            # 使用异步流式执行
            inputs = {"messages": [HumanMessage(content=user_input)]}
            async for chunk in self.agent.astream(inputs, stream_mode="updates"):
                if self.stream_manager:
                    try:
                        await self.stream_manager.print_stream(str(chunk))
                    except Exception as e:
                        logger.warning(f"流式输出失败: {e}")
                else:
                    print(str(chunk))

        except Exception as e:
            logger.error(f"流式执行失败: {e}")
            error_msg = f"流式执行失败: {str(e)}"
            if self.stream_manager:
                await self.stream_manager.print_content(error_msg)

    def get_execution_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        return {
            "agent_type": "MainAgent",
            "template_id": self.template_id,
            "work_id": self.work_id,
            "workspace_dir": self.workspace_dir,
            "tools_count": len(self.tools),
            "tool_names": [tool.name for tool in self.tools],
            "langchain_based": True
        }
