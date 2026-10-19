"""Owner-scoped experience projection in the existing application database."""
import hashlib
import sqlite3
import time
from pathlib import Path
from contextlib import closing

from models.models import User, Project, ResearchExperience, ResearchExperiencePreference
from ai_system.research.store import ResearchStore, DB_PATH, digest
from ai_system.research.experience import card_for, compatible, problem_type, tokens


def experience_summary(card):
    from ai_system.modules.methods import METHOD_INDEX
    methods=[METHOD_INDEX[key].name for key in card.get('method_keys',[]) if key in METHOD_INDEX]
    kind=card.get('problem_type')
    conditions=card.get('data_conditions',{})
    return {'problem':METHOD_INDEX[kind].name if kind in METHOD_INDEX else '时间序列' if kind=='time_series' else '研究类型未记录',
            'methods':methods,
            'data':{'tabular':'表格数据','files':'附件数据','self_contained':'题内数据'}.get(conditions.get('data_mode'),'数据来源待核对'),
            'structure':{'time_series':'时间序列','integer':'整数变量','continuous':'连续变量'}.get(conditions.get('structure'),'结构条件未记录')}


class ExperienceScope:
    def __init__(self,user_id,work_id=None,session_factory=None,workspace_resolver=None):
        if session_factory is None:
            from database.database import SessionLocal
            session_factory=SessionLocal
        if workspace_resolver is None:
            from services.file_services.workspace_files import workspace_file_service
            workspace_resolver=workspace_file_service.get_workspace_path
        self.user_id=user_id;self.work_id=work_id
        self.sessions=session_factory;self.workspace=workspace_resolver

    def owned(self,db,work_id):
        return db.query(Project).filter(Project.work_id==work_id,Project.created_by==self.user_id).first() is not None

    def enabled(self,db):
        p=db.get(ResearchExperiencePreference,self.user_id)
        return p is None or p.enabled

    def authorize(self,db):
        if db.get(User,self.user_id) is None:raise PermissionError('用户不存在')
        if self.work_id is not None and not self.owned(db,self.work_id):raise PermissionError('当前研究权限已改变')

    def set_enabled(self,value):
        with self.sessions() as db:
            self.authorize(db)
            p=db.get(ResearchExperiencePreference,self.user_id)
            if p is None:p=ResearchExperiencePreference(user_id=self.user_id);db.add(p)
            p.enabled=value;db.commit()

    def index(self,store):
        # Coordinator calls only after its research transaction is durable.
        state=store.snapshot()
        if state is None:return 0
        if Path(self.workspace(self.work_id)).resolve()!=store.root:raise PermissionError('经验来源工作区不匹配')
        count=0
        with self.sessions() as db:
            self.authorize(db)
            for aid in state['attempts']:
                card=card_for(state,aid,store.root)
                if card is None:continue
                card['source']['work_id']=self.work_id
                key=f'{self.user_id}:{self.work_id}:{state["research_id"]}:{aid}'
                eid='E'+hashlib.sha256(key.encode()).hexdigest()[:32]
                row=db.get(ResearchExperience,eid)
                if row is not None and row.deleted:continue
                hashed=digest(card)
                if row is None:
                    row=ResearchExperience(id=eid,user_id=self.user_id,source_work_id=self.work_id,source_key=key,
                        version=1,content_hash=hashed,payload=card,enabled=True,deleted=False,status='candidate',
                        history=[{'version':1,'hash':hashed,'payload':card,'event':'candidate','at':time.time()}]);db.add(row);count+=1
                elif row.content_hash!=hashed:
                    row.payload=card;row.content_hash=hashed;row.version+=1;count+=1
                    row.status='candidate';row.validation=None
                    row.history=[*(row.history or []),{'version':row.version,'hash':hashed,'payload':card,'event':'candidate','at':time.time()}]
            db.commit()
        return count

    def rebuild(self):
        count=0;failures=0
        with self.sessions() as db:
            self.authorize(db)
            works=[w.work_id for w in db.query(Project).filter(Project.created_by==self.user_id)]
        for wid in works:
            root=Path(self.workspace(wid))
            if not (root/DB_PATH).is_file():continue
            try:
                probe=ResearchStore(root,'experience-index',read_only=True)
                with closing(probe._connect()) as conn:
                    ids=[r[0] for r in conn.execute('SELECT id FROM research')]
                scoped=ExperienceScope(self.user_id,wid,self.sessions,self.workspace)
                for rid in ids:count+=scoped.index(ResearchStore(root,rid,read_only=True))
            except Exception:
                failures+=1
        return {'updated':count,'unavailable_sources':failures}

    def available(self,db,row,*,require_adopted=True):
        if row.deleted or row.user_id!=self.user_id or not self.owned(db,row.source_work_id):return False
        if require_adopted and (not row.enabled or row.status!='adopted' or not row.validation or row.validation.get('hash')!=row.content_hash):return False
        try:
            card=row.payload;source=card['source'];root=Path(self.workspace(row.source_work_id))
            if card['evidence_refs'] and not card.get('evidence_provenance'):return False
            if not (root/DB_PATH).is_file():return False
            store=ResearchStore(root,source['research_id'],read_only=True);state=store.snapshot()
            if state is None or source['attempt_id'] not in state['attempts']:return False
            actual=card_for(state,source['attempt_id'],store.root)
            if actual is None:return False
            frozen=state['attempts'][source['attempt_id']].get('experience_evidence')
            if frozen and actual['evidence_provenance']!=frozen:return False
            actual['source']['work_id']=row.source_work_id
            if digest(actual)!=row.content_hash:return False
            for ref in card['evidence_refs']:
                entry=state['evidence'][ref]
                if store._artifact_hash(entry['payload'])!=entry.get('artifact_hash'):return False
            files={k:v for k,v in source['input_refs'].items() if not k.startswith(('task:','assumption:'))}
            return store.file_inputs()==files and source['published']
        except (OSError,ValueError,KeyError,TypeError,sqlite3.DatabaseError):return False

    def list(self,query=""):
        with self.sessions() as db:
            self.authorize(db)
            rows=db.query(ResearchExperience).filter(ResearchExperience.user_id==self.user_id,ResearchExperience.deleted==False).order_by(ResearchExperience.source_key).all()
            return {'enabled':self.enabled(db),'cards':[{'id':r.id,'version':r.version,'hash':r.content_hash,
                'enabled':r.enabled,'available':self.available(db,r),'status':r.status,'validation':r.validation,
                'history':[{'version':h['version'],'event':h['event'],'at':h.get('at')} for h in (r.history or [])], 'card':r.payload,'summary':experience_summary(r.payload)} for r in rows
                if self.owned(db,r.source_work_id) and (not query or query.lower() in str(r.payload).lower())]}

    def reference_sources(self, refs):
        """Describe the exact frozen versions without upgrading or reviving them."""
        result=[]
        with self.sessions() as db:
            self.authorize(db)
            for ref in refs:
                entry={'id':ref['id'],'version':ref['version'],'title':'来源不可访问','source_work_id':None}
                row=db.query(ResearchExperience).filter(ResearchExperience.id==ref['id'],ResearchExperience.user_id==self.user_id).first()
                if row is not None and self.owned(db,row.source_work_id):
                    if row.deleted:
                        entry['title']='已删除的经验'
                    else:
                        payload=(row.payload if row.version==ref['version'] and row.content_hash==ref['hash'] else
                                 next((h.get('payload') for h in reversed(row.history or [])
                                       if h.get('version')==ref['version'] and h.get('hash')==ref['hash'] and h.get('payload')),None))
                        entry.update(title=(payload or {}).get('question') or '历史经验版本',source_work_id=row.source_work_id)
                result.append(entry)
        return result

    def change(self,eid,*,enabled=None,delete=False):
        with self.sessions() as db:
            self.authorize(db)
            row=db.query(ResearchExperience).filter(ResearchExperience.id==eid,ResearchExperience.user_id==self.user_id).first()
            if row is None or row.deleted or not self.owned(db,row.source_work_id):raise KeyError('经验不存在或不可访问')
            if delete:row.deleted=True;row.enabled=False;row.payload={};row.history=[];row.validation=None;row.status='deleted'  # Keep source-key tombstone.
            elif enabled is not None:row.enabled=enabled
            db.commit()

    def transition(self,eid,action,base_version):
        with self.sessions() as db:
            self.authorize(db)
            row=db.query(ResearchExperience).filter(ResearchExperience.id==eid,ResearchExperience.user_id==self.user_id).with_for_update().first()
            if row is None or row.deleted or not self.owned(db,row.source_work_id):raise KeyError('经验不存在或不可访问')
            if row.version!=base_version:raise ValueError('经验版本已变化，请刷新后操作')
            if action=='validate':
                if not self.available(db,row,require_adopted=False):raise ValueError('来源或证据已失效，不能验证经验')
                card=row.payload;checks=card.get('checks',[])
                if card.get('level') not in {'verified','counterexample'} or not checks or any(c['outcome']=='inconclusive' for c in checks):
                    raise ValueError('候选缺少可核验的契约证据，不能视为已验证方法')
                row.validation={'hash':row.content_hash,'version':row.version,'checks':checks,
                    'source':card['source'],'policy':'source-contract-evidence-v1','at':time.time()}
                row.status='validated'
            elif action=='adopt':
                if row.status!='validated' or not row.validation or row.validation['hash']!=row.content_hash or not self.available(db,row,require_adopted=False):
                    raise ValueError('仅能采用当前版本已验证且来源有效的经验')
                row.status='adopted';row.enabled=True
            elif action=='withdraw':row.status='withdrawn';row.enabled=False
            else:raise ValueError('未知经验操作')
            row.history=[*(row.history or []),{'version':row.version,'hash':row.content_hash,
                'event':row.status,'at':time.time()}]
            db.commit()
            return {'id':row.id,'version':row.version,'status':row.status}

    def search(self,question,methods,actual):
        with self.sessions() as db:
            self.authorize(db)
            if not self.enabled(db):return []
            query=tokens(question);hits=[]
            rows=db.query(ResearchExperience).filter(ResearchExperience.user_id==self.user_id,
                ResearchExperience.enabled==True,ResearchExperience.deleted==False,
                ResearchExperience.source_work_id!=self.work_id).all()
            for row in rows:
                p=row.payload
                wanted=problem_type([],question)
                if wanted!='unspecified' and p['problem_type']!='unspecified' and wanted!=p['problem_type']:continue
                if not compatible(p['data_conditions'],actual):continue
                common=set(methods)&set(p['method_keys'])
                if methods and not common:continue
                overlap=len(query&tokens(p['question']))
                if not overlap and not common:continue
                if not self.available(db,row):continue
                score=4*len(common)+min(overlap,10)+(2 if p['level']=='verified' else 0)
                reason=f'方法命中：{", ".join(sorted(common)) or "待选择"}；问题词命中 {overlap}；已知条件相容，未知条件待核验'
                hits.append((score,{'id':row.id,'version':row.version,'hash':row.content_hash,'card':p,'reason':reason}))
            return [v for _,v in sorted(hits,key=lambda item:(-item[0],item[1]['id']))[:3]]

    def valid_refs(self,refs):
        try:
            with self.sessions() as db:
                self.authorize(db)
                if not self.enabled(db):return not refs
                for ref in refs:
                    row=db.get(ResearchExperience,ref['id'])
                    if row is None or row.user_id!=self.user_id or row.version!=ref['version'] or row.content_hash!=ref['hash'] or not self.available(db,row):return False
            return True
        except Exception:return False


def current_usage(store):
    state=store.snapshot() or {}
    return [{'attempt_id':aid,'task_id':a['task_id'],'revision':a['revision'],
             'published':a.get('commit',{}).get('published',False),'refs':a.get('experience_refs',[])}
            for aid,a in state.get('attempts',{}).items() if a.get('experience_refs')]
