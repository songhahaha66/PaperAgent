"""Chat is a projection of durable Agent turns, not a second execution model."""
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4
import logging
import re
import time
from ai_system.agent.context import new_turn
from ai_system.agent.protocol import MessageItem, NoticeItem
from ai_system.agent.projection import public_block
from ai_system.agent.store import AgentStore

# Domain notifications refresh panels. Only authored messages, requests and
# notices enter the conversation; tool progress comes exclusively from runtime.
DOMAIN_NOTIFICATIONS = {'main_agent_start','paper_stage','paper_phase','paper_code','paper_solved',
                        'paper_export','paper_validation','plan_updated','file_changed',
                        'research_snapshot','research_state','execution_changed',
                        'call_code_agent','call_writing_agent','word_tool_call'}


def public_reply(reply, *, root=None):
    if reply.get('message_type') != 'agent_turn':
        return {k:v for k,v in reply.items() if k not in {'segments','json_blocks'}}
    if root is None:
        return reply
    turn_id = reply['metadata']['turn_id']
    store = AgentStore(root, read_only=True)
    snapshot = store.turn_snapshot(turn_id)
    items = snapshot['items']
    metadata = {**reply['metadata'], 'event_seq': snapshot['event_seq']}
    if snapshot['turn']:metadata['status'] = snapshot['turn']['status']
    content = '\n\n'.join(i['text'] for i in items if i['type']=='agentMessage' and i['thread_id']==metadata['thread_id'])
    return {**reply, 'items':items, 'content':content, 'metadata':metadata}


class TurnWriter:
    """Persist before publishing. Chat-message storage is a rebuildable index.

    Authored token streams flush every 500 ms / 4 KiB. All visible tool changes
    are already committed by ToolRuntime and are forwarded without re-parsing.
    """
    def __init__(self, history, work_id, chat_task_id, root, publish, *, user_message=None):
        self.history, self.work_id, self.root, self.publish = history, work_id, Path(root), publish
        self.lock = RLock()
        self.pending = {}
        self.active_text = None
        self.pending_chars = 0
        turn_id = uuid4().hex
        self.reply = {'id':turn_id,'role':'assistant','content':'','timestamp':datetime.now().isoformat(),
                      'message_type':'agent_turn','items':[],
                      'metadata':{'system_type':'brain','chat_task_id':chat_task_id,'reply_id':turn_id,
                                  'thread_id':work_id,'turn_id':turn_id,'revision':1,'event_seq':0,'status':'running'}}
        # Save the turn locator before events, so a crash cannot orphan a reply.
        self.history.upsert_reply(self.work_id, self.reply)
        self.turn = new_turn(root, work_id, turn_id=turn_id, publish=self.accept, lock=self.lock)
        if user_message:
            self.message(user_message['content'], role='userMessage', identity='user:'+turn_id)

    @property
    def id(self):return self.reply['id']

    def accept(self, event):
        with self.lock:
            metadata = self.reply['metadata']
            if event['seq'] <= metadata['event_seq']:return
            item = event.get('item')
            if item:
                rows = self.reply['items']
                index = next((n for n,row in enumerate(rows) if row['id']==item['id']),None)
                if index is None:rows.append(item)
                else:rows[index] = item
                if event['method']=='item/started' and item['id']!=self.active_text and self.active_text:
                    self.pending[self.active_text]['sealed'] = True
                    self.active_text = None
            if event['method']=='turn/completed' and event['turn_id']==metadata['turn_id']:
                metadata['status'] = event['turn']['status']
            metadata['event_seq'] = event['seq']
            metadata['revision'] += 1
            self.reply['content'] = '\n\n'.join(i['text'] for i in self.reply['items']
                if i['type']=='agentMessage' and i['thread_id']==metadata['thread_id'])
            # The event log is authoritative if this cache or the socket fails.
            try:self.history.upsert_reply(self.work_id, self.reply)
            except Exception:logging.getLogger(__name__).exception('Chat turn projection will rebuild from Items')
            self.publish({'reply_id':self.id,'chat_task_id':metadata['chat_task_id'],
                          'thread_id':metadata['thread_id'],'turn_id':metadata['turn_id'],'event':event})

    def message(self, text, *, role='agentMessage', phase='commentary', identity=None):
        with self.lock:
            if self.reply['metadata']['status']!='running' or not text.strip():return
            self.flush(seal=True)
            item = MessageItem(id=identity or uuid4().hex,thread_id=self.turn.thread_id,turn_id=self.turn.turn_id,
                               type=role,phase=phase,text=text.strip(),started_at=time.time()).model_dump(exclude_none=True)
            if self.turn.store.get_item(item['id']):return
            self.turn.put(item,'item/started')
            item.update(status='completed',finished_at=time.time())
            self.turn.put(item,'item/completed')

    def text(self, text):
        with self.lock:
            if self.reply['metadata']['status']!='running' or not text:return
            if self.active_text is None:
                item = MessageItem(id=uuid4().hex,thread_id=self.turn.thread_id,turn_id=self.turn.turn_id,
                                   type='agentMessage',phase='final_answer',text='',started_at=time.time()).model_dump(exclude_none=True)
                self.active_text = item['id']
                self.pending[item['id']] = {'item':item,'sealed':False,'dirty':False}
                self.turn.put(item,'item/started')
            pending = self.pending[self.active_text]
            pending['item']['text'] += text
            pending['dirty'] = True
            self.pending_chars += len(text)
            if self.pending_chars>=4096:self.flush()

    def commentary(self, content):
        if not isinstance(content,dict):return
        identity,text = content.get('id'),content.get('text')
        if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{64}',identity):return
        if isinstance(text,str):self.message(text,identity=self.id+':commentary:'+identity)

    def block(self, block):
        name = block.get('type')
        if name=='research_commentary':
            self.commentary(block.get('content'))
            return
        if name in DOMAIN_NOTIFICATIONS:return
        with self.lock:
            if self.reply['metadata']['status']!='running':return
            self.flush(seal=True)
            block=public_block(self.root,block)
            if name=='main_agent_complete' and isinstance(block['content'],str):
                previous=next((i for i in reversed(self.reply['items']) if i['thread_id']==self.turn.thread_id),None)
                if previous and previous['type']=='agentMessage' and previous['text'].strip()==block['content'].strip():return
                self.message(block['content'],phase='final_answer')
                return
            item=NoticeItem(id=uuid4().hex,thread_id=self.turn.thread_id,turn_id=self.turn.turn_id,
                            type='inputRequest' if name in {'paper_confirm','paper_clarification','main_agent_confirm'} else 'notice',
                            name=name or 'notice',content=block['content'] or '',started_at=time.time()).model_dump(exclude_none=True)
            self.turn.put(item,'item/started')
            item.update(status='completed',finished_at=time.time())
            self.turn.put(item,'item/completed')

    def flush(self, *, seal=False):
        with self.lock:
            if seal:self.active_text=None
            for identity,pending in list(self.pending.items()):
                item=pending['item']
                if seal or pending['sealed']:
                    item.update(status='completed',finished_at=time.time())
                    self.turn.put(item,'item/completed')
                    del self.pending[identity]
                elif pending['dirty']:
                    self.turn.put(item,'item/agentMessage/delta')
                    pending['dirty']=False
            self.pending_chars=0

    def finish(self,status,notice=None):
        with self.lock:
            if self.reply['metadata']['status']!='running':return
            if notice:self.message(notice)
            self.flush(seal=True)
            state = 'completed' if status in {'completed','done'} else 'failed' if status in {'failed','error'} else 'interrupted'
            self.turn.finish(state)


def resolve_items(root, reply, identities):
    """Membership in the authorized root turn is the only detail lookup boundary."""
    turn_id = reply.get('metadata',{}).get('turn_id')
    if not turn_id:return []
    allowed = {item['id']:item for item in AgentStore(root,read_only=True).items(root_turn_id=turn_id,limit=100000)}
    return [allowed[identity] for identity in dict.fromkeys(identities) if identity in allowed]
