"""Read-only recovery of comment messages in the existing display history."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.chat_services import paper_comments as comments, paper_project as project
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.chat_service import ChatService


def test_comment_history_recovers_pending_then_completed_without_copying_messages(paper):
    store,_=paper
    manager=ChatHistoryManager(str(store.root.parent))
    manager.save_message(store.root.name,'user','先前研究消息')
    service=ChatService(None);service.history_manager=manager
    doc=project.snapshot(store.root)
    text=next(r['text'] for r in doc['files'] if r['path']=='paper.md')
    bound={'file':'paper.md','version':doc['version'],'start':0,'end':2,'quote':text[:2]}
    record,_=comments.create(store,'recover-comment','解释这段原文',bound)
    first=service.get_work_chat_history_for_frontend(store.root.name)
    assert len(first)==3 and first[0]['content']=='先前研究消息'
    user,pending=first[1:]
    assert user['content'].endswith('解释这段原文') and user['content'].startswith('> '+text[:2])
    assert pending['paper_execution']['state']=='pending'
    assert service.get_work_chat_history_for_frontend(store.root.name)==first
    comments.finish(store,record,'这是基于原文的回复')
    complete=service.get_work_chat_history_for_frontend(store.root.name)
    assert [m['id'] for m in complete]==[m['id'] for m in first]
    assert complete[-1]['content']=='这是基于原文的回复'
    assert complete[-1]['paper_execution']['state']=='completed'
    assert len(manager.get_messages(store.root.name))==1
    assert len(project.read_records(store,'paper_threads')[0]['messages'])==2
