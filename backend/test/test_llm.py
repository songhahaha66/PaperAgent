import litellm
import os
import io
import contextlib
import json
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# 从环境变量中获取API密钥
# 确保你的 .env 文件中有 GEMINI_API_KEY="your_api_key"
litellm.api_key = os.getenv("GEMINI_API_KEY")

# --- 代码手 Agent (作为工具被调用) ---


def code_interpreter(task_prompt: str) -> str:
    """
    当主模型需要执行代码时调用此函数。
    它接收一个自然语言描述的任务，内部调用一个强大的代码LLM生成并执行Python代码，
    然后返回代码的输出结果或错误信息。

    Args:
        task_prompt (str): 一个清晰、具体的指令，描述需要执行的计算或分析任务。
                           例如: "求解方程 x^2 + 5*x - 10 = 0"，"计算从1到100所有偶数的和"。

    Returns:
        str: Python脚本执行后的标准输出结果或捕获到的异常信息。
    """
    # 动态打印，告知用户代码手已被激活
    print(f"\n>> [调用代码手 Agent... 任务: {task_prompt}] <<\n", flush=True)

    try:
        # 内部调用一个更强大的模型 (Gemini 1.5 Pro) 来生成代码
        # 这个模型专门负责高质量的代码生成
        coder_messages = [
            {
                "role": "system",
                "content": (
                    "你是一个精通 Python 的代码生成助手。"
                    "根据用户的任务，编写一个完整、可直接执行的 Python 脚本。"
                    "脚本必须通过 `print()` 函数将其最终结果输出到标准输出。"
                    "不要包含任何解释、注释或示例用法，只输出纯粹的 Python 代码块。"
                    "例如，如果任务是'计算2+2'，你的输出应该是 'print(2+2)'。"
                ),
            },
            {"role": "user", "content": task_prompt},
        ]

        # 使用 litellm 调用代码生成模型
        response = litellm.completion(
            model="gemini/gemini-2.0-flash",
            messages=coder_messages
        )

        # 从响应中提取代码
        generated_code = response.choices[0].message.content.strip()
        # 清理可能的 markdown 代码块标记
        if generated_code.startswith("```python"):
            generated_code = generated_code[9:]
        if generated_code.startswith("```"):
            generated_code = generated_code[3:]
        if generated_code.endswith("```"):
            generated_code = generated_code[:-3]
        generated_code = generated_code.strip()

        # 安全执行生成的代码并捕获输出
        # 警告: 在生产环境中执行LLM生成的代码存在安全风险。
        # 这里使用沙箱环境或更安全的执行策略会更佳。
        string_io = io.StringIO()
        with contextlib.redirect_stdout(string_io), contextlib.redirect_stderr(string_io):
            exec(generated_code, {})

        result = string_io.getvalue()

        print(f"\n>> [代码手 Agent 执行完毕。输出: {result.strip()}] <<\n", flush=True)
        return result

    except Exception as e:
        error_message = f"代码执行出错: {str(e)}"
        print(f"\n>> [代码手 Agent 执行失败: {error_message}] <<\n", flush=True)
        return error_message


# --- 主模型 (Orchestrator) ---


def mathematical_modeling_agent(user_problem: str):
    """
    主模型 Agent，负责处理用户的数学建模问题。
    """
    print("--- 主模型 Agent 启动 ---", flush=True)

    # 定义主模型可以使用的工具
    tools = [
        {
            "type": "function",
            "function": {
                "name": "code_interpreter",
                "description": "当需要进行数学计算、数据分析、模拟或可视化时，调用此代码解释器。提供一个清晰、具体的自然语言任务描述。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_prompt": {
                            "type": "string",
                            "description": "一个需要通过执行Python代码来完成的任务的详细描述。例如：'使用SciPy库求解方程组：3*x + 2*y = 7, x - y = 1' 或 '模拟1000次抛硬币，并计算正反面出现的次数'。",
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
                "你是一个顶级的数学建模专家。你的任务是分析和解决用户提出的数学问题。"
                "你需要将复杂问题分解成多个步骤。你不能自己编写或执行代码。"
                "当你需要进行任何形式的计算、数据处理、模拟或绘图时，你必须使用 `code_interpreter` 工具来完成。"
                "在调用工具后，你将获得结果，然后你需要基于该结果继续分析或给出最终答案。"
                "请以流式方式输出你的思考过程和最终结论。"
            )
        },
        {"role": "user", "content": user_problem}
    ]

    # 循环处理，直到获得最终答案
    while True:
        # 使用 gemini-1.5-flash-latest 作为主模型，因为它速度快且支持工具调用
        response_stream = litellm.completion(
            model="gemini/gemini-2.0-flash",
            messages=messages,
            tools=tools,
            stream=True,
        )

        # 处理流式响应
        full_response = ""
        tool_calls = []
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

        # 将完整的响应添加到消息历史中
        assistant_message = {"role": "assistant", "content": full_response}

        # 如果有工具调用，添加到assistant消息中
        if current_tool_call["name"] and current_tool_call["arguments"]:
            try:
                # 解析工具调用参数
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
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": current_tool_call["id"],
                            "content": tool_result,
                        }
                    )
            except json.JSONDecodeError:
                # 如果JSON解析失败，继续等待更多数据
                continue
        else:
            # 如果没有工具调用，说明对话结束
            messages.append(assistant_message)
            print("\n\n--- Agent 任务完成 ---", flush=True)
            break


if __name__ == "__main__":
    # --- 示例问题 ---
    # problem = "我有一个投资组合，初始资金为10000元，年化收益率为8%。如果我每月额外定投500元，请计算20年后我的投资组合总价值。请使用公式 M = P(1+r)^n + c[((1+r)^n - 1)/r] 来计算，其中P是初始投资，c是每期定投金额，r是每期利率，n是期数。"
    problem = "鸡兔同笼，共有35个头，94只脚，请问鸡和兔各有多少只？请列出方程组并求解。"
    # problem = "请模拟一个随机漫步过程。一个质点从原点0开始，每次等概率地向左或向右移动1个单位。请模拟100步，并画出它的轨迹图。" # 注意：绘图会在后端执行，但只会返回文本结果。

    mathematical_modeling_agent(problem)
