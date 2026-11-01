"""Read-only display of historical paper comments; all new messages use chat."""
import json
from pathlib import Path
from services.file_services.workspace_layout import workspace_path


def chat_history(root):
    """Read-only conversation projection; threads/executions remain authoritative."""
    import sqlite3
    from contextlib import closing
    from datetime import datetime, timezone
    path=workspace_path(Path(root), '.system/research.sqlite')
    if not path.is_file():return []
    with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'paper_threads','paper_executions'}<=tables:return []
        executions={(rid,row['id']):row for rid,raw in db.execute('SELECT research_id,payload FROM paper_executions') for row in [json.loads(raw)]}
        threads=[(rid,json.loads(raw)) for rid,raw in db.execute('SELECT research_id,payload FROM paper_threads ORDER BY rowid')]
    messages=[];counts={};latest={}
    def append(rid,execution_id,role,text,at,state='historical'):
        key=(rid,execution_id,role);index=counts.get(key,0);counts[key]=index+1
        row={'id':f'paper:{rid}:{execution_id}:{role}:{index}','role':role,'content':text,
             'timestamp':datetime.fromtimestamp(at,timezone.utc).isoformat(),
             'paper_execution':{'id':execution_id,'research_id':rid,'state':state}}
        messages.append(row)
        if role=='assistant':latest[(rid,execution_id)]=row
    for rid,thread in threads:
        for index,message in enumerate(thread['messages']):
            execution_id=message.get('execution_id') or f"{thread['id']}:{index}"
            execution=executions.get((rid,execution_id),{})
            text=message['text']
            references=execution.get('references') or [execution]
            quotes=[r.get('quote') or (r.get('selection') or {}).get('quote') or r.get('read_quote') for r in references]
            if message['role']=='user' and any(quotes):
                text='\n\n'.join('> '+quote.replace('\n','\n> ') for quote in quotes if quote)+'\n\n'+text
            append(rid,execution_id,message['role'],text,message.get('at',execution.get('created_at',thread['created_at'])))
    for (rid,execution_id),execution in executions.items():
        state=execution['state']
        if state in {'pending','running'}:state='interrupted'
        if (rid,execution_id) not in latest:
            text=execution.get('answer') or execution.get('error') or '旧评论未完成，可在主对话中继续处理。'
            append(rid,execution_id,'assistant',text,execution.get('updated_at',execution['created_at']),state)
        else:
            latest[(rid,execution_id)]['paper_execution']['state']=state
    return messages
