"""
执行驱动的方案搜索（求解的第一步）

design 阶段的 critic 只能在动手前判断方案"看起来"好不好。2024_C 里看起来合理的 Q3 方案，
跑出来验证集 AUC 只有 0.616，而这时已经没有回头的机会。这里借鉴 AIDE / MLE-bench 的做法，
让排名靠前的几个方案先在真实数据上跑一小段，用执行结果来定：

1. 为子问题定一个统一的验证协议（同一个指标、同一种划分、同一个基线），各候选按同一协议汇报，结果才可比；
2. design 评分最高的 k 个候选各开一个内核，并行跑一个短的探索循环，只实现核心模型并按协议给出指标；
3. 指标必须出现在探索时某次成功执行的输出里（与结果登记同一套核对），编造的指标不参与比较；
4. 有可比指标时按指标选，差距在容差内用 design 评分打破平局；没有统一指标的子问题（优化、评价、描述类），
   在跑通的候选里按 critic 对执行证据的评分选；
5. 胜出分支的内核和对话历史整体晋升为正式求解，探索阶段的计算不浪费；其余内核释放。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from ...judge import Score
from ..compaction import CompactHistoryMiddleware, StepBudgetMiddleware
from ..context import GraphContext
from ..llm_utils import as_messages, call_structured
from ..registry import GroundingError, _parse_number
from ..state import BranchInput, ProbeReport, ValidationProtocol
from .review import leaked

logger = logging.getLogger(__name__)

# 两个指标相对差距在 1% 以内视为打平
TIE_TOLERANCE = 0.01

PROTOCOL_SYSTEM = (
    "你是数学建模竞赛的指导老师。几个候选建模方案要先在数据上各跑一小段再决定用哪个，"
    "请为这个子问题定一个所有方案共用的验证协议，使它们的结果可以直接比较。"
    "协议要防止数据泄漏（例如按比赛或按时间划分，而不是随机打乱逐行划分），并给出一个简单的对照基线。"
    "指标只能是一个数（例如 AUC），不能是“检验力与第一类错误率”这样的组合；需要兼顾多个量时，定义一个合成的单一分数。"
    "如果这个子问题本质上没有可比的数值指标（如优化方案、综合评价、描述性分析、写备忘录），applicable 设为 false。"
)

PROBE_SYSTEM = """你是数学建模竞赛的编程手，现在处于方案探索阶段：几个候选方案各自在数据上先跑一小段，
按同一个验证协议比较，胜出的方案再完整求解。

- 用 run_python 执行代码。内核是持久的，前面定义的变量后面都能直接用。
- 只实现方案的核心模型，不做灵敏度分析，不画论文用图，步数有限（约 {steps} 步）。
- 严格按验证协议计算指标和基线，并把它们 print 出来；协议无法套用时如实说明。
- 不要为了指标好看而泄漏数据或只挑一部分数据。
- 同一次执行里再做一次标签置换对照：构造特征之前把目标列随机打乱、其余完全不变，算出同一指标并 print；
  打乱后仍明显好于基线的指标会被判为泄漏，不参与比较。
- 数据文件路径相对于工作目录，例如 data/xxx.csv。

完成后提交 ProbeReport：指标值必须是某次执行 print 出的数，metric_run_id 填那次执行的 run_id。"""

PROBE_NUDGE = "探索步数有限，请现在调用 ProbeReport 提交探索结果（没跑通就如实写 works=false）。"

EVIDENCE = Score(
    instructions="根据探索阶段的执行结果，这个方案在真实数据上的表现是否可靠、是否值得完整求解？",
    criteria=[
        "没有跑通或结果明显不合理",
        "勉强跑通，问题较多",
        "跑通，结果基本合理",
        "跑通，结果合理且有检验",
        "跑通，结果合理、检验充分，明显优于直观基线",
    ],
)


@dataclass
class Probe:
    candidate_index: int
    candidate: Dict[str, Any]
    kernel_key: str
    prior: Optional[float]
    report: Optional[Dict[str, Any]] = None
    metric: Optional[float] = None  # 只有核对通过的指标才记数值
    grounding_error: str = ""
    evidence: Optional[float] = None
    error: str = ""
    messages: List[BaseMessage] = field(default_factory=list)
    run_log: List[Dict] = field(default_factory=list)

    @property
    def works(self) -> bool:
        return bool(self.report and self.report.get("works"))

    def to_record(self) -> Dict[str, Any]:
        report = self.report or {}
        return {
            "candidate_index": self.candidate_index,
            "name": self.candidate["name"],
            "prior": self.prior,
            "works": self.works,
            "metric_value": report.get("metric_value", ""),
            "metric_grounded": self.metric is not None,
            "grounding_error": self.grounding_error,
            "baseline_value": report.get("baseline_value", ""),
            "evidence": self.evidence,
            "summary": report.get("summary", ""),
            "problems": report.get("problems", []),
            "error": self.error,
            "runs": len(self.run_log),
        }


@dataclass
class Exploration:
    chosen_index: int
    reason: str
    protocol: Dict[str, Any]
    probes: List[Probe]
    winner: Probe

    def to_record(self) -> Dict[str, Any]:
        return {
            "protocol": self.protocol,
            "probes": [p.to_record() for p in self.probes],
            "chosen_index": self.chosen_index,
            "reason": self.reason,
        }


def shortlist(design: Dict[str, Any], top_k: int) -> List[int]:
    """design 评分最高的 k 个候选；没有评分时保持生成顺序，原选中的候选总在第一位"""
    candidates = design.get("candidates") or []
    scores = design.get("scores") or [{} for _ in candidates]
    chosen = design.get("chosen_index", 0)
    order = sorted(
        range(len(candidates)),
        key=lambda i: (i != chosen, -(scores[i].get("total") if scores[i].get("total") is not None else -1), i),
    )
    return order[:top_k]


def _better(a: float, b: float, higher_is_better: bool) -> bool:
    return a > b if higher_is_better else a < b


def _tied(a: float, b: float) -> bool:
    return abs(a - b) <= TIE_TOLERANCE * max(abs(a), abs(b), 1e-12)


def _prior(probe: Probe) -> float:
    return probe.prior if probe.prior is not None else -1.0


def select_probe(probes: List[Probe], protocol: Dict[str, Any]) -> Tuple[Probe, str]:
    """按执行结果选出胜出的分支，返回 (分支, 理由)"""
    runnable = [p for p in probes if p.works]
    if not runnable:
        best = max(probes, key=_prior)
        return best, "探索阶段没有方案跑通，沿用方案评审的选择"

    measured = [p for p in runnable if p.metric is not None] if protocol.get("applicable") else []
    if measured:
        higher = protocol.get("higher_is_better", True)
        best = measured[0]
        for probe in measured[1:]:
            if _tied(probe.metric, best.metric):
                if _prior(probe) > _prior(best):
                    best = probe
            elif _better(probe.metric, best.metric, higher):
                best = probe
        others = "，".join(f"「{p.candidate['name']}」{p.report['metric_value']}" for p in measured if p is not best)
        reason = f"按{protocol.get('metric') or '验证指标'}选择：{best.report['metric_value']}"
        return best, reason + (f"（对比 {others}）" if others else "")

    best = max(runnable, key=lambda p: (p.evidence if p.evidence is not None else -1.0, _prior(p)))
    if len(runnable) == 1:
        return best, "只有这个方案在数据上跑通"
    if best.evidence is not None:
        return best, f"没有统一指标，按执行证据评分选择（{best.evidence:.1f}/4）"
    return best, "没有统一指标，在跑通的方案中沿用方案评审的排序"


def _subproblem_text(subproblem: Dict[str, Any]) -> str:
    deliverables = "；".join(subproblem.get("deliverables") or []) or "（未列出）"
    return f"{subproblem['id']} {subproblem['title']}\n问题：{subproblem['question']}\n交付物：{deliverables}"


async def make_protocol(ctx: GraphContext, branch: BranchInput, candidates: List[Dict]) -> Dict[str, Any]:
    names = "\n".join(f"- {c['name']}：{c['idea']}" for c in candidates)
    prompt = (
        f"# 子问题\n{_subproblem_text(branch['subproblem'])}\n\n"
        f"# 数据概览\n{branch['data_profile'][:6000]}\n\n"
        f"# 待比较的候选方案\n{names}"
    )
    protocol = await call_structured(ctx.planner_llm, ValidationProtocol, as_messages(PROTOCOL_SYSTEM, prompt))
    return protocol.model_dump()


def protocol_text(protocol: Dict[str, Any]) -> str:
    if not protocol.get("applicable"):
        return "# 验证协议\n这个子问题没有统一的数值指标。跑通核心模型，给出能说明结果是否合理的检验即可，metric_value 留空。"
    direction = "越大越好" if protocol.get("higher_is_better", True) else "越小越好"
    return (
        f"# 验证协议（所有候选方案相同，必须严格遵守）\n"
        f"- 指标：{protocol.get('metric')}（{direction}）\n"
        f"- 验证做法：{protocol.get('procedure')}\n"
        f"- 对照基线：{protocol.get('baseline')}"
    )


async def run_probe(ctx: GraphContext, branch: BranchInput, probe: Probe, protocol: Dict[str, Any]) -> Probe:
    from .solve import build_task, build_tools, run_agent  # 避免循环导入

    subproblem_id = branch["subproblem"]["id"]
    agent = create_agent(
        model=ctx.coder_llm,
        tools=build_tools(ctx, subproblem_id, probe.run_log, kernel_key=probe.kernel_key, record=False),
        system_prompt=PROBE_SYSTEM.format(steps=ctx.explore_steps),
        response_format=ToolStrategy(ProbeReport),
        middleware=[CompactHistoryMiddleware(), StepBudgetMiddleware(ctx.explore_steps, 3, PROBE_NUDGE)],
    )
    task = build_task(branch, ctx, candidate=probe.candidate, extra=protocol_text(protocol), ending="开始探索。")
    try:
        result = await run_agent(agent, [HumanMessage(content=task)], ctx.explore_steps * 2 + 1, PROBE_NUDGE)
    except GraphRecursionError:
        probe.error = f"超过 {ctx.explore_steps} 步仍未完成探索"
        return probe
    except Exception as exc:
        logger.exception("探索 %s 失败", probe.kernel_key)
        probe.error = str(exc)
        return probe

    structured = result.get("structured_response")
    probe.messages = result["messages"]
    if structured is None:
        probe.error = "没有提交探索报告"
        return probe
    probe.report = structured.model_dump()
    value, run_id = probe.report.get("metric_value", ""), probe.report.get("metric_run_id", "")
    if probe.works and protocol.get("applicable") and value and run_id:
        try:
            ctx.registry.verify(value, run_id)
            probe.metric = _parse_number(value)
            probe.grounding_error = shuffle_check(ctx, probe.report, protocol)
            if probe.grounding_error:
                probe.metric = None
        except GroundingError as exc:
            probe.grounding_error = str(exc)
    return probe


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


async def score_evidence(ctx: GraphContext, branch: BranchInput, probes: List[Probe]) -> None:
    """没有统一指标时，让 critic 对各分支的执行证据打分"""
    runnable = [p for p in probes if p.works]
    if ctx.judge is None or len(runnable) < 2:
        return
    state = {
        "子问题": _subproblem_text(branch["subproblem"]),
        "探索结果": {
            f"方案{i + 1}": {"方案": p.candidate["name"], "思路": p.candidate["idea"], **{
                k: p.report.get(k) for k in ("summary", "problems", "baseline_value")
            }}
            for i, p in enumerate(runnable)
        },
    }
    # 每个分支单独一次请求，所有分支放进一次请求时模型容易给出相同分数
    async def judge_one(i: int) -> float:
        question = Score(instructions=f"只评价「方案{i + 1}」。{EVIDENCE.instructions}", criteria=EVIDENCE.criteria)
        answers = await ctx.judge.ask(state, {"evidence": question})
        return answers.scores["evidence"].score

    try:
        scores = await asyncio.gather(*(judge_one(i) for i in range(len(runnable))))
    except Exception as exc:  # 打分失败就退回 design 评分
        logger.warning("执行证据评分失败: %s", exc)
        return
    for probe, score in zip(runnable, scores):
        probe.evidence = score


async def explore(ctx: GraphContext, branch: BranchInput) -> Optional[Exploration]:
    """跑探索并把胜出分支的内核晋升到子问题名下；候选不足时返回 None（直接按原方案求解）"""
    design = branch["design"]
    subproblem_id = branch["subproblem"]["id"]
    picks = shortlist(design, ctx.explore_top_k)
    if len(picks) < 2:
        return None

    candidates = design["candidates"]
    scores = design.get("scores") or [{} for _ in candidates]
    priors = [scores[i].get("total") for i in picks]
    # 评审已经明显分出高下时，试跑带来的信息不值它的成本
    if None not in priors[:2] and priors[0] - priors[1] > ctx.explore_margin:
        return None
    protocol = await make_protocol(ctx, branch, [candidates[i] for i in picks])
    # 没有统一指标时各分支无法直接比较，只能再请 critic 读执行证据，收益小于成本，直接按评审选择求解
    if not protocol.get("applicable"):
        return None
    await ctx.events.stage(f"{subproblem_id}：{len(picks)} 个候选方案先在数据上试跑")
    probes = [
        Probe(candidate_index=i, candidate=candidates[i], kernel_key=f"{subproblem_id}~c{i}", prior=scores[i].get("total"))
        for i in picks
    ]
    try:
        await asyncio.gather(*(run_probe(ctx, branch, p, protocol) for p in probes))
        if not protocol.get("applicable") or not any(p.metric is not None for p in probes):
            await score_evidence(ctx, branch, probes)
        winner, reason = select_probe(probes, protocol)
        ctx.kernels.promote(winner.kernel_key, subproblem_id)
    finally:
        await asyncio.gather(*(ctx.kernels.release(p.kernel_key) for p in probes))

    await ctx.events.emit("paper_explore", f"{subproblem_id} 选用「{winner.candidate['name']}」：{reason}")
    return Exploration(
        chosen_index=winner.candidate_index, reason=reason, protocol=protocol, probes=probes, winner=winner
    )


def promotion_message(exploration: Exploration) -> str:
    return (
        f"你的方案「{exploration.winner.candidate['name']}」在探索阶段胜出（{exploration.reason}）。"
        "现在进入正式求解：内核中的变量都还在，在此基础上完成完整求解，补齐全部交付物、检验和灵敏度分析，"
        "用 record_results / record_figure 登记论文要用的结果和图（探索阶段算出的数字也要重新 print 并登记），"
        "最后提交 SolveReport，报告里的数字一律写成登记返回的引用。"
    )
