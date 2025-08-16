import os
import io
import contextlib
import json
from dotenv import load_dotenv
import litellm

# 加载 .env 文件中的环境变量
load_dotenv()

# 从环境变量中获取API密钥
litellm.api_key = os.getenv("GEMINI_API_KEY")

# --- 第三层：Python代码执行器 (pyexec) ---


def pyexec(python_code: str) -> str:
    """
    执行Python代码并返回结果
    """
    print(f"\n>> [pyexec 执行代码...] <<\n", flush=True)

    try:
        # 安全执行生成的代码并捕获输出
        string_io = io.StringIO()
        with contextlib.redirect_stdout(string_io), contextlib.redirect_stderr(string_io):
            exec(python_code, {})

        result = string_io.getvalue()
        print(f"\n>> [pyexec 执行完毕。输出: {result.strip()}] <<\n", flush=True)
        return result

    except Exception as e:
        error_message = f"代码执行出错: {str(e)}"
        print(f"\n>> [pyexec 执行失败: {error_message}] <<\n", flush=True)
        return error_message

# --- 第二层：代码手 LLM ---


def code_interpreter(task_prompt: str) -> str:
    """
    代码手LLM，接收任务描述，生成Python代码，然后调用pyexec执行
    """
    print(f"\n>> [代码手LLM 启动... 任务: {task_prompt}] <<\n", flush=True)

    # 定义代码手可以使用的工具
    tools = [
        {
            "type": "function",
            "function": {
                "name": "pyexec",
                "description": "执行Python代码并返回结果",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "python_code": {
                            "type": "string",
                            "description": "要执行的Python代码",
                        }
                    },
                    "required": ["python_code"],
                },
            },
        }
    ]

    # 代码手的消息历史
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个精通Python的代码生成助手。"
                "根据用户的任务描述，生成完整、可直接执行的Python代码。"
                "代码必须通过print()函数输出结果。"
                "不要包含任何解释或注释，只生成纯粹的Python代码。"
                "使用pyexec工具来执行生成的代码。"
            )
        },
        {"role": "user", "content": task_prompt}
    ]

    # 流式调用代码手LLM
    response_stream = litellm.completion(
        model="gemini/gemini-2.0-flash",
        messages=messages,
        tools=tools,
        stream=True,
    )

    # 处理流式响应
    full_response = ""
    current_tool_call = {"id": None, "name": None, "arguments": ""}

    for chunk in response_stream:
        # 提取并打印文本内容
        if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content:
            content = chunk.choices[0].delta.content
            print(content, end="", flush=True)
            full_response += content

        # 提取工具调用信息
        if hasattr(chunk.choices[0].delta, 'tool_calls') and chunk.choices[0].delta.tool_calls:
            for tool_call_delta in chunk.choices[0].delta.tool_calls:
                if hasattr(tool_call_delta, 'id') and tool_call_delta.id:
                    current_tool_call["id"] = tool_call_delta.id
                if hasattr(tool_call_delta, 'function') and tool_call_delta.function:
                    if hasattr(tool_call_delta.function, 'name') and tool_call_delta.function.name:
                        current_tool_call["name"] = tool_call_delta.function.name
                    if hasattr(tool_call_delta.function, 'arguments') and tool_call_delta.function.arguments:
                        current_tool_call["arguments"] += tool_call_delta.function.arguments

    print()  # 换行

    # 构建assistant消息
    assistant_message = {"role": "assistant", "content": full_response}

    # 如果有工具调用，执行pyexec
    if current_tool_call["name"] and current_tool_call["arguments"]:
        try:
            function_args = json.loads(current_tool_call["arguments"])

            # 构建完整的工具调用对象
            tool_call = {
                "id": current_tool_call["id"],
                "type": "function",
                "function": {
                    "name": current_tool_call["name"],
                    "arguments": current_tool_call["arguments"]
                }
            }
            assistant_message["tool_calls"] = [tool_call]

            if current_tool_call["name"] == "pyexec":
                python_code = function_args.get("python_code", "")
                result = pyexec(python_code=python_code)

                # 将结果返回给代码手LLM进行最终处理
                messages.append(assistant_message)
                messages.append({
                    "role": "tool",
                    "tool_call_id": current_tool_call["id"],
                    "content": result,
                })

                # 代码手LLM的最终回答
                final_response = litellm.completion(
                    model="gemini/gemini-2.0-flash",
                    messages=messages,
                    stream=True,
                )

                print("\n>> [代码手LLM 最终回答:] <<\n", flush=True)
                for chunk in final_response:
                    if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        print(content, end="", flush=True)
                print()

                return result

        except json.JSONDecodeError:
            return "代码手LLM处理失败：JSON解析错误"
    else:
        return full_response

# --- 第一层：主LLM (Orchestrator) ---


def main_agent(user_problem: str):
    """
    主LLM，负责分析问题并决定是否调用代码手
    """
    print("--- 主LLM Agent 启动 ---", flush=True)

    # 定义主LLM可以使用的工具
    tools = [
        {
            "type": "function",
            "function": {
                "name": "code_interpreter",
                "description": "当需要进行数学计算、数据分析、编程任务时，调用此代码解释器。提供一个清晰、具体的任务描述。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_prompt": {
                            "type": "string",
                            "description": "需要执行的具体任务描述，例如：'计算函数f(x)=x^2+2x+1在x=3处的导数' 或 '求解方程x^2-5x+6=0'",
                        }
                    },
                    "required": ["task_prompt"],
                },
            },
        }
    ]

    # 初始化对话历史
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个顶级的数学和编程问题解决专家。"
                "你需要分析用户的问题，如果涉及计算、编程或数学运算，"
                "你必须使用code_interpreter工具来完成。"
                "在调用工具后，基于结果给出完整的分析和答案。"
                "请以流式方式输出你的思考过程。"
            )
        },
        {"role": "user", "content": user_problem}
    ]

    # 循环处理，直到获得最终答案
    while True:
        response_stream = litellm.completion(
            model="gemini/gemini-2.0-flash",
            messages=messages,
            tools=tools,
            stream=True,
        )

        # 处理流式响应
        full_response = ""
        current_tool_call = {"id": None, "name": None, "arguments": ""}

        for chunk in response_stream:
            # 提取并打印文本内容
            if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                print(content, end="", flush=True)
                full_response += content

            # 提取工具调用信息
            if hasattr(chunk.choices[0].delta, 'tool_calls') and chunk.choices[0].delta.tool_calls:
                for tool_call_delta in chunk.choices[0].delta.tool_calls:
                    if hasattr(tool_call_delta, 'id') and tool_call_delta.id:
                        current_tool_call["id"] = tool_call_delta.id
                    if hasattr(tool_call_delta, 'function') and tool_call_delta.function:
                        if hasattr(tool_call_delta.function, 'name') and tool_call_delta.function.name:
                            current_tool_call["name"] = tool_call_delta.function.name
                        if hasattr(tool_call_delta.function, 'arguments') and tool_call_delta.function.arguments:
                            current_tool_call["arguments"] += tool_call_delta.function.arguments

        # 构建assistant消息
        assistant_message = {"role": "assistant", "content": full_response}

        # 如果有工具调用，执行代码手
        if current_tool_call["name"] and current_tool_call["arguments"]:
            try:
                function_args = json.loads(current_tool_call["arguments"])

                # 构建完整的工具调用对象
                tool_call = {
                    "id": current_tool_call["id"],
                    "type": "function",
                    "function": {
                        "name": current_tool_call["name"],
                        "arguments": current_tool_call["arguments"]
                    }
                }
                assistant_message["tool_calls"] = [tool_call]

                if current_tool_call["name"] == "code_interpreter":
                    task = function_args.get("task_prompt", "")
                    tool_result = code_interpreter(task_prompt=task)

                    # 将工具调用的结果添加回消息历史
                    messages.append(assistant_message)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": current_tool_call["id"],
                        "content": tool_result,
                    })

            except json.JSONDecodeError:
                continue
        else:
            # 如果没有工具调用，说明对话结束
            messages.append(assistant_message)
            print("\n\n--- 主LLM Agent 任务完成 ---", flush=True)
            break


if __name__ == "__main__":
    # 测试问题
    problem = "我在做一个物理实验，测量某一物体的运动轨迹。已知物体的位置随时间变化的函数为f(x)=x^3+2x^2-5x+3，其中x为时间（秒），f(x)为位置（米）。请帮我计算当x=2秒时物体的瞬时速度。"
    # problem = "求解方程x^2-7x+12=0"
    # problem = "计算积分∫(x^2+3x)dx"

    main_agent(problem)
