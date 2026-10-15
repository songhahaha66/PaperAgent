"""
求解结果审查（critic）：在写作之前检查结果本身靠不靠谱

求解循环只保证"数字确实是代码算出来的"，保证不了"算法用对了"。
2024_C 的 trace 里出现过：只用了一场比赛的数据、类别不平衡下用 F1 显得很高、训练/验证差距大。
这里让审查模型对照代码和登记结果逐项检查，有必须修正的问题就把意见交回同一个求解循环
（内核还在，变量不用重算），最多 max_review_rounds 轮。
"""

from typing import Dict, List

from ..context import GraphContext
from ..llm_utils import as_messages, call_structured, clip
from ..state import BranchInput, ResultReview

SYSTEM = (
    "你是数学建模竞赛的评审老师，负责在写论文之前审查一个子问题的求解结果。"
    "只依据给出的代码、输出和登记结果判断，不要臆测。只把真正影响结论可信度的问题列为必须修正，"
    "措辞风格、可有可无的扩展不要列入。"
)

CHECKLIST = """逐项检查：
1. 数据范围：是否用了题目给出的全部相关数据（例如全部比赛、全部年份）；只用了子集时理由是否充分。
2. 数据泄漏与前视偏差：特征或标签是否用到了预测时刻之后的信息；训练/测试划分是否合理。
3. 样本量与过拟合：样本是否太少；训练集与验证集指标差距是否过大。
4. 指标是否合适：类别不平衡时是否报告了 AUC、基准率或对照基线；回归是否有残差诊断。
5. 结论是否被结果支持：主要结论与登记的数值是否一致，是否夸大。
6. 交付物是否齐全：题目要求的每一项是否都有对应结果。"""


def review_prompt(branch: BranchInput, report: Dict, ctx: GraphContext, runs: List[Dict]) -> str:
    subproblem = branch["subproblem"]
    code = "\n\n".join(
        f"## {r['run_id']}（{'成功' if r['ok'] else '失败'}）\n```python\n{clip(r['code'], 1500)}\n```"
        for r in runs[-8:]
    )
    return (
        f"# 子问题 {subproblem['id']}：{subproblem['title']}\n{subproblem['question']}\n"
        f"交付物：{'；'.join(subproblem.get('deliverables') or [])}\n\n"
        f"# 数据概览\n{clip(branch['data_profile'], 4000)}\n\n"
        f"# 选定方案\n{branch['design']['chosen']['name']}：{branch['design']['chosen']['idea']}\n\n"
        f"# 求解报告\n{report}\n\n"
        f"# 登记结果\n{ctx.registry.to_prompt_table(subproblem['id'])}\n\n"
        f"# 最近的代码\n{code or '（无）'}\n\n{CHECKLIST}"
    )


async def review_solution(ctx: GraphContext, branch: BranchInput, report: Dict, runs: List[Dict]) -> ResultReview:
    prompt = review_prompt(branch, report, ctx, runs)
    return await call_structured(ctx.planner_llm, ResultReview, as_messages(SYSTEM, prompt))


def feedback_message(review: ResultReview) -> str:
    items = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(review.must_fix))
    return (
        "评审老师审查了你的结果，以下问题必须修正：\n"
        f"{items}\n\n"
        "内核中的变量仍然存在。请补充或重做相应计算，登记新的结果，然后重新提交完整的求解报告。"
    )
