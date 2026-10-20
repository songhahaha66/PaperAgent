"""Durable request usage, explicit/legacy limits and operation failure guards."""
import json
import time
from contextlib import closing,contextmanager
from contextvars import ContextVar
from .store import ResearchStore

ACTIVE_BUDGET=ContextVar('paperagent_request_budget',default=None)
BUDGET_ID='workspace-model-budget'


class ResearchBudgetExhausted(RuntimeError):pass
class RepeatedOperationBlocked(RuntimeError):pass
class ModelServiceError(RuntimeError):pass

ACTIVE_MODEL_REQUEST=ContextVar('paperagent_model_request',default=None)


class ResearchRuntime:
    def __init__(self,root,research_id='workspace-runtime'):
        self.active_research_id=research_id
        self.store=ResearchStore(root,BUDGET_ID)

    def extend_limit(self,extra_requests,*,expected_limit,command_id,authorization):
        """Apply an explicitly authorized small extension; never reset usage or provider faults."""
        from .store import digest
        if not 1<=extra_requests<=5 or not command_id or not authorization:raise ValueError('追加需要明确授权，且单次仅支持1至5次')
        signature=digest({'extra':extra_requests,'expected_limit':expected_limit,'authorization':authorization})
        with self.store._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget_extensions(command_id TEXT PRIMARY KEY,signature TEXT NOT NULL,payload TEXT NOT NULL)')
            prior=db.execute('SELECT signature,payload FROM budget_extensions WHERE command_id=?',(command_id,)).fetchone()
            if prior:
                if prior[0]!=signature:raise ValueError('追加命令已绑定其他授权')
                return json.loads(prior[1])
            row=db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(BUDGET_ID,)).fetchone()
            if not row:raise ValueError('没有已冻结的请求预算')
            state=json.loads(row[0])
            if state.get('request_limit') is None:raise ValueError('当前没有调用上限，无需追加')
            if state['request_limit']!=expected_limit:raise ValueError('当前上限已变化，请重新核对')
            if state['state']=='provider_blocked':raise ValueError('网关授权/额度拒绝不能由本地预算追加解除')
            receipt={'command_id':command_id,'research_id':self.active_research_id,'old_limit':expected_limit,
                'new_limit':expected_limit+extra_requests,'used_at_extension':state['used'],'authorization':authorization,'recorded_at':time.time()}
            state['request_limit']=receipt['new_limit'];state['state']='active' if state['used']<state['request_limit'] else 'budget_exhausted'
            db.execute('UPDATE request_budgets SET payload=? WHERE research_id=?',(json.dumps(state),BUDGET_ID))
            policy=db.execute('SELECT payload FROM runtime_policy WHERE id=1').fetchone()
            if policy:
                settings=json.loads(policy[0]);settings['request_limit']=receipt['new_limit']
                db.execute('UPDATE runtime_policy SET payload=? WHERE id=1',(json.dumps(settings),))
            db.execute('INSERT INTO budget_extensions VALUES (?,?,?)',(command_id,signature,json.dumps(receipt,ensure_ascii=False)))
        return receipt

    def policy(self):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM runtime_policy WHERE id=1').fetchone()
        if row is None:raise ValueError('运行配置尚未迁移')
        return json.loads(row[0])

    def configure(self,settings):
        limit=settings.get('request_limit')
        if limit is not None and (type(limit) is not int or limit<0):raise ValueError('内部请求上限必须为非负整数')
        policy={'policy_version':2,'preference_source':'explicit','request_limit':limit,'timeout_seconds':settings.get('timeout_seconds'),
                'limit_source':'explicit' if limit is not None else 'default',
                'timeout_source':'explicit' if settings.get('timeout_seconds') is not None else 'model_connection',
                'compact_paper':False,'confirm_models':False,**settings}
        with self.store._transaction() as db:db.execute('INSERT OR REPLACE INTO runtime_policy VALUES (1,?)',(json.dumps(policy),))

    def status(self,research_id=None):
        policy=self.policy()
        with closing(self.store._connect()) as db:r=db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(BUDGET_ID,)).fetchone()
        state={**policy,**(json.loads(r[0]) if r else {'used':0,'state':'ready','metering_version':2})}
        state.setdefault('input_tokens',0);state.setdefault('output_tokens',0);state.setdefault('usage_reported_calls',0)
        state['stop_reason']=self.stop_reason()
        return state

    def remaining(self):
        state=self.status();limit=state.get('request_limit')
        return None if limit is None else max(0,limit-state['used'])

    def reserve(self,host):
        try:return self._reserve(host)
        except ResearchBudgetExhausted:
            state=self.status()
            self.note_stop('legacy_limit' if state.get('limit_source')=='legacy' else 'explicit_limit',f"已达到请求上限（{state['used']}/{state['request_limit']}），计数和已完成结果保留")
            raise

    def _reserve(self,host):
        from ..graph.tracing import CURRENT_LABEL
        policy=self.policy()
        with self.store._transaction() as db:
            r=db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(BUDGET_ID,)).fetchone()
            state={**policy,**(json.loads(r[0]) if r else {'used':0,'state':'ready','metering_version':2})}
            limit=state.get('request_limit')
            if limit is not None and state['used']>=limit:
                reason={'kind':'explicit_limit' if state.get('limit_source')!='legacy' else 'legacy_limit',
                        'message':f"已达到请求上限（{state['used']}/{limit}），计数和已完成结果保留"}
                self._stop(db,reason)
                raise ResearchBudgetExhausted(reason['message'])
            state['used']+=1;state['state']='budget_exhausted' if limit is not None and state['used']>=limit else 'active'
            seq=state['used'];row={'sequence':seq,'sent_at':time.time(),'host':host,'label':CURRENT_LABEL.get(),'research_id':self.active_research_id,'state':'sent'}
            db.execute('INSERT OR REPLACE INTO request_budgets VALUES (?,?)',(BUDGET_ID,json.dumps(state)))
            db.execute('INSERT INTO model_requests VALUES (?,?,?)',(BUDGET_ID,seq,json.dumps(row)))
        return seq

    def response(self,sequence,status):
        with self.store._transaction() as db:
            r=db.execute('SELECT payload FROM model_requests WHERE research_id=? AND sequence=?',(BUDGET_ID,sequence)).fetchone()
            if r:
                row=json.loads(r[0]);row.update(http_status=status,headers_at=time.time(),state='http_response')
                db.execute('UPDATE model_requests SET payload=? WHERE research_id=? AND sequence=?',(json.dumps(row),BUDGET_ID,sequence))

    def usage(self,sequence,usage=None,*,state="completed"):
        usage=usage or {};known=bool(usage)
        values={key:max(0,int(usage.get(source) or 0)) for key,source in [('input_tokens','input_tokens'),('output_tokens','output_tokens')]}
        with self.store._transaction() as db:
            row=db.execute('SELECT payload FROM model_requests WHERE research_id=? AND sequence=?',(BUDGET_ID,sequence)).fetchone()
            if not row:return
            item=json.loads(row[0])
            if item.get('usage_recorded'):return
            item.update(**values,usage_recorded=True,usage_returned=known,state=state,finished_at=time.time())
            state=json.loads(db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(BUDGET_ID,)).fetchone()[0])
            for key,value in values.items():state[key]=state.get(key,0)+value
            state['usage_reported_calls']=state.get('usage_reported_calls',0)+int(known)
            db.execute('UPDATE model_requests SET payload=? WHERE research_id=? AND sequence=?',(json.dumps(item),BUDGET_ID,sequence))
            db.execute('UPDATE request_budgets SET payload=? WHERE research_id=?',(json.dumps(state),BUDGET_ID))

    def _stop(self,db,reason):
        db.execute('INSERT OR REPLACE INTO runtime_stop VALUES (1,?)',(json.dumps({**reason,'recorded_at':time.time()}),))

    def note_stop(self,kind,message):
        with self.store._transaction() as db:self._stop(db,{'kind':kind,'message':message})

    def stop_reason(self):
        with closing(self.store._connect()) as db:row=db.execute('SELECT payload FROM runtime_stop WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def guard(self,key):
        with closing(self.store._connect()) as db:row=db.execute('SELECT payload FROM failure_guards WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else {'count':0}

    def check_guard(self,key):
        guard=self.guard(key)
        if guard.get('blocked') or guard['count']>=3:
            self.note_stop(guard.get('kind') if guard.get('blocked') else 'repeated_failure',guard.get('message') or '同一操作重复失败，已保存状态')
            raise RepeatedOperationBlocked(guard.get('message') or '同一操作重复失败，已停止无效重试；请检查输入或模型连接后恢复')

    def failed(self,key,signature,kind,message,*,terminal=False):
        with self.store._transaction() as db:
            row=db.execute('SELECT payload FROM failure_guards WHERE id=?',(key,)).fetchone();old=json.loads(row[0]) if row else {}
            errors=old.get('errors',{})
            if not errors and old.get('signature'):errors[old['signature']]=old.get('count',0)
            errors[signature]=errors.get(signature,0)+1
            count=max(errors.values())
            value={'errors':errors,'signature':signature,'count':count,'blocked':terminal,'kind':kind,'message':message,'at':time.time()}
            db.execute('INSERT OR REPLACE INTO failure_guards VALUES (?,?)',(key,json.dumps(value)))
            self._stop(db,{'kind':kind if terminal or count<3 else 'repeated_failure','message':message,'operation':key})
        return count

    def succeeded(self,key):
        with self.store._transaction() as db:
            db.execute('DELETE FROM failure_guards WHERE id=?',(key,))
            reason=db.execute('SELECT payload FROM runtime_stop WHERE id=1').fetchone()
            if reason and json.loads(reason[0]).get('operation')==key:db.execute('DELETE FROM runtime_stop WHERE id=1')

    def requests(self):
        with closing(self.store._connect()) as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM model_requests WHERE research_id=? ORDER BY sequence',(self.store.research_id,))]


@contextmanager
def budget_scope(root,research_id=None):
    if research_id is None:
        path=root/'.system/graph_thread.json'
        research_id=json.loads(path.read_text()).get('thread_id') if path.exists() else None
    runtime=ResearchRuntime(root,research_id or 'preparation');token=ACTIVE_BUDGET.set(runtime)
    try:yield runtime
    finally:ACTIVE_BUDGET.reset(token)


def request_hook(request):
    runtime=ACTIVE_BUDGET.get()
    if runtime is not None:
        active=ACTIVE_MODEL_REQUEST.get()
        sequence=active[1] if active and active[0] is runtime else runtime.reserve(request.url.host)
        request.extensions['paperagent_budget']=(runtime,sequence)
        # Only explicit/legacy project overrides replace the model connection's timeout.
        timeout=runtime.policy().get('timeout_seconds')
        if timeout is not None:request.extensions['timeout']={k:timeout for k in ('connect','read','write','pool')}


def response_hook(response):
    reference=response.request.extensions.get('paperagent_budget')
    if reference:reference[0].response(reference[1],response.status_code)


async def async_request_hook(request):request_hook(request)
async def async_response_hook(response):response_hook(response)


def decision_error_summary(error):
    reason=str(error)
    if any(text in reason for text in ('原题要求','要求绑定','要求必须','要求引用','新增要求')):
        return '实验条件未能正确关联原题要求'
    if '证据' in reason or '引用' in reason:return '证据引用未通过核对'
    if '截断' in reason or '结构化输出' in reason:return '模型返回的操作信息不完整或未通过校验'
    return '操作信息未通过校验'


def execution_reason(root,status):
    """Read-only projection for recent work and snapshots; never starts/migrates a run."""
    if status=='waiting':return {'kind':'waiting_confirmation','message':'等待补充必要研究信息'}
    if status not in {'paused','failed','cancelled'}:return None
    import sqlite3
    path=root/'.system/research.sqlite'
    if not path.exists():return None
    try:
        preparation=None
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT payload FROM runtime_stop WHERE id=1').fetchone()
            thread=root/'.system/graph_thread.json'
            if thread.is_file():
                research_id=json.loads(thread.read_text()).get('thread_id')
                latest=db.execute('SELECT payload FROM research WHERE id=?',(research_id,)).fetchone()
                if latest:
                    action=json.loads(latest[0]).get('action') or {}
                    if action.get('status')=='failed':
                        return {'kind':action.get('error_type','action_failed'),
                                'message':action.get('message') or action.get('error') or action['reason']}
        reason=json.loads(row[0]) if row else None
        if reason and reason.get('kind')=='invalid_response':
            reason={**reason,'message':f'下一步操作准备失败：{decision_error_summary(preparation) if preparation else "模型返回的信息未通过校验"}。可继续重试。'}
        return reason if reason and reason.get('kind') not in {'running','completed'} else None
    except (sqlite3.Error,ValueError):return None
