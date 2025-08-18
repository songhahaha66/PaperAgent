import logging
import asyncio
import os
import io
import contextlib
import json
from dotenv import load_dotenv
import litellm
from typing import List, Dict, Any, Callable, Union
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端
from datetime import datetime

# 配置日志
logger = logging.getLogger(__name__)

# 配置日志
logger = logging.getLogger(__name__)

# --- 核心组件：配置与初始化 ---


def setup_environment():
    """加载环境变量并配置 litellm API 密钥。"""
    load_dotenv()
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise ValueError("未找到 API_KEY，请检查 .env 文件。")
    litellm.api_key = api_key

    # 从环境变量获取workspace路径，如果没有设置则使用默认路径
    workspace_dir = os.getenv("WORKSPACE")
    if not workspace_dir:
        workspace_dir = os.path.join(os.path.dirname(__file__), "workspace")
        logger.info(f"未设置WORKSPACE环境变量，使用默认路径: {workspace_dir}")
    else:
        logger.info(f"从环境变量获取WORKSPACE路径: {workspace_dir}")

    # 确保workspace目录存在
    os.makedirs(workspace_dir, exist_ok=True)
    logger.info(f"环境初始化完成，workspace目录: {workspace_dir}")

    # 将workspace路径设置为环境变量，供其他组件使用
    os.environ["WORKSPACE_DIR"] = workspace_dir

# --- 流式输出管理器 ---


class StreamOutputManager:
    """管理全程流式输出，包括XML标签格式"""

    def __init__(self, stream_callback=None):
        self.indent_level = 0
        self.stream_callback = stream_callback
        self.output_count = 0
        logger.info("StreamOutputManager初始化完成")

    def _output(self, content: str):
        """统一的输出方法"""
        self.output_count += 1
        logger.debug(
            f"StreamOutputManager._output() 第 {self.output_count} 次调用: {repr(content[:50])}...")

        if self.stream_callback:
            # 直接调用回调函数，让回调函数自己处理线程安全问题
            try:
                self.stream_callback(content)
                logger.debug(f"成功调用回调函数")
            except Exception as e:
                logger.error(f"回调函数调用失败: {e}")
        else:
            logger.debug("无回调函数，直接打印")
            print(content)

    def print_xml_open(self, tag_name: str, content: str = ""):
        """打印XML开始标签"""
        # indent = "        " * self.indent_level
        indent = ""
        if content:
            output = f"{indent}<{tag_name}>\n{indent}{content}"
        else:
            output = f"{indent}<{tag_name}>"

        logger.debug(f"XML开始标签: {tag_name}")
        self._output(output)
        self.indent_level += 1

    def print_xml_close(self, tag_name: str):
        """打印XML结束标签"""
        self.indent_level -= 1
        # indent = "        " * self.indent_level
        indent = ""
        output = f"{indent}</{tag_name}>"

        logger.debug(f"XML结束标签: {tag_name}")
        self._output(output)

    def print_content(self, content: str):
        """打印内容"""
        # indent = "        " * self.indent_level
        indent = ""
        output = f"{indent}{content}"

        logger.debug(f"打印内容: {repr(content[:50])}...")
        self._output(output)

    def print_stream(self, content: str):
        """流式打印内容（不换行）"""
        logger.debug(f"流式打印: {repr(content[:50])}...")
        self._output(content)

# --- 文件操作工具 ---


class FileTools:
    """文件操作工具类"""

    def __init__(self, stream_manager: StreamOutputManager):
        self.stream_manager = stream_manager
        # 从环境变量获取workspace路径
        self.workspace_dir = os.getenv("WORKSPACE_DIR")
        if not self.workspace_dir:
            self.workspace_dir = os.path.join(
                os.path.dirname(__file__), "workspace")
            logger.warning("FileTools未找到WORKSPACE_DIR环境变量，使用默认路径")
        os.makedirs(self.workspace_dir, exist_ok=True)
        logger.info(f"FileTools初始化完成，workspace目录: {self.workspace_dir}")

    def writemd(self, filename: str, content: str) -> str:
        """
        写入Markdown文件到workspace目录

        Args:
            filename: 文件名（不需要.md后缀）
            content: Markdown内容

        Returns:
            操作结果信息
        """
        if not filename.endswith('.md'):
            filename += '.md'

        filepath = os.path.join(self.workspace_dir, filename)
        logger.info(f"写入Markdown文件: {filepath}")

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

            result = f"成功写入Markdown文件: {filepath}"
            self.stream_manager.print_xml_open("writemd_result")
            self.stream_manager.print_content(result)
            self.stream_manager.print_xml_close("writemd_result")

            return result
        except Exception as e:
            error_msg = f"写入Markdown文件失败: {str(e)}"
            logger.error(f"写入文件失败: {e}")
            self.stream_manager.print_xml_open("writemd_result")
            self.stream_manager.print_content(error_msg)
            self.stream_manager.print_xml_close("writemd_result")
            return error_msg

    def tree(self, directory: str = None) -> str:
        """
        显示目录树结构

        Args:
            directory: 要显示的目录路径，默认为workspace目录

        Returns:
            目录树结构字符串
        """
        if directory is None:
            directory = self.workspace_dir

        if not os.path.exists(directory):
            return f"目录不存在: {directory}"

        logger.info(f"生成目录树: {directory}")

        def _tree_helper(path: str, prefix: str = "", is_last: bool = True) -> str:
            result = []
            items = os.listdir(path)
            items.sort()

            for i, item in enumerate(items):
                item_path = os.path.join(path, item)
                is_last_item = i == len(items) - 1

                if os.path.isdir(item_path):
                    result.append(
                        f"{prefix}{'└── ' if is_last_item else '├── '}{item}/")
                    new_prefix = prefix + ('    ' if is_last_item else '│   ')
                    result.append(_tree_helper(
                        item_path, new_prefix, is_last_item))
                else:
                    result.append(
                        f"{prefix}{'└── ' if is_last_item else '├── '}{item}")

            return '\n'.join(result)

        tree_result = f"{os.path.basename(directory)}/\n" + \
            _tree_helper(directory)

        self.stream_manager.print_xml_open("tree_result")
        self.stream_manager.print_content(tree_result)
        self.stream_manager.print_xml_close("tree_result")

        return tree_result

# --- 第三层封装：代码执行器 ---


class CodeExecutor:
    """代码执行器 - 简化版本"""

    def __init__(self, stream_manager: StreamOutputManager):
        self.stream_manager = stream_manager
        # 从环境变量获取workspace路径
        self.workspace_dir = os.getenv("WORKSPACE_DIR")
        if not self.workspace_dir:
            self.workspace_dir = os.path.join(
                os.path.dirname(__file__), "workspace")
            logger.warning("CodeExecutor未找到WORKSPACE_DIR环境变量，使用默认路径")
        
        # 确保工作空间目录存在
        os.makedirs(self.workspace_dir, exist_ok=True)
        # 不需要重复创建目录结构，environment.py中已经处理了
        logger.info(f"CodeExecutor初始化完成，workspace目录: {self.workspace_dir}")

    def execute_code_file(self, code_file_path: str) -> Dict[str, Any]:
        """
        执行指定的Python代码文件并返回结果
        
        Args:
            code_file_path: 要执行的代码文件路径
            
        Returns:
            执行结果字典，包含状态、输出、错误等信息
        """
        logger.info(f"执行代码文件: {code_file_path}")
        
        try:
            # 读取代码文件内容
            with open(code_file_path, 'r', encoding='utf-8') as f:
                python_code = f.read()
            
            # 执行代码
            result = self._execute_python_code(python_code)
            
            # 保存执行日志
            self._save_execution_log(code_file_path, result)
            
            return result
            
        except Exception as e:
            error_result = {
                "success": False,
                "error": f"文件执行失败: {str(e)}",
                "output": "",
                "execution_time": 0
            }
            self._save_execution_log(code_file_path, error_result)
            return error_result

    def _execute_python_code(self, python_code: str) -> Dict[str, Any]:
        """
        执行Python代码字符串并捕获其标准输出和错误
        
        Args:
            python_code: 要执行的 Python 代码
            
        Returns:
            执行结果字典
        """
        logger.info(f"执行Python代码，代码长度: {len(python_code)} 字符")
        
        start_time = datetime.now()
        
        try:
            # 创建一个包含workspace路径的全局环境
            globals_dict = {
                '__builtins__': __builtins__,
                'workspace_dir': self.workspace_dir,
                'os': os,
                'plt': plt,
                'matplotlib': matplotlib,
            }

            # 尝试导入numpy
            try:
                import numpy
                globals_dict['numpy'] = numpy
            except ImportError:
                logger.warning("numpy未安装，跳过导入")

            # 设置matplotlib后端和配置
            plt.switch_backend('Agg')
            plt.rcParams['figure.figsize'] = (10, 6)
            plt.rcParams['font.size'] = 12

            # 使用 io.StringIO 捕获 exec 的所有输出
            string_io = io.StringIO()
            with contextlib.redirect_stdout(string_io), contextlib.redirect_stderr(string_io):
                # 在包含workspace_dir的环境中执行代码
                exec(python_code, globals_dict)

            output = string_io.getvalue()
            execution_time = (datetime.now() - start_time).total_seconds()
            
            # 检查是否有图表需要保存
            plot_files = self._save_plots()
            
            result = {
                "success": True,
                "output": output.strip(),
                "execution_time": execution_time,
                "plot_files": plot_files,
                "timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"代码执行成功，输出长度: {len(output)} 字符，执行时间: {execution_time:.2f}秒")
            return result
            
        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            error_result = {
                "success": False,
                "error": str(e),
                "output": "",
                "execution_time": execution_time,
                "plot_files": {},
                "timestamp": datetime.now().isoformat()
            }
            
            logger.error(f"代码执行失败: {e}")
            return error_result

    def _save_plots(self) -> Dict[str, str]:
        """
        保存matplotlib图表到plots目录
        
        Returns:
            保存的图片文件路径字典
        """
        plot_files = {}
        
        try:
            if plt.get_fignums():  # 如果有图表
                plots_dir = os.path.join(self.workspace_dir, "outputs", "plots")
                os.makedirs(plots_dir, exist_ok=True)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                for fig_num in plt.get_fignums():
                    fig = plt.figure(fig_num)
                    plot_filename = f"plot_{timestamp}_{fig_num}.png"
                    plot_filepath = os.path.join(plots_dir, plot_filename)
                    
                    fig.savefig(plot_filepath, dpi=300, bbox_inches='tight')
                    plot_files[f"plot_{fig_num}"] = f"outputs/plots/{plot_filename}"
                    logger.info(f"图表已保存到: {plot_filepath}")
                
                plt.close('all')  # 关闭所有图表
                
        except Exception as e:
            logger.warning(f"保存图表失败: {e}")
        
        return plot_files

    def _save_execution_log(self, code_file_path: str, result: Dict[str, Any]):
        """
        保存执行日志到execution_logs目录
        
        Args:
            code_file_path: 代码文件路径
            result: 执行结果
        """
        try:
            logs_dir = os.path.join(self.workspace_dir, "execution_logs")
            os.makedirs(logs_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"execution_{timestamp}.log"
            log_filepath = os.path.join(logs_dir, log_filename)
            
            # 构建日志内容
            log_content = {
                "timestamp": result.get("timestamp", datetime.now().isoformat()),
                "code_file": code_file_path,
                "execution_status": "success" if result.get("success") else "failed",
                "execution_time": result.get("execution_time", 0),
                "output": result.get("output", ""),
                "error": result.get("error", ""),
                "plot_files": result.get("plot_files", {}),
                "log_file": f"execution_logs/{log_filename}"
            }
            
            with open(log_filepath, 'w', encoding='utf-8') as f:
                json.dump(log_content, f, ensure_ascii=False, indent=2)
            
            logger.info(f"执行日志已保存到: {log_filepath}")
            
        except Exception as e:
            logger.error(f"保存执行日志失败: {e}")

    # 保留原有的pyexec方法以保持兼容性
    def pyexec(self, python_code: str) -> str:
        """
        执行Python代码字符串并捕获其标准输出和错误。
        保持向后兼容性。

        Args:
            python_code: 要执行的 Python 代码。

        Returns:
            执行结果的字符串（包括输出或错误信息）。
        """
        logger.info(f"执行Python代码，代码长度: {len(python_code)} 字符")
        self.stream_manager.print_xml_open("call_exec")
        self.stream_manager.print_content(python_code)
        self.stream_manager.print_xml_close("call_exec")

        try:
            # 创建一个包含workspace路径的全局环境
            globals_dict = {
                '__builtins__': __builtins__,
                'workspace_dir': self.workspace_dir,
                'os': os,
                'plt': plt,
                'matplotlib': matplotlib,
                'numpy': __import__('numpy')
            }

            # 设置matplotlib后端和配置
            plt.switch_backend('Agg')
            plt.rcParams['figure.figsize'] = (10, 6)
            plt.rcParams['font.size'] = 12

            # 使用 io.StringIO 捕获 exec 的所有输出
            string_io = io.StringIO()
            with contextlib.redirect_stdout(string_io), contextlib.redirect_stderr(string_io):
                # 在包含workspace_dir的环境中执行代码
                exec(python_code, globals_dict)

            result = string_io.getvalue()
            logger.info(f"代码执行成功，输出长度: {len(result)} 字符")

            self.stream_manager.print_xml_open("ret_exec")
            self.stream_manager.print_content(result.strip())
            self.stream_manager.print_xml_close("ret_exec")

            return result
        except Exception as e:
            error_message = f"代码执行出错: {str(e)}"
            logger.error(f"代码执行失败: {e}")
            self.stream_manager.print_xml_open("ret_exec")
            self.stream_manager.print_content(error_message)
            self.stream_manager.print_xml_close("ret_exec")
            return error_message

# --- 通用逻辑封装：LLM 通信处理器 ---


class LLMHandler:
    """
    处理与 litellm API 的所有通信，包括流式响应和工具调用。
    """

    def __init__(self, model: str = "gemini/gemini-2.0-flash", stream_manager: StreamOutputManager = None):
        self.model = model
        self.stream_manager = stream_manager
        logger.info(f"LLMHandler初始化完成，模型: {model}")

    def process_stream(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None):
        """
        调用 LLM API 并处理流式响应，返回完整的响应和工具调用信息。
        """
        logger.info(f"开始调用LLM API，消息数量: {len(messages)}")
        if tools:
            logger.info(f"使用工具数量: {len(tools)}")

        response_stream = litellm.completion(
            model=self.model,
            messages=messages,
            tools=tools,
            stream=True,
        )

        full_response_content = ""
        tool_calls = []
        current_tool_call = {"id": None, "name": None, "arguments": ""}
        chunk_count = 0

        for chunk in response_stream:
            chunk_count += 1
            delta = chunk.choices[0].delta

            # 1. 累积文本内容
            if delta.content:
                content = delta.content
                if self.stream_manager:
                    self.stream_manager.print_stream(content)
                else:
                    print(content, end="", flush=True)
                full_response_content += content
                logger.debug(
                    f"接收到文本内容块 {chunk_count}: {repr(content[:30])}...")

            # 2. 累积工具调用信息
            if delta.tool_calls:
                for tool_call_delta in delta.tool_calls:
                    if tool_call_delta.id:
                        # 如果是新的 tool_call，保存上一个
                        if current_tool_call["id"] is not None:
                            tool_calls.append(
                                self._finalize_tool_call(current_tool_call))
                        current_tool_call = {
                            "id": tool_call_delta.id, "name": None, "arguments": ""}
                        logger.debug(f"开始新的工具调用: {tool_call_delta.id}")

                    if tool_call_delta.function:
                        if tool_call_delta.function.name:
                            current_tool_call["name"] = tool_call_delta.function.name
                            logger.debug(
                                f"工具调用名称: {tool_call_delta.function.name}")
                        if tool_call_delta.function.arguments:
                            current_tool_call["arguments"] += tool_call_delta.function.arguments
                            logger.debug(
                                f"工具调用参数累积: {len(current_tool_call['arguments'])} 字符")

        # 添加最后一个工具调用
        if current_tool_call["id"] is not None:
            tool_calls.append(self._finalize_tool_call(current_tool_call))

        if not self.stream_manager:
            print()  # 确保换行

        logger.info(f"LLM API调用完成，总块数: {chunk_count}，工具调用数: {len(tool_calls)}")

        # 构建完整的 assistant 消息
        assistant_message = {"role": "assistant",
                             "content": full_response_content}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls

        return assistant_message, tool_calls

    def _finalize_tool_call(self, tool_call_data: Dict) -> Dict:
        """构建完整的工具调用对象"""
        return {
            "id": tool_call_data["id"],
            "type": "function",
            "function": {
                "name": tool_call_data["name"],
                "arguments": tool_call_data["arguments"]
            }
        }

# --- Agent 核心逻辑 ---


class Agent:
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
        self.available_functions[func.__name__] = func
        logger.debug(f"注册工具: {func.__name__}")

    def run(self, *args, **kwargs):
        raise NotImplementedError("每个 Agent 子类必须实现 run 方法。")


class CodeAgent(Agent):
    """
    代码手 LLM Agent，负责生成和执行代码。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager):
        super().__init__(llm_handler, stream_manager)
        self.executor = CodeExecutor(stream_manager)
        self._setup()
        logger.info("CodeAgent初始化完成")

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        self.messages = [{
            "role": "system",
            "content": (
                "根据用户的任务描述，先规划分析代码结构，然后使用 pyexec 工具来执行代码，pyexec的输入就是具体代码"
                "**也就是说当你想开始写代码时，就调用 pyexec 工具，pyexec的输入就是具体代码**"
                "必须使用 pyexec 工具来执行代码，并根据执行结果进行总结。"
                "**注意：如果需要保存图像或数据文件，请使用workspace_dir=os.environ[\"WORKSPACE_DIR\"]变量指向的路径。**"
                "例如：plt.savefig(os.path.join(workspace_dir,'figure.png'))"
                "保存图像后，请显示保存路径，确保用户知道文件保存位置。"
            )
        }]
        pyexec_tool = {
            "type": "function",
            "function": {
                "name": "pyexec",
                "description": "执行Python代码并返回结果",
                "parameters": {
                    "type": "object",
                    "properties": {"python_code": {"type": "string", "description": "要执行的Python代码"}},
                    "required": ["python_code"],
                },
            },
        }
        self._register_tool(self.executor.pyexec, pyexec_tool)

    def run(self, task_prompt: str) -> str:
        """执行代码生成和解释任务，支持多次代码执行。"""
        logger.info(f"CodeAgent开始执行任务: {repr(task_prompt[:50])}...")

        if self.stream_manager:
            self.stream_manager.print_xml_open("ret_code_agent")

        self.messages.append({"role": "user", "content": task_prompt})

        max_iterations = 5  # 最大迭代次数，防止无限循环
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"CodeAgent第{iteration}次迭代")

            # 调用LLM生成代码或分析结果
            assistant_message, tool_calls = self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                # 没有工具调用，说明LLM认为任务完成，生成最终回答
                result = assistant_message.get("content", "代码手任务完成。")
                logger.info(f"CodeAgent在第{iteration}次迭代完成，无更多工具调用")
                if self.stream_manager:
                    self.stream_manager.print_xml_close("ret_code_agent")
                return result

            # 执行所有工具调用
            for tool_call in tool_calls:
                function_name = tool_call["function"]["name"]
                logger.info(f"CodeAgent执行工具调用: {function_name}")

                if function_name in self.available_functions:
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        result = self.available_functions[function_name](
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
                        result = f"代码手LLM处理失败：JSON解析错误 - {str(e)}"
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
            self.stream_manager.print_content(
                f"达到最大迭代次数({max_iterations})，任务结束")
            self.stream_manager.print_xml_close("ret_code_agent")

        return "代码手任务完成（达到最大迭代次数）"


class MainAgent(Agent):
    """
    主 LLM Agent (Orchestrator)，负责分析问题并委派任务。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager):
        super().__init__(llm_handler, stream_manager)
        self.file_tools = FileTools(stream_manager)
        self._setup()
        logger.info("MainAgent初始化完成")

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        self.messages = [{
            "role": "system",
            "content": (
                "你是一个建模专家，擅长将用户的问题转化为数学模型。"
                "分析用户问题，如果涉及具体计算、数据分析"
                "必须交给 CodeAgent 工具来完成。"
                "例如你可以调用 CodeAgent 工具，请生成这份数据的可视化图片"
                "或者，请编程计算这个微分方程的解"
                "任务完成后，必须先使用tree工具查看目录结构，确认所有生成的文件都存在，"
                "然后使用writemd工具生成最终的论文文档。在论文中要引用生成的文件。"
            )
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

        self.tools.extend([code_interpreter_tool, writemd_tool, tree_tool])
        self.available_functions.update({
            "writemd": self.file_tools.writemd,
            "tree": self.file_tools.tree
        })

    def run(self, user_problem: str):
        """执行主 Agent 逻辑，循环处理直到任务完成。"""
        logger.info(f"MainAgent开始执行，问题长度: {len(user_problem)} 字符")

        if self.stream_manager:
            self.stream_manager.print_xml_open("main_agent")

        self.messages.append({"role": "user", "content": user_problem})

        iteration_count = 0
        while True:
            iteration_count += 1
            logger.info(f"MainAgent第 {iteration_count} 次迭代")

            assistant_message, tool_calls = self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                logger.info("MainAgent没有工具调用，任务完成")
                if self.stream_manager:
                    self.stream_manager.print_xml_close("main_agent")
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
                            self.stream_manager.print_xml_open(
                                "call_code_agent")
                            self.stream_manager.print_content(task_prompt)
                            self.stream_manager.print_xml_close(
                                "call_code_agent")

                        # 创建并运行子 Agent
                        code_agent = CodeAgent(
                            self.llm_handler, self.stream_manager)
                        tool_result = code_agent.run(task_prompt)

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
                        tool_result = self.available_functions[function_name](
                            **args)

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

        logger.info(f"MainAgent执行完成，总共 {iteration_count} 次迭代")

# --- 程序入口 ---


if __name__ == "__main__":
    try:
        setup_environment()

        # 定义问题
        problem = """
        问题：咖啡的最佳饮用时机

        问题描述：
        一名办公室职员在早上 t=0 时刻冲泡了一杯热咖啡。他知道刚冲好的咖啡太烫，无法饮用，而冷掉的咖啡口感不佳。他希望能在咖啡温度最适宜的区间内享用。
        假设我们已经知道以下所有必需的物理参数，请建立一个数学模型来确定他应该在什么时候开始喝咖啡，以及这个"最佳饮用窗口"有多长。
        
        已知条件与参数：
        初始温度 (t=0)：咖啡的初始温度 T₀ = 95°C
        环境温度：办公室的恒定室温 T_env = 25°C
        最佳饮用温度区间：咖啡的理想入口温度在 60°C（最低）到 70°C（最高）之间。
        冷却系数 (k): 为了简化问题，我们假设一个合理的冷却系数 k = 0.05 (单位: 1/分钟)。
        
        任务：
        1. 使用牛顿冷却定律建立温度随时间变化的数学模型。
        2. 计算咖啡温度达到 70°C 所需的时间 (t_start)。
        3. 计算咖啡温度达到 60°C 所需的时间 (t_end)。
        4. 计算最佳饮用窗口的持续时间 (Δt = t_end - t_start)。
        5. 生成温度随时间变化的可视化图表。
        6. 生成完整的论文文档。
        """

        # 初始化流式输出管理器
        stream_manager = StreamOutputManager()

        # 初始化并运行主 Agent
        llm_handler = LLMHandler(stream_manager=stream_manager)
        main_agent = MainAgent(llm_handler, stream_manager)
        main_agent.run(problem)

    except ValueError as e:
        logger.error(f"初始化错误: {e}")
    except Exception as e:
        logger.error(f"程序运行出现意外错误: {e}")
