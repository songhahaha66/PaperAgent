"""Budgeted, project-measured supervised two-candidate exploration."""
import asyncio
import re
from dataclasses import dataclass,field
from typing import Any,Dict,List,Optional
from langchain_core.messages import HumanMessage
from ..context import GraphContext
from ..llm_utils import as_messages,call_structured
from ..state import ValidationProtocol
from ..registry import GroundingError,_parse_number
from .review import leaked

HYPOTHESIS_TASK=re.compile(r"检验|假说|是否.{0,8}(随机|存在|显著|有关)")
PREDICTIVE_TASK=re.compile(r"预测|分类|识别|回归|拟合|估计|predict|classif|regression",re.I)


def is_hypothesis_test(subproblem):
    text=f"{subproblem.get('title','')} {subproblem.get('question','')}"
    return bool(HYPOTHESIS_TASK.search(text)) and not PREDICTIVE_TASK.search(text)


def two_candidate_mode(ctx,branch):
    if ctx.explore_top_k<2 or is_hypothesis_test(branch['subproblem']):return False
    from .supervised_probe import csv_files,root_store
    from ...research.comparison import ComparisonStore
    return bool(csv_files(ctx.workspace)) and (bool(PREDICTIVE_TASK.search(branch['subproblem']['question'])) or bool(ComparisonStore(root_store(ctx)).policy().get('spec')))


@dataclass
class Probe:
    candidate_index:int
    candidate:Dict[str,Any]
    kernel_key:str
    prior:Optional[float]=None
    report:Optional[Dict]=None
    metric:Optional[float]=None
    grounding_error:str=''
    error:str=''
    messages:List=field(default_factory=list)
    run_log:List=field(default_factory=list)
    project_measured:bool=False
    @property
    def works(self):return bool(self.project_measured and self.report and self.report.get('works'))
    def to_record(self):return self.report or {}


@dataclass
class Exploration:
    chosen_index:Optional[int]
    reason:str
    protocol:Dict
    probes:List[Probe]
    winner:Optional[Probe]
    record:Dict=field(default_factory=dict)
    def to_record(self):return self.record


def shortlist(design,top_k):
    candidates=design.get('candidates') or [];scores=design.get('scores') or [{} for _ in candidates]
    chosen=design.get('chosen_index',0)
    return sorted(range(len(candidates)),key=lambda i:(i!=chosen,-(scores[i].get('total') if scores[i].get('total') is not None else -1),i))[:top_k]


def select_probe(probes,protocol):
    measured=[p for p in probes if p.works and p.metric is not None] if protocol.get('applicable') else []
    if not measured:return None,'没有项目重算指标，不沿用自评宣布胜者'
    best=min(measured,key=lambda p:p.candidate_index)
    for p in sorted(measured,key=lambda p:p.candidate_index):
        if abs(p.metric-best.metric)<=protocol.get('tie_tolerance',1e-6):continue
        if (p.metric>best.metric)==protocol.get('higher_is_better',True):best=p
    return best,'按项目重算指标和冻结顺序选择，未使用 critic 分数'


async def make_protocol(ctx,branch,candidates):
    """Legacy descriptive protocol helper; never authorizes an experiment."""
    prompt=f"原题：{branch['source']}\n子问题：{branch['subproblem']['question']}\n数据概览：{branch['data_profile']}"
    result=await call_structured(ctx.planner_llm,ValidationProtocol,as_messages('提供文字验证建议；它不等于可执行的冻结协议。',prompt))
    return result.model_dump()


def shuffle_check(ctx: GraphContext, report: Dict[str, Any], protocol: Dict[str, Any]) -> str:
    """探索指标的标签置换对照；不通过时返回原因，指标不参与比较"""
    shuffled, baseline = report.get("shuffled_value", ""), report.get("baseline_value", "")
    if not shuffled:
        return "没有做标签置换对照"
    try:
        ctx.registry.verify(shuffled, report["metric_run_id"])
    except GroundingError as exc:
        return f"置换对照数值无法核对：{exc}"
    real, shuffled_num, chance = _parse_number(report["metric_value"]), _parse_number(shuffled), _parse_number(baseline)
    if None not in (real, shuffled_num, chance) and leaked(real, shuffled_num, chance, protocol.get("higher_is_better", True)):
        return f"打乱标签后指标仍为 {shuffled}（基线 {baseline}），判为数据泄漏"
    return ""



async def explore(ctx,branch):
    if not two_candidate_mode(ctx,branch):return None
    from .supervised_probe import compare,current,root_store
    from ...research.comparison import ComparisonStore
    record=await compare(ctx,branch)
    if record['status']=='not_applicable':return None
    probes=[];winner=None
    for row in record['branches']:
        candidates=branch['design']['candidates']
        p=Probe(candidate_index=row['index'],candidate=candidates[row['index']] if row['index']<len(candidates) else {'name':row['name']},
            kernel_key=row.get('kernel_key',f"{branch['subproblem']['id']}~comparison{row['index']}"),prior=row.get('prior'),
            report={**row,'works':row['status']=='valid'},metric=row.get('metric'),project_measured=True,
            run_log=[{'run_id':rid,'ok':row['status']=='valid','code':row.get('driver_code','')} for rid in row.get('run_ids',[])])
        probes.append(p)
        if row['index']==record.get('chosen_index'):winner=p
    try:
        if winner is not None:
            if not current(ctx,record,branch):
                record.update(status='input_changed',chosen_index=None,reason='晋升前输入已改变');ComparisonStore(root_store(ctx)).save(record);winner=None
            elif winner.kernel_key in getattr(ctx.kernels,'_kernels',{}):
                ctx.kernels.promote(winner.kernel_key,branch['subproblem']['id'])
                record['kernel_promoted']=True
    finally:
        await asyncio.gather(*(ctx.kernels.release(p.kernel_key) for p in probes))
    await ctx.events.emit('paper_explore',{'id':record['id'],'status':record['status'],'reason':record['reason']})
    return Exploration(record.get('chosen_index'),record['reason'],record.get('protocol',{}),probes,winner,record)


def promotion_message(exploration):
    row=exploration.winner.report
    return (f"比较状态：{exploration.record['status']}。{exploration.reason}。"
        '继续冻结的候选实现；有晋升内核时 comparison_estimator 是真实训练标签拟合的模型。恢复时原内核可能不存在，需按冻结代码重新建立模型。'
        '比较预测不是已登记的本研究结果，正式求解仍须独立检查、print 并 record_results/record_figure，最后提交 SolveReport。'
        f"\n冻结代码 SHA256：{row.get('code_hash')}\n{row.get('code','')}")
