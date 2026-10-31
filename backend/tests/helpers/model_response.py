"""Controlled model response for HTTP adapter tests."""


def response(finish='stop', tokens=10):
    return {'id':'offline','object':'chat.completion','created':1,'model':'unknown-gateway-model',
        'choices':[{'index':0,'message':{'role':'assistant','content':'done'},'finish_reason':finish}],
        'usage':{'prompt_tokens':20,'completion_tokens':tokens,'total_tokens':20+tokens,
                 'completion_tokens_details':{'reasoning_tokens':max(0,tokens-1)}}}
