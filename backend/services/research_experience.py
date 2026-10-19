"""Owner-scoped experience projection in the existing application database."""
import hashlib
import sqlite3
from pathlib import Path
from contextlib import closing

from models.models import Work, ResearchExperience, ResearchExperiencePreference
from ai_system.research.store import ResearchStore, DB_PATH, digest
from ai_system.research.experience import card_for, compatible, problem_type, tokens

class ExperienceScope:
    def __init__(self,user_id,work_id,session_factory=None,workspace_resolver=None):
        if session_factory is None:
            from database.database import SessionLocal
            session_factory=SessionLocal
        if workspace_resolver is None:
            from services.file_services.workspace_files import workspace_file_service
            workspace_resolver=workspace_file_service.get_workspace_path
        self.user_id=user_id;self.work_id=work_id
        self.sessions=session_factory;self.workspace=workspace_resolver

    def owned(self,db,work_id):
        return db.query(Work).filter(Work.work_id==work_id,Work.created_by==self.user_id).first() is not None

    def enabled(self,db):
        p=db.get(ResearchExperiencePreference,self.user_id)
        return p is None or p.enabled

    def authorize(self,db):
        if not self.owned(db,self.work_id):raise PermissionError('当前研究权限已改变')

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
                        version=1,content_hash=hashed,payload=card,enabled=True,deleted=False);db.add(row);count+=1
                elif row.content_hash!=hashed:
                    row.payload=card;row.content_hash=hashed;row.version+=1;count+=1
            db.commit()
        return count

    def rebuild(self):
        count=0;failures=0
        with self.sessions() as db:
            self.authorize(db)
            works=[w.work_id for w in db.query(Work).filter(Work.created_by==self.user_id).limit(100)]
        for wid in works:
            root=Path(self.workspace(wid))
            if not (root/DB_PATH).is_file():continue
            try:
                probe=ResearchStore(root,'experience-index',read_only=True)
                with closing(probe._connect()) as conn:
                    ids=[r[0] for r in conn.execute('SELECT id FROM research LIMIT 100')]
                scoped=ExperienceScope(self.user_id,wid,self.sessions,self.workspace)
                for rid in ids:count+=scoped.index(ResearchStore(root,rid,read_only=True))
            except Exception:
                failures+=1
        return {'updated':count,'unavailable_sources':failures}

    def available(self,db,row):
        if row.deleted or not row.enabled or row.user_id!=self.user_id or not self.owned(db,row.source_work_id):return False
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

    def list(self):
        with self.sessions() as db:
            self.authorize(db)
            rows=db.query(ResearchExperience).filter(ResearchExperience.user_id==self.user_id,ResearchExperience.deleted==False).order_by(ResearchExperience.source_key).limit(300).all()
            return {'enabled':self.enabled(db),'cards':[{'id':r.id,'version':r.version,'hash':r.content_hash,
                'enabled':r.enabled,'available':self.available(db,r),'card':r.payload} for r in rows
                if self.owned(db,r.source_work_id)]}

    def change(self,eid,*,enabled=None,delete=False):
        with self.sessions() as db:
            self.authorize(db)
            row=db.query(ResearchExperience).filter(ResearchExperience.id==eid,ResearchExperience.user_id==self.user_id).first()
            if row is None or row.deleted or not self.owned(db,row.source_work_id):raise KeyError('经验不存在或不可访问')
            if delete:row.deleted=True;row.enabled=False;row.payload={}  # Keep source-key tombstone.
            elif enabled is not None:row.enabled=enabled
            db.commit()

    def search(self,question,methods,actual):
        with self.sessions() as db:
            self.authorize(db)
            if not self.enabled(db):return []
            query=tokens(question);hits=[]
            rows=db.query(ResearchExperience).filter(ResearchExperience.user_id==self.user_id,
                ResearchExperience.enabled==True,ResearchExperience.deleted==False,
                ResearchExperience.source_work_id!=self.work_id).limit(300).all()
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
