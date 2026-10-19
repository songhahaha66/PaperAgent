"""Per-study durable request cap and unified entry preferences; no credentials stored."""
import json
import time
from contextlib import closing,contextmanager
from contextvars import ContextVar
from .store import ResearchStore

ACTIVE_BUDGET=ContextVar('paperagent_request_budget',default=None)
BUDGET_ID='workspace-model-budget'


class ResearchBudgetExhausted(RuntimeError):pass


class ResearchRuntime:
    def __init__(self,root,research_id='workspace-runtime'):
        self.active_research_id=research_id
        self.store=ResearchStore(root,BUDGET_ID)
        with closing(self.store._connect()) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS runtime_policy(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS request_budgets(research_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_requests(research_id TEXT NOT NULL,sequence INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(research_id,sequence));''')

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
        with closing(self.store._connect()) as db:r=db.execute('SELECT payload FROM runtime_policy WHERE id=1').fetchone()
        return json.loads(r[0]) if r else {'request_limit':40,'timeout_seconds':90,'compact_paper':False,'confirm_models':False}

    def configure(self,settings):
        with self.store._transaction() as db:db.execute('INSERT OR REPLACE INTO runtime_policy VALUES (1,?)',(json.dumps(settings),))

    def status(self,research_id=None):
        rid=BUDGET_ID
        with closing(self.store._connect()) as db:r=db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(rid,)).fetchone()
        return json.loads(r[0]) if r else {**self.policy(),'used':0,'state':'ready'}

    def reserve(self,host):
        from ..graph.tracing import CURRENT_LABEL
        rid=self.store.research_id
        with self.store._transaction() as db:
            r=db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(rid,)).fetchone()
            state=json.loads(r[0]) if r else {**self.policy(),'used':0,'state':'ready'}
            if state['state']=='provider_blocked':raise ResearchBudgetExhausted('网关授权或额度已拒绝，请求已停止；检查点和预算保留')
            if state['used']>=state['request_limit']:
                raise ResearchBudgetExhausted(f"研究模型请求预算耗尽（{state['used']}/{state['request_limit']}），保留检查点，恢复不会清零")
            state['used']+=1;state['state']='active' if state['used']<state['request_limit'] else 'budget_exhausted'
            seq=state['used'];row={'sequence':seq,'sent_at':time.time(),'host':host,'label':CURRENT_LABEL.get(),'research_id':self.active_research_id,'state':'sent'}
            db.execute('INSERT OR REPLACE INTO request_budgets VALUES (?,?)',(rid,json.dumps(state)))
            db.execute('INSERT INTO model_requests VALUES (?,?,?)',(rid,seq,json.dumps(row)))
        return seq

    def response(self,sequence,status):
        with self.store._transaction() as db:
            r=db.execute('SELECT payload FROM model_requests WHERE research_id=? AND sequence=?',(self.store.research_id,sequence)).fetchone()
            if r:
                row=json.loads(r[0]);row.update(http_status=status,headers_at=time.time(),state='http_response')
                db.execute('UPDATE model_requests SET payload=? WHERE research_id=? AND sequence=?',(json.dumps(row),self.store.research_id,sequence))
                if status in {401,403,429}:
                    state=json.loads(db.execute('SELECT payload FROM request_budgets WHERE research_id=?',(self.store.research_id,)).fetchone()[0]);state['state']='provider_blocked'
                    db.execute('UPDATE request_budgets SET payload=? WHERE research_id=?',(json.dumps(state),self.store.research_id))

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
        request.extensions['paperagent_budget']=(runtime,runtime.reserve(request.url.host))
        # Applies before any network send, including SDK-internal retry attempts.
        request.extensions['timeout']={k:runtime.status()['timeout_seconds'] for k in ('connect','read','write','pool')}


def response_hook(response):
    reference=response.request.extensions.get('paperagent_budget')
    if reference:reference[0].response(reference[1],response.status_code)


async def async_request_hook(request):request_hook(request)
async def async_response_hook(response):response_hook(response)
