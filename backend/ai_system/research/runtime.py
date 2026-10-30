"""Durable request usage, explicit/legacy limits and operation failure guards."""
from services.file_services.workspace_layout import workspace_path
import json
import time
import asyncio
import logging
import math
from contextlib import closing,contextmanager
from contextvars import ContextVar
from .store import ResearchStore

ACTIVE_BUDGET=ContextVar('paperagent_request_budget',default=None)
BUDGET_ID='workspace-model-budget'


class ResearchBudgetExhausted(RuntimeError):pass
class RepeatedOperationBlocked(RuntimeError):pass
class ModelServiceError(RuntimeError):pass
class BudgetUnavailable(ResearchBudgetExhausted):pass

ACTIVE_MODEL_REQUEST=ContextVar('paperagent_model_request',default=None)
CURRENT_EXECUTION=ContextVar('paperagent_execution',default={})
LINK_FIELDS=('action_id','observation_id','question_id','experiment_id','parent_call_id','step','purpose','subject','context_usage')


def execution_links():
    current=CURRENT_EXECUTION.get()
    return {key:current[key] for key in LINK_FIELDS if key in current}


@contextmanager
def execution_scope(**values):
    current={**CURRENT_EXECUTION.get(),**values}
    if values.get('events') is not None:
        try:current['event_loop']=asyncio.get_running_loop()
        except RuntimeError:pass
    token=CURRENT_EXECUTION.set(current)
    try:yield
    finally:CURRENT_EXECUTION.reset(token)


def notify_runtime():
    """Notify only after durable writes. Tokens never trigger snapshot pushes."""
    current=CURRENT_EXECUTION.get()
    events=current.get('events');loop=current.get('event_loop')
    if events is None or loop is None or loop.is_closed():return
    payload={'research_id':current.get('research_id'),'recorded_at':time.time()}
    def send():
        async def emit():
            try:await events.emit('research_activity',payload)
            except Exception:logging.getLogger(__name__).warning('执行状态通知失败',exc_info=True)
        loop.create_task(emit())
    try:loop.call_soon_threadsafe(send)
    except RuntimeError:pass  # A stopped notification loop must not fail a saved operation.


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
        for key in ('output_token_limit','total_token_limit','cost_limit_usd'):
            value=settings.get(key)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0):
                raise ValueError('资源额度必须为正数：'+key)
            if value is not None and key.endswith('token_limit') and type(value) is not int:raise ValueError('token额度必须为整数')
        policy={'policy_version':3,'preference_source':'explicit','request_limit':limit,'timeout_seconds':settings.get('timeout_seconds'),
                'limit_source':'explicit' if limit is not None else 'default',
                'timeout_source':'explicit' if settings.get('timeout_seconds') is not None else 'model_connection',
                'compact_paper':settings.get('compact_paper', False),
                **{k:settings.get(k) for k in ('output_token_limit','total_token_limit','cost_limit_usd')}}
        with self.store._transaction() as db:
            # Old unmeasured requests have no upper-bound reservation to reuse.
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM model_requests')]
            if any(policy.get(k) is not None for k in ('output_token_limit','total_token_limit')) and any(not r.get('usage_returned') and not r.get('reservation') for r in rows):
                raise BudgetUnavailable('历史请求用量未知且没有预留记录，不能宣称已覆盖其token消耗')
            if policy.get('cost_limit_usd') is not None and any(r.get('cost_usd') is None and (r.get('reservation') or {}).get('cost_usd') is None for r in rows):
                raise BudgetUnavailable('历史请求费用未知，不能启用覆盖整个项目的费用上限')
            db.execute('INSERT OR REPLACE INTO runtime_policy VALUES (1,?)',(json.dumps(policy),))

    def status(self,research_id=None):
        policy=self.policy()
        with closing(self.store._connect()) as db:r=db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(BUDGET_ID,)).fetchone()
        state={**(json.loads(r[0]) if r else {'used':0,'state':'ready','metering_version':3}),**policy}
        state.setdefault('input_tokens',0);state.setdefault('output_tokens',0);state.setdefault('usage_reported_calls',0)
        state['stop_reason']=self.stop_reason()
        return state

    def remaining(self):
        state=self.status();limit=state.get('request_limit')
        return None if limit is None else max(0,limit-state['used'])

    def reserve(self,host,**reservation):
        try:return self._reserve(host,**reservation)
        except ResearchBudgetExhausted as exc:
            self.note_stop('resource_limit',str(exc))
            state=self.status()
            if state.get('request_limit') is not None and state['used']>=state['request_limit']:
                self.note_stop('legacy_limit' if state.get('limit_source')=='legacy' else 'explicit_limit',f"已达到请求上限（{state['used']}/{state['request_limit']}），计数和已完成结果保留")
            raise

    def _reserve(self,host,*,input_tokens=0,output_tokens=0,prices=None):
        from ..graph.tracing import CURRENT_LABEL
        policy=self.policy()
        with self.store._transaction() as db:
            r=db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(BUDGET_ID,)).fetchone()
            state={**(json.loads(r[0]) if r else {'used':0,'state':'ready','metering_version':3}),**policy}
            limit=state.get('request_limit')
            if limit is not None and state['used']>=limit:
                reason={'kind':'explicit_limit' if state.get('limit_source')!='legacy' else 'legacy_limit',
                        'message':f"已达到请求上限（{state['used']}/{limit}），计数和已完成结果保留"}
                self._stop(db,reason)
                raise ResearchBudgetExhausted(reason['message'])
            wanted=max(0,int(output_tokens));input_tokens=max(0,int(input_tokens));allowed=wanted
            if policy.get('output_token_limit') is not None:
                allowed=min(allowed,max(0,int(policy['output_token_limit']-state.get('output_tokens',0)-state.get('reserved_output_tokens',0))))
            if policy.get('total_token_limit') is not None:
                allowed=min(allowed,max(0,int(policy['total_token_limit']-state.get('input_tokens',0)-state.get('output_tokens',0)-state.get('reserved_input_tokens',0)-state.get('reserved_output_tokens',0)-input_tokens)))
            if wanted and allowed<=0:raise ResearchBudgetExhausted('项目token额度耗尽，已有成果保留')
            exposure=None
            if prices:
                exposure=(input_tokens*max(prices['input_per_million'],prices.get('cached_input_per_million') or 0)+allowed*prices['output_per_million'])/1000000
            if policy.get('cost_limit_usd') is not None:
                if exposure is None:raise BudgetUnavailable('费用额度需要已确认的连接价格；当前价格未知，未发起请求')
                available=policy['cost_limit_usd']-state.get('cost_usd',0)-state.get('reserved_cost_usd',0)
                input_cost=input_tokens*max(prices['input_per_million'],prices.get('cached_input_per_million') or 0)/1000000
                if available<input_cost:raise ResearchBudgetExhausted('项目费用可用额度不足以覆盖输入，已有成果保留')
                if prices['output_per_million']:
                    allowed=min(allowed,max(0,int((available-input_cost)*1000000/prices['output_per_million'])))
                if wanted and allowed<=0:raise ResearchBudgetExhausted('项目费用额度耗尽，已有成果保留')
                exposure=input_cost+allowed*prices['output_per_million']/1000000
            state['reserved_input_tokens']=state.get('reserved_input_tokens',0)+input_tokens
            state['reserved_output_tokens']=state.get('reserved_output_tokens',0)+allowed
            state['reserved_cost_usd']=state.get('reserved_cost_usd',0)+(exposure or 0)
            state['used']+=1;state['state']='budget_exhausted' if limit is not None and state['used']>=limit else 'active'
            seq=state['used'];row={'sequence':seq,'sent_at':time.time(),'host':host,'label':CURRENT_LABEL.get(),'research_id':self.active_research_id,'state':'sent',
                                  **execution_links(),'reservation':{'input_tokens':input_tokens,'output_tokens':allowed,'cost_usd':exposure},
                                  'generation_limit':allowed or None,'prices':prices}
            db.execute('INSERT OR REPLACE INTO request_budgets VALUES (?,?)',(BUDGET_ID,json.dumps(state)))
            db.execute('INSERT INTO model_requests VALUES (?,?,?)',(BUDGET_ID,seq,json.dumps(row)))
        notify_runtime()
        return seq

    def response(self,sequence,status):
        with self.store._transaction() as db:
            r=db.execute('SELECT payload FROM model_requests WHERE research_id=? AND sequence=?',(BUDGET_ID,sequence)).fetchone()
            if r:
                row=json.loads(r[0]);row.update(http_status=status,headers_at=time.time(),state='http_response')
                db.execute('UPDATE model_requests SET payload=? WHERE research_id=? AND sequence=?',(json.dumps(row),BUDGET_ID,sequence))
        notify_runtime()

    def usage(self,sequence,usage=None,*,state="completed",diagnostic=None):
        usage=usage or {};known=bool(usage)
        values={key:max(0,int(usage.get(source) or 0)) for key,source in [('input_tokens','input_tokens'),('output_tokens','output_tokens')]}
        reasoning=(usage.get('output_token_details') or {}).get('reasoning')
        if reasoning is not None:values['reasoning_tokens']=max(0,int(reasoning))
        details = usage.get('input_token_details') or {}
        if details.get('cache_read') is not None:
            values['cache_read_tokens'] = max(0, int(details['cache_read']))
        with self.store._transaction() as db:
            row=db.execute('SELECT payload FROM model_requests WHERE research_id=? AND sequence=?',(BUDGET_ID,sequence)).fetchone()
            if not row:return
            item=json.loads(row[0])
            if item.get('usage_recorded'):return
            if diagnostic:item['diagnostic']=diagnostic
            item.update(**values,usage_recorded=True,usage_returned=known,state=state,finished_at=time.time())
            state=json.loads(db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(BUDGET_ID,)).fetchone()[0])
            reservation=item.get('reservation') or {}
            cost=usage.get('cost_usd')
            if cost is not None and (isinstance(cost,bool) or not isinstance(cost,(int,float)) or not math.isfinite(cost) or cost<0):cost=None
            prices=item.get('prices')
            if known and cost is None and prices and (prices.get('cached_input_per_million') is None or 'cache_read_tokens' in values):
                cached=min(values['input_tokens'],values.get('cache_read_tokens',0))
                cost=((values['input_tokens']-cached)*prices['input_per_million']+cached*(prices.get('cached_input_per_million') if prices.get('cached_input_per_million') is not None else prices['input_per_million'])+values['output_tokens']*prices['output_per_million'])/1000000
            if known and item['state'] in {'completed','truncated'}:
                for name in ('input_tokens','output_tokens'):
                    field='reserved_'+name;state[field]=max(0,state.get(field,0)-reservation.get(name,0))
            else:
                # Incomplete/failed streams may report only a lower bound. Keep
                # the unmeasured remainder reserved rather than treating it as free.
                for name in ('input_tokens','output_tokens'):
                    field='reserved_'+name
                    state[field]=max(0,state.get(field,0)-min(reservation.get(name,0),values[name]))
                item['usage_unknown']=True
                state['unmeasured_requests']=state.get('unmeasured_requests',0)+1
            if cost is not None:
                item['cost_usd']=float(cost);state['cost_usd']=state.get('cost_usd',0)+float(cost)
                state['reserved_cost_usd']=max(0,state.get('reserved_cost_usd',0)-((reservation.get('cost_usd') or 0) if item['state'] in {'completed','truncated'} else min(float(cost),reservation.get('cost_usd') or 0)))
            elif reservation.get('cost_usd') is not None:item['cost_unknown']=True
            for key,value in values.items():state[key]=state.get(key,0)+value
            state['usage_reported_calls']=state.get('usage_reported_calls',0)+int(known)
            db.execute('UPDATE model_requests SET payload=? WHERE research_id=? AND sequence=?',(json.dumps(item),BUDGET_ID,sequence))
            db.execute('UPDATE request_budgets SET payload=? WHERE research_id=?',(json.dumps(state),BUDGET_ID))
        notify_runtime()

    def generation_limit(self,sequence):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM model_requests WHERE research_id=? AND sequence=?',(BUDGET_ID,sequence)).fetchone()
        return json.loads(row[0]).get('generation_limit') if row else None

    def wrap_up(self):
        state=self.status()
        resources=[(state.get('used',0),state.get('request_limit')),
                   (state.get('output_tokens',0)+state.get('reserved_output_tokens',0),state.get('output_token_limit')),
                   (state.get('input_tokens',0)+state.get('output_tokens',0)+state.get('reserved_input_tokens',0)+state.get('reserved_output_tokens',0),state.get('total_token_limit')),
                   (state.get('cost_usd',0)+state.get('reserved_cost_usd',0),state.get('cost_limit_usd'))]
        remaining=state.get('request_limit')
        near_requests=remaining is not None and remaining-state.get('used',0)<=2
        if near_requests or any(limit is not None and used>=limit*.8 for used,limit in resources):
            return '项目资源额度接近上限。优先保存现有代码、结果和正文，说明已完成范围及未完成部分；停止扩大调查或重复复核，完成后直接交付或明确暂停。不要为收尾重跑已完成计算。'
        return None

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
        path=workspace_path(root, '.system/graph_thread.json')
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
    from .review_cache import error_detail
    return error_detail(error)


def action_failure_message(action):
    capability = action['capability']
    if capability.startswith('writing.'):
        stage = {'reconcile':'结论核对','write':'论文撰写','validate':'论文检查',
                 'repair':'论文修订','render':'论文渲染','export':'论文导出'}[capability.split('.')[1]]
        return f'{stage}未完成。实验结果、代码和图表已保存，点击继续将从此步骤重试。'
    return '当前操作执行失败，已有产物和执行记录已保存。可查看执行详情后继续。'


def execution_reason(root,status):
    """Read-only projection for recent work and snapshots; never starts/migrates a run."""
    if status=='waiting':return {'kind':'waiting_confirmation','message':'等待补充必要研究信息'}
    if status not in {'paused','failed','cancelled'}:return None
    import sqlite3
    path=workspace_path(root, '.system/research.sqlite')
    if not path.exists():return None
    try:
        preparation=None
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT payload FROM runtime_stop WHERE id=1').fetchone()
            thread=workspace_path(root, '.system/graph_thread.json')
            if thread.is_file():
                research_id=json.loads(thread.read_text()).get('thread_id')
                latest=db.execute('SELECT payload FROM research WHERE id=?',(research_id,)).fetchone()
                if latest:
                    snapshot=json.loads(latest[0]);action=snapshot.get('action') or {}
                    if action.get('status')=='failed':
                        from .planner import decision_block_message
                        return {'kind':action.get('error_type','action_failed'),
                                'message':decision_block_message(None,snapshot),
                                'detail':action.get('error')}
        reason=json.loads(row[0]) if row else None
        if reason and reason.get('kind')=='invalid_response':
            reason={**reason,'message':f'下一步操作准备失败：{decision_error_summary(preparation) if preparation else "模型返回的信息未通过校验"}。可继续重试。'}
        return reason if reason and reason.get('kind') not in {'running','completed'} else None
    except (sqlite3.Error,ValueError):return None
