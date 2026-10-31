"""One idempotent writer for either supported graph-resume entry point."""
from .models import ResearchConflict


def receive(store, action_id, response):
    with store._transaction() as db:
        state=store._read(db);action=state.get('action') or {}
        if action.get('id')!=action_id or action.get('capability')!='ask':
            raise ResearchConflict('待补充问题已改变，请重试')
        rows=state.setdefault('clarifications',[])
        existing=next((row for row in rows if row.get('action_id')==action_id),None)
        if existing:
            if existing['response']!=response:raise ResearchConflict('该问题已收到其他回答，请作为新补充信息提交')
            return existing
        if action.get('status')!='waiting':raise ResearchConflict('该问题当前不等待补充信息')
        row={'ref':'clarification:'+str(len(rows)+1),'action_id':action_id,
             'question':action['inputs']['message'],'response':response}
        rows.append(row);action['status']='done'
        store._save(db,state,'clarification_received',row)
        return row
