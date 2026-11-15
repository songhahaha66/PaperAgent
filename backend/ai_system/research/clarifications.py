"""Record one answer before resuming the shared Agent session."""
from uuid import uuid4
from .models import ResearchConflict


def receive(store, action_id, response):
    with store._transaction() as db:
        state=store._read(db);action=state.get('pending_input') or {}
        rows=state.setdefault('clarifications',[])
        existing=next((row for row in rows if row.get('action_id')==action_id),None)
        if existing:
            if existing['response']!=response:raise ResearchConflict('该问题已收到其他回答，请作为新补充信息提交')
            return existing
        if action.get('id')!=action_id:
            raise ResearchConflict('待补充问题已改变，请重试')
        row={'ref':'clarification:'+str(len(rows)+1),'action_id':action_id,
             'question':action['message'],'response':response}
        rows.append(row);state['pending_input']=None
        state['conversation_input']={'id':'user:'+uuid4().hex,'message':response,
                                     'question_id':None,'outcome':state['outcome']}
        store._save(db,state,'clarification_received',row)
        return row
