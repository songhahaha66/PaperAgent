"""
求解结果审查（critic）：在写作之前检查结果本身靠不靠谱

求解循环只保证"数字确实是代码算出来的"，保证不了"算法用对了"。
2024_C 的 trace 里出现过：只用了一场比赛的数据、类别不平衡下用 F1 显得很高、训练/验证差距大。
这里让审查模型对照代码和登记结果逐项检查，有必须修正的问题就把意见交回同一个求解循环
（内核还在，变量不用重算），最多 max_review_rounds 轮。

模型审查抓不住所有泄漏：2024_C 的 Q1 用卡尔曼滤波"预测"当前这一分时，滤波值已经吸收了这一分的结果，
LogLoss 从 0.68 降到 0.49，审查模型没有发现。所以预测效果另加一道确定性检查——标签置换对照（negative control）：
在构造特征之前把目标列打乱、流程不变再算一遍指标。没有泄漏时打乱后应回到无信息水平；
打乱后仍保留真实提升的一半以上，就判定为泄漏，作为必须修正的问题交回求解循环。
"""

import re
from typing import Dict, List, Optional

from ..context import GraphContext
from ..registry import GroundingError
from ..llm_utils import as_messages, call_structured, clip
from ..state import BranchInput, ResultReview

SYSTEM = (
    "你是数学建模竞赛的评审老师，负责在写论文之前审查一个子问题的求解结果。"
    "只依据给出的代码、输出和登记结果判断，不要臆测。只把真正影响结论可信度的问题列为必须修正，"
    "措辞风格、可有可无的扩展不要列入。"
)

CHECKLIST = """逐项检查：
1. 数据范围：是否用了题目给出的全部相关数据（例如全部比赛、全部年份）；只用了子集时理由是否充分。
2. 数据泄漏与前视偏差：特征或标签是否用到了预测时刻之后的信息；标签是否由同一时刻的特征直接算出（如用动量变号定义转折、又把当期动量当特征）；训练/测试划分是否合理。
3. 样本量与过拟合：样本是否太少；训练集与验证集指标差距是否过大。
4. 指标是否合适：类别不平衡时是否报告了 AUC、基准率或对照基线；回归是否有残差诊断。
5. 结论是否被结果支持：主要结论与登记的数值是否一致，是否夸大。
6. 交付物是否齐全：题目要求的每一项是否都有对应结果。"""


# 报告里出现这些预测效果指标时，必须附带标签置换对照
PREDICTIVE = re.compile(r"AUC|准确率|精确率|召回率|F1|LogLoss|对数损失|Brier|RMSE|MAE|MAPE|R²|R\^2|决定系数|命中率", re.IGNORECASE)
# 打乱标签后仍保留的提升占真实提升的比例，超过就判定为泄漏
LEAK_RATIO = 0.5


def _as_float(text: str) -> Optional[float]:
    cleaned = str(text).strip().replace(",", "")
    try:
        return float(cleaned.rstrip("%")) / (100 if cleaned.endswith("%") else 1)
    except ValueError:
        return None


# 常见指标的方向是确定的，不靠模型填：2024_C 里模型把"LogLoss，越小越好"的 higher_is_better 填成了 true，
# 两个方案都跑通时就会选出更差的那个
LOWER_IS_BETTER = re.compile(r"loss|损失|误差|error|rmse|mse|mae|mape|brier|aic|bic|困惑度|perplexity|偏差|负对数似然|nll", re.IGNORECASE)
HIGHER_IS_BETTER = re.compile(r"auc|准确率|accuracy|精确率|precision|召回率|recall|f1|r²|r\^2|r2|决定系数|似然|likelihood|命中率", re.IGNORECASE)


def metric_direction(metric: str) -> Optional[bool]:
    """按指标名判断是否越大越好；认不出时返回 None，沿用模型填写的方向"""
    lower, higher = bool(LOWER_IS_BETTER.search(metric)), bool(HIGHER_IS_BETTER.search(metric))
    if lower != higher:
        return higher
    return None


def leaked(real: float, shuffled: float, chance: float, higher_is_better: bool) -> bool:
    """真实标签相对无信息水平有提升，且打乱标签后仍保留一半以上，判定为泄漏"""
    sign = 1 if higher_is_better else -1
    gain, kept = sign * (real - chance), sign * (shuffled - chance)
    return gain > 0 and kept >= LEAK_RATIO * gain


def control_issues(ctx: GraphContext, report: Dict) -> List[str]:
    """标签置换对照的确定性检查，返回必须修正的问题"""
    controls = report.get("controls") or []
    text = " ".join([report.get("summary", ""), *(f.get("statement", "") for f in report.get("key_findings") or [])])
    if not controls:
        if PREDICTIVE.search(ctx.registry.render(text)):
            return [
                "报告了预测效果但没有做标签置换对照：在构造特征之前把目标列随机打乱、其余流程完全不变，"
                "重新计算同一指标，print 并登记真实值和打乱后的值，填进报告的 controls。"
            ]
        return []
    issues = []
    for c in controls:
        try:
            ctx.registry.verify(c["value"], c["run_id"])
            ctx.registry.verify(c["shuffled_value"], c["shuffled_run_id"])
        except GroundingError as exc:
            issues.append(f"{c['metric']} 的置换对照数值无法核对：{exc}")
            continue
        real, shuffled, chance = (_as_float(c[k]) for k in ("value", "shuffled_value", "chance"))
        if None in (real, shuffled, chance):
            issues.append(f"{c['metric']} 的置换对照里有不是数值的字段，请填写数值")
            continue
        direction = metric_direction(c.get("metric", ""))
        if leaked(real, shuffled, chance, c.get("higher_is_better", True) if direction is None else direction):
            issues.append(
                f"{c['metric']} 打乱标签后仍为 {c['shuffled_value']}（无信息水平 {c['chance']}，真实标签下 {c['value']}），"
                "说明特征构造或评估用到了标签本身的信息（数据泄漏，例如滤波/平滑/滚动统计包含了当期结果）。"
                "请定位泄漏来源，只用预测时刻之前的信息重新构造特征，再重新评估并重做置换对照。"
            )
    return issues


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
