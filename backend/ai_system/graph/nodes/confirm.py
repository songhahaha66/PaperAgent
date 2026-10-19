"""
阶段 3：建模方案确认（可选的人工介入点）

开启 confirm_models 时，图在这里 interrupt，把各子问题选中的方案发给用户；
用户回复"确认/继续"等则进入求解，否则把回复当作修改意见重新设计方案。
状态已经 checkpoint 到 sqlite，用户隔多久回复都能接着跑。
"""

import re

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from ..context import GraphContext
from ..state import PaperState

APPROVE_PATTERN = re.compile(r"^\s*(ok|okay|yes|y|go|lgtm|确认|同意|继续|可以|没问题|开始|好|好的|行)[\s。.!！]*$", re.IGNORECASE)


def is_approval(reply: str) -> bool:
    return not reply.strip() or bool(APPROVE_PATTERN.match(reply))


def designs_summary(state: PaperState, ctx=None) -> str:
    lines = ["请确认各子问题的建模方案（回复「确认」开始求解，或直接写修改意见）：", ""]
    for sp in state["analysis"]["subproblems"]:
        design = state["designs"].get(sp["id"])
        if not design:
            continue
        from .explore import two_candidate_mode
        paired=ctx is not None and two_candidate_mode(ctx,{'subproblem':sp})
        choices=design.get('candidates',[]) if paired else [design['chosen']]
        lines.append(f"**{sp['id']} {sp['title']}**")
        if paired:
            lines.append('确认后两项方案使用共同冻结划分实际比较；胜者由项目计算的指标决定，当前尚无实测胜者。')
        for chosen in choices:
            lines.append(f"- **{chosen['name']}**：{chosen['idea']}")
            lines.append(f"  检验：{chosen['validation']}")
        lines.append("")
    return "\n".join(lines)


async def confirm(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("design", "completed")
    rounds = state.get("design_round", 0)
    if not ctx.confirm_models or rounds >= ctx.max_design_rounds:
        await ctx.set_stage("confirm", "completed", "自动通过")
        return {"approved": True}

    await ctx.set_stage("confirm", "in_progress", "等待用户确认")
    reply = interrupt({"type": "confirm_models", "message": designs_summary(state,ctx)})
    reply = str(reply or "")
    if is_approval(reply):
        await ctx.set_stage("confirm", "completed", "用户已确认")
        return {"approved": True, "feedback": ""}
    await ctx.set_stage("confirm", "in_progress", "按用户意见重新设计")
    return {"approved": False, "feedback": reply, "design_round": rounds + 1}
