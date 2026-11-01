"""Old comments remain readable without keeping their retired executor alive."""
from services.chat_services import paper_project as project
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.chat_service import ChatService


def test_historical_comments_are_static_and_do_not_restart_or_duplicate(paper):
    store,_=paper
    manager=ChatHistoryManager(str(store.root.parent))
    manager.save_message(store.root.name,'user','先前研究消息')
    service=ChatService(None);service.history_manager=manager
    project.initialize(store)
    thread={'id':'old-thread','created_at':1,'messages':[{'role':'user','text':'解释这段原文','execution_id':'old-comment','at':2}]}
    record={'id':'old-comment','thread_id':'old-thread','state':'running','message':'解释这段原文',
            'selection':{'quote':'历史原文'},'created_at':2}
    with store._transaction() as db:
        project.put(db,store,'paper_threads',thread)
        project.put(db,store,'paper_executions',record)
    first=service.get_work_chat_history_for_frontend(store.root.name)
    assert len(first)==3
    old_user=next(m for m in first if m['role']=='user' and m['id'].startswith('paper:'))
    assert old_user['content'].startswith('> 历史原文')
    old_reply=next(m for m in first if m['role']=='assistant')
    assert old_reply['paper_execution']['state']=='interrupted'
    assert '主对话' in old_reply['content']
    assert service.get_work_chat_history_for_frontend(store.root.name)==first
    assert len(manager.get_messages(store.root.name))==1
    assert project.describe(store)['executions'][0]['state']=='interrupted'
