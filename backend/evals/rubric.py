"""
论文质量评分：MM-Bench 的 4 个维度 × 2 个子项，共 8 个 Score 问题

维度与等级划分沿用 MM-Bench 的评测细则（MMBench/evaluation/prompts.py），
1–10 分按 5 个等级段（1-2 / 3-4 / 5-6 / 7-8 / 9-10）离散成 Score 的 5 级，
期望等级再映射回 1–10：score_10 = 1.5 + 2 * level。

每个维度单独发一次请求，state 只放与该维度相关的章节（Jev 在无关内容多时准确率下降）。
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ai_system.judge import Judge, Score

# 每次请求中论文正文的最大字符数（中文约 1 token/字，Jev 单问题上下文 32k token）
MAX_PAPER_CHARS = 20000
# 过滤后正文少于此长度时，改为发送整篇论文
MIN_FILTERED_CHARS = 800
# 置信度低于此值的子项需要人工复核
LOW_CONFIDENCE = 0.5


@dataclass(frozen=True)
class SubCriterion:
    key: str
    instructions: str
    levels: Tuple[str, str, str, str, str]


@dataclass(frozen=True)
class Dimension:
    key: str
    name: str
    # 章节标题关键词（中英文），用来挑选相关正文
    section_keywords: Tuple[str, ...]
    subcriteria: Tuple[SubCriterion, ...]


DIMENSIONS: Tuple[Dimension, ...] = (
    Dimension(
        key="analysis",
        name="问题分析",
        section_keywords=("摘要", "重述", "问题分析", "summary", "abstract", "restatement", "analysis of the problem", "problem analysis", "introduction"),
        subcriteria=(
            SubCriterion(
                "definition",
                "In `paper`, how clearly are the scope and goals of the problem in `problem_requirement` defined, "
                "with its key components identified and the goals the model must reach stated explicitly?",
                ("completely unclear", "not clear enough", "basically clear", "clear", "completely clear"),
            ),
            SubCriterion(
                "coverage",
                "How completely do the sub-problems that `paper` breaks the task into cover every requirement in "
                "`problem_requirement`, without missing a key part or drifting from the actual goals?",
                ("completely deviated from the goals", "partially deviated", "basically covered", "mostly covered", "completely covered"),
            ),
        ),
    ),
    Dimension(
        key="rigor",
        name="建模严谨性",
        section_keywords=("假设", "符号", "模型", "建模", "assumption", "notation", "model"),
        subcriteria=(
            SubCriterion(
                "assumptions",
                "How reasonable are the modeling assumptions in `paper`: clearly stated, consistent with the background "
                "in `problem_background`, and with their impact on the model discussed?",
                ("completely unreasonable", "partially reasonable", "average", "reasonable", "very reasonable"),
            ),
            SubCriterion(
                "rationality",
                "How appropriate are the methods and metrics `paper` chooses, and how well does the model's structure "
                "reflect the real problem?",
                ("completely unreasonable", "partially reasonable", "average", "reasonable", "very reasonable"),
            ),
        ),
    ),
    Dimension(
        key="practicality",
        name="实用与科学性",
        section_keywords=("模型", "求解", "建立", "评价", "推广", "model", "solution", "solving", "strength", "weakness", "evaluation"),
        subcriteria=(
            SubCriterion(
                "practicality",
                "How practical is the model in `paper`: does the method fit this specific problem, give insight beyond "
                "fitting the data, and produce output that supports real decisions?",
                ("completely impractical", "partially practical", "average", "practical", "very practical"),
            ),
            SubCriterion(
                "scientificity",
                "How scientific is the model in `paper`: grounded in theory or domain principles, with justified "
                "assumptions and methods rather than a generic technique applied without reasoning?",
                ("completely unscientific", "partially scientific", "average", "scientific", "very scientific"),
            ),
        ),
    ),
    Dimension(
        key="results",
        name="结果与偏差分析",
        section_keywords=("结果", "求解", "灵敏度", "敏感性", "误差", "偏差", "检验", "评价", "result", "sensitivity", "error", "bias", "validation", "strength", "weakness"),
        subcriteria=(
            SubCriterion(
                "results",
                "How clear are the results in `paper`: stated explicitly, with the reasoning from model to result "
                "explained, and with conclusions that interpret what the results mean for the problem?",
                ("completely unclear", "partially clear", "average", "clear", "very clear"),
            ),
            SubCriterion(
                "bias",
                "How thoroughly does `paper` identify data bias and model bias, analyze their effect, and correct for them?",
                ("biases completely ignored", "biases partially considered", "average", "biases considered and corrected", "very thorough, biases effectively corrected"),
            ),
        ),
    ),
)


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def split_sections(markdown: str) -> List[Tuple[str, str]]:
    """按 Markdown 标题切分，返回 [(标题, 含标题的正文)]；标题前的内容标题为空串"""
    matches = list(_HEADING.finditer(markdown))
    if not matches:
        return [("", markdown)]
    sections = []
    if matches[0].start() > 0:
        sections.append(("", markdown[: matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections.append((m.group(2).strip(), markdown[m.start():end]))
    return sections


def select_sections(markdown: str, keywords: Tuple[str, ...]) -> str:
    """挑出标题含关键词的章节；太短时退回整篇。结果截断到 MAX_PAPER_CHARS"""
    picked = [
        body for title, body in split_sections(markdown)
        if title and any(k in title.lower() for k in keywords)
    ]
    text = "\n".join(picked)
    if len(text) < MIN_FILTERED_CHARS:
        text = markdown
    if len(text) > MAX_PAPER_CHARS:
        text = text[:MAX_PAPER_CHARS] + "\n\n[...论文其余部分因长度限制省略...]"
    return text


def level_to_ten(level: float) -> float:
    return round(1.5 + 2 * level, 2)


@dataclass
class PaperScore:
    judge: str
    # 维度 -> 1–10 分（两个子项平均）
    dimensions: Dict[str, float] = field(default_factory=dict)
    # "维度.子项" -> {score_10, level, confidence, reason}
    details: Dict[str, Dict] = field(default_factory=dict)
    input_tokens: int = 0

    @property
    def overall(self) -> Optional[float]:
        if not self.dimensions:
            return None
        return round(sum(self.dimensions.values()) / len(self.dimensions), 2)

    @property
    def needs_review(self) -> List[str]:
        return [
            key for key, d in self.details.items()
            if d["confidence"] is not None and d["confidence"] < LOW_CONFIDENCE
        ]

    def to_dict(self) -> Dict:
        return {
            "judge": self.judge,
            "overall": self.overall,
            "dimensions": self.dimensions,
            "details": self.details,
            "needs_review": self.needs_review,
            "input_tokens": self.input_tokens,
        }


# LLM 判分读全文；超过这个长度才截断（远大于常见论文长度）
MAX_FULL_PAPER_CHARS = 150000


def paper_for_judge(judge: Judge, paper: str, keywords: Tuple[str, ...]) -> str:
    """Jev 单题上下文 32k，只喂相关章节；LLM 判分读全文"""
    if getattr(judge, "name", "") == "jev":
        return select_sections(paper, keywords)
    return paper[:MAX_FULL_PAPER_CHARS]


async def score_paper(judge: Judge, background: str, requirement: str, paper: str) -> PaperScore:
    result = PaperScore(judge=judge.name)
    for dim in DIMENSIONS:
        state = {
            "problem_background": background,
            "problem_requirement": requirement,
            "paper": paper_for_judge(judge, paper, dim.section_keywords),
        }
        questions = {
            sub.key: Score(instructions=sub.instructions, criteria=list(sub.levels))
            for sub in dim.subcriteria
        }
        answers = await judge.ask(state, questions)
        result.judge = answers.judge or result.judge
        result.input_tokens += answers.input_tokens or 0

        sub_scores = []
        for sub in dim.subcriteria:
            answer = answers.scores[sub.key]
            score_10 = level_to_ten(answer.score)
            sub_scores.append(score_10)
            result.details[f"{dim.key}.{sub.key}"] = {
                "score_10": score_10,
                "level": round(answer.score, 3),
                "confidence": answer.confidence,
                "reason": answer.reason,
            }
        result.dimensions[dim.key] = round(sum(sub_scores) / len(sub_scores), 2)
    return result
