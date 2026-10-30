"""Optional review of the question, code, outputs and report; never an acceptance gate."""
from typing import Dict, List
from ai_system.graph.context import GraphContext
from ai_system.graph.llm_utils import as_messages, call_structured, clip
from ai_system.graph.state import BranchInput, ResultReview
from ai_system.graph.tracing import labelled

SYSTEM = "你是数学建模研究的复核者。只根据原题、实际代码、输出和结果文件提出具体疑点。意见供研究者核实，不构成交付门槛，不增加题外实验。"
CHECKLIST = "检查单位、时间对齐、边界约束、数据范围、预测时可用信息、数值与结论是否相符，以及原题目标是否回答。没有展示的内容保留为未知，不臆测错误。"


def review_prompt(branch: BranchInput, report: Dict, ctx: GraphContext, runs: List[Dict]) -> str:
    subproblem = branch["subproblem"]
    code = "\n\n".join(
        f"## {r['run_id']}（{'成功' if r['ok'] else '失败'}）\n```python\n{r['code']}\n```\n"
        + ("执行输出：\n" + clip(ctx.fs.read_text(info['stdout']), 1500) if (info := ctx.fs.get_run_info(r['run_id'])) and info.get('stdout') else "")
        for r in runs
    )
    if len(code) > 48000:
        code = code[:30000] + "\n[中间执行记录因上下文预算省略；不能据未展示的计算确认正确]\n" + code[-18000:]
    return (
        f"# 原题（含题内数据）\n{branch['source']}\n\n"
        f"# 子问题 {subproblem['id']}：{subproblem['title']}\n{subproblem['question']}\n"
        f"交付物：{'；'.join(subproblem.get('deliverables') or [])}\n\n"
        f"# 数据概览\n{clip(branch['data_profile'], 4000)}\n\n"
        f"# 选定方案\n{((branch.get('design') or {}).get('chosen') or {}).get('name',report.get('approach',''))}：{((branch.get('design') or {}).get('chosen') or {}).get('idea','未保留独立方法说明')}\n\n"
        f"# 求解报告\n{report}\n\n"
        f"# 本次执行代码与输出（按时间顺序，含最初建模计算）\n{code or '（无）'}\n\n{CHECKLIST}\n\n"
        f"# 原题要求与建模前提\n{branch.get('research_contract', {})}\n"
        f"# 针对本意见的独立补充实验（只据真实结果判断，不因已执行而通过）\n{branch.get('supplementary_evidence', [])}\n"
        f"# 冻结候选及评价条件\n{branch.get('experiment') or '无额外候选协议'}\n"
        "逐项对照上述候选方法、参数、数据范围、评价条件与实际代码；执行者静默换方法或改变条件属于实质问题。不能把运行错误判成方法无效，也不能把预估评分当成实测结果。\n"
        f"# 必须逐项重新核对的先前意见\n{branch.get('review_findings', [])}\n"
        "只返回仍未解决的问题，填写目标任务与具体代码、输出或文件依据。"
        "问题没有验证依据时保留疑点；不能仅因求解者称已修复就清空。全部有依据地解决后issues留空。"
    )


async def review_solution(ctx: GraphContext, branch: BranchInput, report: Dict, runs: List[Dict]) -> ResultReview:
    prompt = review_prompt(branch, report, ctx, runs)
    from ai_system.research.runtime import execution_scope
    with labelled(f"{branch['subproblem']['id']}:review"), execution_scope(step='检查实验报告'):
        return await call_structured(ctx.planner_llm, ResultReview, as_messages(SYSTEM, prompt))
