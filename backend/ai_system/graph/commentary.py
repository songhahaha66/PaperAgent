"""Publish the research Agent's public text before its tools execute.

Only message text is eligible; reasoning blocks, tool arguments and auxiliary
model output are not public messages. Legacy progress wrappers remain readable.
"""
import hashlib
import re
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

PROGRESS_PROMPT = """
面向用户的进展说明：开始处理任务、发现影响下一步的问题、改变做法或准备交付时，在回复正文中用
一两句简短的公开说明，说明刚发现什么、接下来做什么及必要原因，再调用工具。
这是给用户看的进展，不是内部推理、完整计划、代码或报告；只陈述实际观察，未执行的操作用将来时。
首轮先简短说明本轮要做什么；之后仅在有实质进展时说明，连续读取或登记不逐项播报。
直接描述研究内容和发现，不向用户播报建立问题、登记引用、获取 ID 等内部操作，不出现 question_id、E1/E2、调用参数或协议字段。
例如“先用前八天拟合两种预测方法，再比较后四天误差”。代码算完不等于整个实验完成；遇到阻碍时说明已完成的部分和剩余工作。
说明与工具可在同一回复中给出，不要为说明单独调用模型或工具，也不要因漏写说明而重试。
"""


def public_progress(content):
    if isinstance(content, list):
        content = '\n'.join(block.get('text', '') for block in content
                            if isinstance(block, dict) and block.get('type') == 'text')
    if not isinstance(content, str):
        return []
    # Discard complete and unterminated reasoning regions, including fake markers inside them.
    content = re.sub(r'<(think|analysis|reasoning)\b[^>]*>.*?(?:</\1\s*>|$)', '', content,
                     flags=re.S | re.I)
    content = re.sub(r'</?progress\s*>', '', content, flags=re.I).strip()
    return [content] if content else []


class ProgressMiddleware(AgentMiddleware):
    def __init__(self, events, *, scope):
        super().__init__()
        self.events = events
        self.scope = scope
        self.seen = set()

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        for message in response.result:
            if not isinstance(message, AIMessage):
                continue
            identity = message.id or hashlib.sha256(
                (str(message.content) + str(message.tool_calls)).encode()).hexdigest()
            for index, text in enumerate(public_progress(message.content)):
                key = hashlib.sha256(f'{self.scope}:{identity}:{index}'.encode()).hexdigest()
                if key in self.seen:
                    continue
                await self.events.emit('research_commentary', {
                    'id': key, 'text': text, 'scope': self.scope,
                })
                self.seen.add(key)
        return response
