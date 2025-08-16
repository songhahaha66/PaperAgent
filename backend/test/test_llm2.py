import os
import io
import contextlib
import json
from dotenv import load_dotenv
import litellm
from typing import List, Dict, Any, Callable

# --- 核心组件：配置与初始化 ---


def setup_environment():
    """加载环境变量并配置 litellm API 密钥。"""
    load_dotenv()
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise ValueError("未找到 API_KEY，请检查 .env 文件。")
    litellm.api_key = api_key

# --- 流式输出管理器 ---


class StreamOutputManager:
    """管理全程流式输出，包括XML标签格式"""

    def __init__(self):
        self.indent_level = 0

    def print_xml_open(self, tag_name: str, content: str = ""):
        """打印XML开始标签"""
        indent = "        " * self.indent_level
        if content:
            print(f"{indent}<{tag_name}>")
            print(f"{indent}        {content}")
        else:
            print(f"{indent}<{tag_name}>")
        self.indent_level += 1

    def print_xml_close(self, tag_name: str):
        """打印XML结束标签"""
        self.indent_level -= 1
        indent = "        " * self.indent_level
        print(f"{indent}</{tag_name}>")

    def print_content(self, content: str):
        """打印内容"""
        indent = "        " * self.indent_level
        print(f"{indent}{content}")

    def print_stream(self, content: str):
        """流式打印内容（不换行）"""
        print(content, end="", flush=True)

# --- 第三层封装：代码执行器 ---


class CodeExecutor:
    """
    一个专门用于安全执行 Python 代码的类。
    """

    def __init__(self, stream_manager: StreamOutputManager):
        self.stream_manager = stream_manager

    def pyexec(self, python_code: str) -> str:
        """
        执行Python代码字符串并捕获其标准输出和错误。

        Args:
            python_code: 要执行的 Python 代码。

        Returns:
            执行结果的字符串（包括输出或错误信息）。
        """
        self.stream_manager.print_xml_open("call_exec")
        self.stream_manager.print_content(python_code)
        self.stream_manager.print_xml_close("call_exec")

        try:
            # 使用 io.StringIO 捕获 exec 的所有输出
            string_io = io.StringIO()
            with contextlib.redirect_stdout(string_io), contextlib.redirect_stderr(string_io):
                # 在一个空字典中执行代码，以隔离作用域
                exec(python_code, {})

            result = string_io.getvalue()

            self.stream_manager.print_xml_open("ret_exec")
            self.stream_manager.print_content(result.strip())
            self.stream_manager.print_xml_close("ret_exec")

            return result
        except Exception as e:
            error_message = f"代码执行出错: {str(e)}"
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

    def process_stream(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None):
        """
        调用 LLM API 并处理流式响应，返回完整的响应和工具调用信息。
        """
        response_stream = litellm.completion(
            model=self.model,
            messages=messages,
            tools=tools,
            stream=True,
        )

        full_response_content = ""
        tool_calls = []
        current_tool_call = {"id": None, "name": None, "arguments": ""}

        for chunk in response_stream:
            delta = chunk.choices[0].delta

            # 1. 累积文本内容
            if delta.content:
                content = delta.content
                if self.stream_manager:
                    self.stream_manager.print_stream(content)
                else:
                    print(content, end="", flush=True)
                full_response_content += content

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

                    if tool_call_delta.function:
                        if tool_call_delta.function.name:
                            current_tool_call["name"] = tool_call_delta.function.name
                        if tool_call_delta.function.arguments:
                            current_tool_call["arguments"] += tool_call_delta.function.arguments

        # 添加最后一个工具调用
        if current_tool_call["id"] is not None:
            tool_calls.append(self._finalize_tool_call(current_tool_call))

        if not self.stream_manager:
            print()  # 确保换行

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

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        self.messages = [{
            "role": "system",
            "content": (
                "根据用户的任务描述，先生成完整、可直接执行的Python代码。"
                "生成之后必须使用 pyexec 工具来执行生成的代码，并根据执行结果进行总结。"
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
        """执行代码生成和解释任务。"""
        if self.stream_manager:
            self.stream_manager.print_xml_open("ret_code_agent")

        self.messages.append({"role": "user", "content": task_prompt})

        # 第一次调用：生成代码并产生工具调用
        assistant_message, tool_calls = self.llm_handler.process_stream(
            self.messages, self.tools)
        self.messages.append(assistant_message)

        if not tool_calls:
            result = assistant_message.get("content", "代码手未生成任何代码。")
            if self.stream_manager:
                self.stream_manager.print_xml_close("ret_code_agent")
            return result

        # 执行工具调用
        tool_call = tool_calls[0]  # 代码手一次只处理一个代码块
        function_name = tool_call["function"]["name"]

        if function_name in self.available_functions:
            try:
                args = json.loads(tool_call["function"]["arguments"])
                result = self.available_functions[function_name](**args)

                # 将工具执行结果添加回消息历史
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                })
            except json.JSONDecodeError as e:
                print(f"JSON 解析失败: {e}")
                result = "代码手LLM处理失败：JSON解析错误"

        # 第二次调用：基于代码执行结果生成最终回答
        if self.stream_manager:
            self.stream_manager.print_content("正在生成最终回答...")

        final_message, _ = self.llm_handler.process_stream(
            self.messages, self.tools)

        # 返回最终的文本内容或代码执行结果作为上下文
        final_result = final_message.get("content") or result

        if self.stream_manager:
            self.stream_manager.print_xml_close("ret_code_agent")

        return final_result


class MainAgent(Agent):
    """
    主 LLM Agent (Orchestrator)，负责分析问题并委派任务。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager):
        super().__init__(llm_handler, stream_manager)
        self._setup()

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        self.messages = [{
            "role": "system",
            "content": (
                "你是一个建模专家，擅长将用户的问题转化为数学模型。"
                "分析用户问题，如果涉及具体计算、数据分析"
                "必须交给 CodeAgent 工具来完成。"
                "例如你可以调用 CodeAgent 工具，请生成这份数据的可视化图片到./data/visualization.png"
                "或者，请编程计算这个微分方程的解"
            )
        }]
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
        # 注意：这里的 code_interpreter 并不是一个直接的函数，而是一个触发器
        # 它的"实现"是在 run 循环中创建一个 CodeAgent 实例来处理
        self.tools.append(code_interpreter_tool)

    def run(self, user_problem: str):
        """执行主 Agent 逻辑，循环处理直到任务完成。"""
        if self.stream_manager:
            self.stream_manager.print_xml_open("main_agent")
            self.stream_manager.print_content("分析建模")

        self.messages.append({"role": "user", "content": user_problem})

        while True:
            assistant_message, tool_calls = self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                if self.stream_manager:
                    self.stream_manager.print_content("好的，这个问题总结如下")
                    self.stream_manager.print_xml_close("main_agent")
                break

            # 目前只处理第一个工具调用
            tool_call = tool_calls[0]
            function_name = tool_call["function"]["name"]

            if function_name == "CodeAgent":
                try:
                    args = json.loads(tool_call["function"]["arguments"])
                    task_prompt = args.get("task_prompt", "")

                    if self.stream_manager:
                        self.stream_manager.print_xml_open("call_code_agent")
                        self.stream_manager.print_content(task_prompt)
                        self.stream_manager.print_xml_close("call_code_agent")

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
                    print(f"JSON 解析失败: {e}")
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"工具参数解析失败: {e}",
                    })
                    continue  # 继续循环让主 Agent 处理这个错误
            else:
                # 如果未来有其他工具，在这里处理
                pass

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
        """

        # 初始化流式输出管理器
        stream_manager = StreamOutputManager()

        # 初始化并运行主 Agent
        llm_handler = LLMHandler(stream_manager=stream_manager)
        main_agent = MainAgent(llm_handler, stream_manager)
        main_agent.run(problem)

    except ValueError as e:
        print(f"初始化错误: {e}")
    except Exception as e:
        print(f"程序运行出现意外错误: {e}")
