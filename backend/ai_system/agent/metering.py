"""Shared model-input accounting; transport serialization is never a token meter."""
import json
from langchain_core.messages import AIMessage, ToolMessage

def message_estimate(messages, *, image_counter=None):
    """Count model content, not response headers or encrypted transport bytes.

    Opaque reasoning keeps its provider payload. Its returned output-token usage
    bounds the generated item instead of tokenizing the encrypted ciphertext.
    Actual input usage remains the anchor for subsequent unchanged prefixes.
    """
    total=0
    for message in messages:
        content=message.content
        opaque=False
        if isinstance(message,AIMessage) and isinstance(content,list):
            opaque=any(isinstance(part,dict) and part.get('type') in {'reasoning','redacted_thinking'} for part in content)
            content=[part for part in content if not isinstance(part,dict)
                     or part.get('type') not in {'reasoning','redacted_thinking'}]
        value={'role':message.type,'content':content}
        if isinstance(message,AIMessage):
            value['tool_calls']=message.tool_calls
            if message.additional_kwargs.get('reasoning_content'):
                value['reasoning_content']=message.additional_kwargs['reasoning_content']
        if isinstance(message,ToolMessage):value.update(name=message.name,tool_call_id=message.tool_call_id)
        count=token_estimate(value,image_counter=image_counter)
        if opaque:count=max(count,(message.usage_metadata or {}).get('output_tokens',0))
        total+=count
    return total


def token_estimate(value,*,image_counter=None):
    if isinstance(value,list):return sum(token_estimate(v,image_counter=image_counter) for v in value)
    if isinstance(value,dict):
        if value.get('type')=='image_ref':return value['tokens']
        if value.get('type') in {'image','image_url'} and ('image_url' in value or 'source' in value):
            if image_counter is None:
                from ..config.generation import ModelContextLimit
                raise ModelContextLimit('图片计量需要指定模型，不能使用固定token常数。')
            return image_counter(value)
        return sum(token_estimate(k,image_counter=image_counter)+token_estimate(v,image_counter=image_counter) for k,v in value.items())
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,default=str)
    return sum(1 if ord(c)>127 else .3 for c in text).__ceil__()



def request_estimate(messages, tools=(), *, image_counter=None):
    return message_estimate(messages,image_counter=image_counter)+token_estimate(list(tools),image_counter=image_counter)


def input_limit(model):
    profile=getattr(model,'profile',None) or {}
    limit=int(profile.get('max_input_tokens') or profile.get('max_context_tokens') or 32000)
    if profile.get('max_context_tokens'):
        limit=min(limit,int(profile['max_context_tokens'])-int(getattr(model,'max_tokens',None) or 16000))
    return limit
