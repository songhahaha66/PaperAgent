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
from typing import Dict, List, Optional, Tuple

from ..context import GraphContext
from ..registry import REF_PATTERN, GroundingError, find_refs
from ..llm_utils import as_messages, call_structured, clip
from ..state import BranchInput, ResultReview
from ..tracing import labelled
from ...research.validation import requirement_evidence_gaps

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
# 相对简单基线的提升不到基线值的这个比例，就算没有提升
MIN_LIFT = 0.005


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


# 取了负号的损失（"-LogLoss"、"负对数损失"）越大越好；v2l 的 Q1 用 -LogLoss，按"loss"会被判成越小越好
NEGATED_LOSS = re.compile(r"(?:^|[^\w])(?:-|−|负|neg_?)\s*(?:log\s*-?loss|loss|对数损失|nll)", re.IGNORECASE)
# p 值不是预测指标，没有"泄漏"一说：v2l 的 Q2 把点顺序置换后的 p 值填进了 controls，
# 被按泄漏规则判定，检验结论末尾都被加上了"可能存在数据泄漏"
P_VALUE_METRIC = re.compile(r"p\s*值|p-?value|显著性", re.IGNORECASE)


def metric_direction(metric: str) -> Optional[bool]:
    """按指标名判断是否越大越好；认不出时返回 None，沿用模型填写的方向"""
    if NEGATED_LOSS.search(metric):
        return True
    lower, higher = bool(LOWER_IS_BETTER.search(metric)), bool(HIGHER_IS_BETTER.search(metric))
    if lower != higher:
        return higher
    return None


def leaked(real: float, shuffled: float, chance: float, higher_is_better: bool) -> bool:
    """真实标签相对无信息水平有提升，且打乱标签后仍保留一半以上，判定为泄漏"""
    sign = 1 if higher_is_better else -1
    gain, kept = sign * (real - chance), sign * (shuffled - chance)
    return gain > 0 and kept >= LEAK_RATIO * gain


def control_issues(ctx: GraphContext, report: Dict, hypothesis: bool = False) -> List[str]:
    """标签置换对照的确定性检查，返回必须修正的问题"""
    return control_findings(ctx, report, hypothesis)[0]


def downgrade_findings(ctx: GraphContext, report: Dict) -> None:
    """没通过置换对照时，引用了这些指标的结论一律降为不显著

    v2j 的 Q1 打乱标签后 AUC 仍有 0.644（真实 0.777），对照判为泄漏、写进了局限，
    结论里却还写着"提升主要来自有效信号而非泄漏"，写作时两者互相矛盾。
    """
    leaky = set()
    for c in predictive_controls(report):
        real, shuffled, chance = (_as_float(c.get(k, "")) for k in ("value", "shuffled_value", "chance"))
        direction = metric_direction(c.get("metric", ""))
        higher = c.get("higher_is_better", True) if direction is None else direction
        if None not in (real, shuffled, chance) and leaked(real, shuffled, chance, higher):
            leaky.add((str(c["value"]).strip(), c.get("run_id", "")))
    # 按数值和来源运行一起匹配：只按数值匹配，别的结果碰巧同值也会被降级
    leaky_refs = {
        r.id for r in ctx.registry.results.values()
        if r.kind != "figure" and (r.value.strip(), r.run_id) in leaky
    }
    for finding in report.get("key_findings") or []:
        if set(find_refs(finding.get("statement", ""))) & leaky_refs:
            finding["strength"] = "不显著"
            finding["statement"] = finding["statement"].rstrip("。") + "（该指标未通过标签置换对照，可能存在数据泄漏）"


def predictive_controls(report: Dict) -> List[Dict]:
    """controls 里真正的预测指标（去掉 p 值这类检验结果）"""
    return [c for c in report.get("controls") or [] if not P_VALUE_METRIC.search(c.get("metric", ""))]


def baseline_gaps(ctx: GraphContext, report: Dict) -> List[str]:
    """没有超过简单基线的预测指标；这是结果本身，返工只能改写结论，不能让它消失"""
    return control_findings(ctx, report)[1]


def control_findings(ctx: GraphContext, report: Dict, hypothesis: bool = False) -> Tuple[List[str], List[str]]:
    """返回 (必须修正的问题, 没有超过基线的指标)

    hypothesis=True（假设检验类子问题）时不要求置换对照：检验的零分布本身就是对照。
    v2h 的 Q2 报告里出现了"检验功效""95%"等字样，被当成预测效果要求做置换对照，与求解提示矛盾。
    """
    controls = predictive_controls(report)
    text = " ".join([report.get("summary", ""), *(f.get("statement", "") for f in report.get("key_findings") or [])])
    if not controls:
        if not hypothesis and PREDICTIVE.search(ctx.registry.render(text)):
            return [
                "报告了预测效果但没有做标签置换对照：在构造特征之前把目标列随机打乱、其余流程完全不变，"
                "重新计算同一指标，print 并登记真实值和打乱后的值，填进报告的 controls。"
            ], []
        return [], []
    issues: List[str] = []
    gaps: List[str] = []
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
        if shuffled == chance:
            # v2h 的 Q3 填了 shuffled_value=0.5、chance=0.5：实际重算的 AUC 几乎不可能恰好是 0.5，像是填的理论值
            issues.append(
                f"{c['metric']} 打乱后的值 {c['shuffled_value']} 恰好等于无信息水平，看起来是理论值而不是实际重算的结果。"
                "请真的把原始结果列打乱、重跑同一流程，print 实际得到的指标值再填写。"
            )
            continue
        direction = metric_direction(c.get("metric", ""))
        higher = c.get("higher_is_better", True) if direction is None else direction
        if leaked(real, shuffled, chance, higher):
            issues.append(
                f"{c['metric']} 打乱标签后仍为 {c['shuffled_value']}（无信息水平 {c['chance']}，真实标签下 {c['value']}），"
                "说明特征构造或评估用到了标签本身的信息（数据泄漏，例如滤波/平滑/滚动统计包含了当期结果）。"
                "请定位泄漏来源，只用预测时刻之前的信息重新构造特征，再重新评估并重做置换对照。"
            )
            continue
        problem, gap = baseline_check(ctx, c, real, higher)
        issues += problem
        gaps += gap
    return issues, gaps


def baseline_check(ctx: GraphContext, control: Dict, real: float, higher: bool) -> Tuple[List[str], List[str]]:
    """模型相对最简单的有信息基线有没有提升

    置换对照只能说明"比无信息水平好"。2024_C 的 Q1 用滤波势头预测下一分，LogLoss 优于无信息水平和置换对照，
    结论写成"势头携带预测信息"；但灵敏度分析里换任何噪声设置指标都不变，信息其实全来自发球方先验。
    """
    metric = control.get("metric", "")
    if not str(control.get("baseline_value") or "").strip():
        return [
            f"{metric} 没有和最简单的有信息基线比较：请在同一划分上算一个只用显而易见结构的基线（如只用发球方先验胜率、只用上一期值），"
            "print 并登记，填进 controls 的 baseline_name / baseline_value / baseline_run_id。"
        ], []
    try:
        ctx.registry.verify(control["baseline_value"], control.get("baseline_run_id", ""))
    except GroundingError as exc:
        return [f"{metric} 的基线数值无法核对：{exc}"], []
    baseline = _as_float(control["baseline_value"])
    if baseline is None:
        return [f"{metric} 的基线值不是数值，请填写数值"], []
    chance = _as_float(control.get("chance", ""))
    if chance is not None and abs(baseline - chance) <= MIN_LIFT * max(abs(chance), 1e-9) * 2:
        # v2h 的 Q1 拿"常数先验胜率"当基线，它就是无信息水平，绕过了和发球方先验的比较
        return [
            f"{metric} 的基线 {control['baseline_value']} 就是无信息水平 {control['chance']}，不算有信息的基线。"
            "请换成只用显而易见结构的基线（如按发球方区分的先验胜率），重新算出并登记。"
        ], []
    lift = (real - baseline) if higher else (baseline - real)
    if lift > MIN_LIFT * max(abs(baseline), 1e-9):
        return [], []
    return [], [
        f"{metric} 为 {control['value']}，没有优于基线「{control.get('baseline_name') or '简单基线'}」的 {control['baseline_value']}。"
        "预测信息来自基线已包含的结构，不能说模型（或其中的动量、状态等成分）有额外预测力。"
    ]


# 显著性水平、置信水平这类约定常数可以直接写
CONVENTIONS = {"0.05", "0.01", "0.001", "0.1", "0.10", "5%", "1%", "10%", "90%", "95%", "99%"}
# 报告里直接写的小数或百分数（整数是场次、阶数这类计数，不查）
LOOSE_NUMBER = re.compile(r"(?<![\w.])-?\d+\.\d+%?|(?<![\w.])\d+%")


def ungrounded_findings(report: Dict) -> List[str]:
    """报告概要和主要结论里没写成引用的数字

    2024_C 的 Q2 只登记了 2 项（验证集场数、置换次数），检验统计量 z=4.05、9.18 直接写在结论里，
    结论节点没有证据可指，写作时这些数又被放进公式 $z = 4.05$ 躲过了正文数字校验。
    """
    texts = [report.get("summary", ""), *(f.get("statement", "") for f in report.get("key_findings") or [])]
    loose: List[str] = []
    for text in texts:
        for match in LOOSE_NUMBER.finditer(REF_PATTERN.sub(" ", text or "")):
            if match.group() not in loose and match.group() not in CONVENTIONS:
                loose.append(match.group())
    if not loose:
        return []
    return [
        f"报告的概要或主要结论里直接写了数字 {', '.join(loose[:8])}，没有对应的登记结果。"
        "请先 print 这些数（检验统计量、p 值、指标等），用 record_results 登记，再把报告里的数字改成引用后重新提交。"
    ]


def foreign_refs(ctx: GraphContext, report: Dict, subproblem_id: str, dependencies: List[str]) -> List[str]:
    """报告里引用了不存在的、或者不属于本子问题及其依赖的结果

    2024_C 的 Q3 一项结果都没登记，结论却写成"验证集 AUC 为 {{R:R3}}"，R3 是 Q1 登记的"验证集场数"。
    """
    allowed = {subproblem_id, *dependencies}
    texts = [report.get(key, "") for key in ("summary", "approach", "sensitivity")]
    texts += [*report.get("limitations", []), *(f.get("statement", "") for f in report.get("key_findings") or [])]
    bad = []
    for ref in find_refs(" ".join(texts)):
        result = ctx.registry.get(ref)
        if (result is None or result.subproblem not in allowed) and ref not in bad:
            bad.append(ref)
    if not bad:
        return []
    return [
        f"报告引用的 {', '.join(bad)} 不是本子问题登记的结果（不存在或属于其他子问题）。"
        "引用只能使用 record_results / record_figure 返回给你的编号，请先登记本问的结果再改写报告。"
    ]


# 这些量只能落在 [0, 1]（或 0-100%）里；紧挨在引用前面出现时，用来发现引错编号的结论
UNIT_INTERVAL_METRIC = re.compile(
    r"(?:(?<![A-Za-z])p|AUC|C-?index|Brier|准确率|召回率|精确率|概率)\s*(?:值)?\s*(?:[=＝≈<>≤≥]|为|是|约为?)\s*$",
    re.IGNORECASE,
)


def implausible_refs(ctx: GraphContext, report: Dict) -> List[str]:
    """结论里 "p = {{R:Rn}}"、"AUC 为 {{R:Rn}}" 引用的值不在 [0, 1] 内：多半是引错了编号

    v2l 的结论写出了 "p=9.6546"、"AUC=426 个"、"C-index -0.0789"：数值本身都是登记过的，
    只是引用指向了统计量、事件数这些别的结果。登记表只保证数字算过，保证不了引用对得上，审查到写作阶段才发现，已经改不了了。
    """
    texts = [report.get("summary", ""), *(f.get("statement", "") for f in report.get("key_findings") or [])]
    bad: List[str] = []
    for text in texts:
        for match in REF_PATTERN.finditer(text):
            metric = UNIT_INTERVAL_METRIC.search(text[max(0, match.start() - 12):match.start()])
            result = ctx.registry.get(match.group(2))
            if not metric or result is None or result.kind == "figure":
                continue
            value = _as_float(result.value)
            if value is not None and not 0 <= value <= 1:
                bad.append(f"{metric.group(0).strip()} {match.group(0)}（登记值 {result.value}，名称「{result.name}」）")
    if not bad:
        return []
    return [
        "这些引用前写的是只能在 0 到 1 之间的量，但引用的登记值不在这个范围，多半引错了编号："
        + "；".join(dict.fromkeys(bad))
        + "。请对照登记表改成正确的结果编号；需要的量还没登记的，print 出来登记后再引用。"
    ]


MIN_SUMMARY_CHARS = 8


def degenerate(report: Dict) -> bool:
    """概要几乎为空或没有主要结论的报告"""
    return len((report.get("summary") or "").strip()) < MIN_SUMMARY_CHARS or not report.get("key_findings")


# 题目要求出图的子问题
VISUAL_TASK = re.compile(r"可视化|画图|绘制|作图|图表|图示|visuali", re.IGNORECASE)


def needs_figure(subproblem: Dict) -> bool:
    text = " ".join([subproblem.get("title", ""), subproblem.get("question", ""), *(subproblem.get("deliverables") or [])])
    return bool(VISUAL_TASK.search(text))


def submission_issues(
    ctx: GraphContext,
    report: Dict,
    subproblem_id: str,
    dependencies: Optional[List[str]] = None,
    figure_required: bool = False,
) -> List[str]:
    """报告的结构与引用检查；数值是否满足领域条件仍由提交器验收。"""
    issues = (
        # v2j 的 Q3 被要求补置换对照、改引用之后，交了概要为 "test"、没有任何结论的报告：
        # 空报告不触发其他检查，等于绕过了检查
        [
            "报告的概要或主要结论为空：请提交完整的求解报告，写明主要结论（数字用登记结果的引用），"
            "并按之前的审查意见补齐对照和登记，不能删掉结论来回避检查。"
        ] if degenerate(report) else []
    ) + (
        foreign_refs(ctx, report, subproblem_id, dependencies or [])
        + ungrounded_findings(report)
        + implausible_refs(ctx, report)
    )
    if figure_required and not any(r.kind == "figure" for r in ctx.registry.for_subproblem(subproblem_id)):
        # v2h 的 Q1 要求"可视化比赛流向"却一张图都没登记，写作时编了一个不存在的 {{F:F1}}
        issues.append("题目要求可视化，但还没有登记任何图：请画出要求的图，用 record_figure 登记后在报告里引用。")
    if not ctx.registry.for_subproblem(subproblem_id):
        # 2024_C 的 Q1 跑了两段读数据的代码就交了报告，一项结果都没登记
        issues.insert(0, "还没有登记任何结果：请完成方案中的建模计算，print 关键数值，用 record_results 和 record_figure 登记后再提交报告。")
    return issues


def submission_inventory_issues(ctx: GraphContext, branch: BranchInput, runs: List[Dict]) -> List[str]:
    """Check required metrics and their actual executions, without judging thresholds."""
    sid = branch["subproblem"]["id"]
    own = ctx.registry.for_subproblem(sid)
    issues = []
    if not own:
        issues.append("还没有登记任何结果：请完成方案中的建模计算，print 关键数值，用 record_results 和 record_figure 登记后再提交报告。")
    if needs_figure(branch["subproblem"]) and not any(r.kind == "figure" for r in own):
        issues.append("题目要求可视化，但还没有登记任何图：请画出要求的图，用 record_figure 登记后在报告里引用。")
    allowed = {sid, *(branch["subproblem"].get("depends_on") or [])}
    successful = {run["run_id"] for run in runs if run.get("ok")}
    for result in own:
        try:
            if result.run_id not in successful:
                raise GroundingError("来源不是本次任务的成功执行")
            if result.kind == "figure":
                info = ctx.fs.get_run_info(result.run_id)
                if not info or info.get("status") != "success":
                    raise GroundingError("图片来源不是成功执行")
                if not result.path or not ctx.fs.exists(result.path):
                    raise GroundingError("登记的图片文件不存在")
            else:
                ctx.registry.verify(result.value, result.run_id)
        except (GroundingError, OSError, ValueError) as exc:
            issues.append(f"登记结果 {result.id}「{result.name}」来源无效：{exc}；请重新执行并登记")
    for rule in (branch.get("research_contract") or {}).get("checks", []):
        check = rule["check"]
        for key in ("left", "right"):
            selector = check.get(key)
            if not selector:
                continue
            target = selector["task_id"]
            if branch["subproblem"].get("kind") == "verify" and target == check["left"]["task_id"]:
                target = sid
            found = [r for r in ctx.registry.results.values() if r.subproblem == target and r.name == selector["name"]]
            if target not in allowed or len(found) != 1:
                issues.append(f"检查 {check['id']} 缺少唯一登记指标 {target}「{selector['name']}」；请按契约原名计算、print并登记，不能改名或放宽检查")
    return list(dict.fromkeys(issues))


def report_submission_issues(ctx: GraphContext, branch: BranchInput, report: Dict, runs: List[Dict]) -> List[str]:
    subproblem = branch["subproblem"]
    sid = subproblem["id"]
    deps = subproblem.get("depends_on") or []
    issues = submission_issues(ctx, report, sid, deps, figure_required=needs_figure(subproblem))
    issues += submission_inventory_issues(ctx, branch, runs)
    available = {r.id for r in ctx.registry.results.values() if r.subproblem in {sid, *deps}}
    requirements = (branch.get("research_contract") or {}).get("requirements", [])
    issues += list(requirement_evidence_gaps([r["id"] for r in requirements], report.get("requirement_evidence", []), available).values())
    return list(dict.fromkeys(issues))


def report_issues(
    ctx: GraphContext, report: Dict, subproblem_id: str, dependencies: Optional[List[str]] = None,
    hypothesis: bool = False, figure_required: bool = False,
) -> List[str]:
    return submission_issues(ctx, report, subproblem_id, dependencies, figure_required=figure_required) + control_issues(ctx, report, hypothesis)


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
        f"# 最近的代码\n{code or '（无）'}\n\n{CHECKLIST}\n\n"
        f"# 研究契约\n{branch.get('research_contract', {})}\n"
        f"# 必须逐项重新核对的先前意见\n{branch.get('review_findings', [])}\n"
        "只返回仍未解决的问题，填写目标任务、实际证据引用、适用的check_ids和assumption_id。"
        "问题没有验证依据时保留疑点；不能仅因求解者称已修复就清空。全部有依据地解决后issues留空。"
    )


async def review_solution(ctx: GraphContext, branch: BranchInput, report: Dict, runs: List[Dict]) -> ResultReview:
    prompt = review_prompt(branch, report, ctx, runs)
    with labelled(f"{branch['subproblem']['id']}:review"):
        return await call_structured(ctx.planner_llm, ResultReview, as_messages(SYSTEM, prompt))


def feedback_message(review: ResultReview) -> str:
    items = "\n".join(f"{i + 1}. [{item.category}] {item.task_id}：{item.message}" for i, item in enumerate(review.issues))
    return (
        "评审老师审查了你的结果，以下问题必须修正：\n"
        f"{items}\n\n"
        "内核中的变量仍然存在。请补充或重做相应计算，登记新的结果，然后重新提交完整的求解报告。"
    )
