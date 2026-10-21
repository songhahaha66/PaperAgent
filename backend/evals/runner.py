"""
在 MM-Bench 题目上运行论文生成流程，并对产物打分

目录结构（每次评测一个 out 目录）：
    <out>/<problem_id>/workspace/   agent 的工作空间（paper.md、runs/、manifest.json）
    <out>/<problem_id>/run.json     运行记录：耗时、token、错误
    <out>/<problem_id>/score.json   评分：确定性指标 + 4 维度判分
    <out>/summary.json / summary.md 汇总
"""

import json
import logging
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.callbacks import get_usage_metadata_callback

from .metrics import code_runs, number_traceability, section_coverage
from .mmbench import Problem, build_task_prompt, load_problem, stage_datasets
from .rubric import DIMENSIONS, score_paper

logger = logging.getLogger(__name__)

PAPER_FILENAME = "paper.md"


@dataclass
class ModelSpec:
    provider: str
    model_id: str
    api_key: str
    base_url: Optional[str] = None
    extra_body: Optional[Dict] = None

    @classmethod
    def from_env(cls, prefix: str) -> Optional["ModelSpec"]:
        """读取 <prefix>_PROVIDER / _MODEL / _API_KEY / _BASE_URL / _EXTRA_BODY（JSON），缺模型或 key 时返回 None"""
        model_id = os.getenv(f"{prefix}_MODEL")
        api_key = os.getenv(f"{prefix}_API_KEY")
        if not model_id or not api_key:
            return None
        return cls(
            provider=os.getenv(f"{prefix}_PROVIDER", "openai"),
            model_id=model_id,
            api_key=api_key,
            base_url=os.getenv(f"{prefix}_BASE_URL") or None,
            extra_body=json.loads(os.environ[f"{prefix}_EXTRA_BODY"]) if os.getenv(f"{prefix}_EXTRA_BODY") else None,
        )

    def create_llm(self, **kwargs):
        from ai_system.core_handlers.llm_providers import LLMProviderFactory

        if self.extra_body:
            kwargs.setdefault("extra_body", self.extra_body)

        return LLMProviderFactory.create_llm_instance(
            {
                "provider": self.provider,
                "model_id": self.model_id,
                "api_key": self.api_key,
                "base_url": self.base_url,
            },
            **kwargs,
        )

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model_id}"


def _sum_usage(usage_by_model: Dict[str, Dict]) -> Dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_read_tokens": 0}
    for usage in usage_by_model.values():
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            totals[key] += int(usage.get(key) or 0)
        totals["cache_read_tokens"] += int((usage.get("input_token_details") or {}).get("cache_read") or 0)
    return totals


# All evaluation variants use the same versioned research engine.
RESEARCH_VARIANTS = {
    "research": {},
    "research-no-planner": {"max_planning_calls": 0},
}
PIPELINES = tuple(RESEARCH_VARIANTS)


def _build_agent(pipeline: str, llm, workspace: Path):
    if pipeline not in RESEARCH_VARIANTS:
        raise ValueError(f"未知 pipeline: {pipeline}")
    from ai_system.graph.agent import PaperGraphAgent

    return PaperGraphAgent(llm, workspace_dir=str(workspace), confirm_models=False, **RESEARCH_VARIANTS[pipeline])


async def run_problem(
    problem: Problem, out_dir: Path, model: ModelSpec, pipeline: str = "research", resume: bool = False
) -> Dict:
    """用指定流水线（markdown 模式）跑一道题；resume=True 时从已提交研究状态恢复，耗时和用量累加到上次记录上"""
    workspace = out_dir / problem.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    staged = stage_datasets(problem, workspace)
    prompt = build_task_prompt(problem, staged)
    llm = model.create_llm(streaming=False)

    record = {
        "problem_id": problem.id,
        "pipeline": pipeline,
        "model": model.label,
        "error": None,
    }
    started = time.monotonic()
    with get_usage_metadata_callback() as usage_cb:
        try:
            agent = _build_agent(pipeline, llm, workspace)
            if resume:
                record["final_message"] = await agent.resume_failed()
            else:
                record["final_message"] = await agent.run(prompt)
            record["engine_version"] = agent.engine_version
            if agent.last_result.get("research_blocked"):
                record["error"] = "研究被阻塞，未发布完整交付物"
            # 智能体自己捕获了异常、只返回失败说明时，也要记为出错，否则汇总里看不出来
            if str(record["final_message"] or "").startswith("任务执行失败"):
                record["error"] = record["final_message"]
        except Exception:
            record["error"] = traceback.format_exc()
            logger.exception("运行 %s 失败", problem.id)
    record["seconds"] = round(time.monotonic() - started, 1)
    record["usage"] = _sum_usage(usage_cb.usage_metadata)
    record["usage_by_model"] = usage_cb.usage_metadata
    record["paper_exists"] = (workspace / PAPER_FILENAME).exists()
    calls_path = workspace / ".system" / "llm_calls.json"
    if calls_path.exists():
        # 逐次模型调用的耗时汇总：慢调用、截断、出错次数和各节点耗时
        record["llm_calls"] = json.loads(calls_path.read_text(encoding="utf-8")).get("summary")

    previous_path = out_dir / problem.id / "run.json"
    if resume:
        previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
        record["resumes"] = previous.get("resumes", 0) + 1
        # 被外部中断的那次没有写 run.json 时，之前的耗时和用量无从累加，记下来避免误读
        record["partial_usage"] = previous.get("partial_usage", not previous)
        record["seconds"] = round(record["seconds"] + previous.get("seconds", 0), 1)
        for key, value in (previous.get("usage") or {}).items():
            record["usage"][key] = record["usage"].get(key, 0) + value

    previous_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


async def score_problem(problem: Problem, out_dir: Path, judge) -> Dict:
    workspace = out_dir / problem.id / "workspace"
    paper_path = workspace / PAPER_FILENAME
    paper = paper_path.read_text(encoding="utf-8") if paper_path.exists() else ""
    problem_text = f"{problem.background}\n{problem.requirement}\n{problem.addendum}"

    result: Dict = {
        "problem_id": problem.id,
        "paper_chars": len(paper),
        "code_runs": code_runs(workspace),
        "sections": section_coverage(paper),
        "numbers": number_traceability(paper, workspace, problem_text),
        "judge": None,
    }
    if paper and judge is not None:
        try:
            scored = await score_paper(judge, problem.background, problem.requirement, paper)
            result["judge"] = scored.to_dict()
        except Exception:
            # 判分失败不影响其余题目，错误留在 score.json 里
            result["judge_error"] = traceback.format_exc()
            logger.exception("判分 %s 失败", problem.id)

    (out_dir / problem.id / "score.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _mean(values: List[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 3) if present else None


def summarize(out_dir: Path, problem_ids: List[str]) -> Dict:
    rows = []
    for pid in problem_ids:
        run_path = out_dir / pid / "run.json"
        score_path = out_dir / pid / "score.json"
        run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
        score = json.loads(score_path.read_text(encoding="utf-8")) if score_path.exists() else {}
        judge = score.get("judge") or {}
        rows.append({
            "problem_id": pid,
            "paper": bool(run.get("paper_exists")),
            "error": bool(run.get("error")),
            "seconds": run.get("seconds"),
            "total_tokens": (run.get("usage") or {}).get("total_tokens"),
            "code_success_rate": (score.get("code_runs") or {}).get("success_rate"),
            "section_coverage": (score.get("sections") or {}).get("coverage"),
            "number_trace_rate": (score.get("numbers") or {}).get("rate"),
            "number_trace_error": (score.get("numbers") or {}).get("error"),
            "overall": judge.get("overall"),
            **{f"dim_{d.key}": (judge.get("dimensions") or {}).get(d.key) for d in DIMENSIONS},
            "needs_review": judge.get("needs_review") or [],
            "judge": judge.get("judge"),
        })

    numeric = [
        "seconds", "total_tokens", "code_success_rate", "section_coverage",
        "number_trace_rate", "overall", *[f"dim_{d.key}" for d in DIMENSIONS],
    ]
    summary = {
        "problems": len(rows),
        "papers_produced": sum(r["paper"] for r in rows),
        "errors": sum(r["error"] for r in rows),
        "number_trace_errors": sum(bool(r["number_trace_error"]) for r in rows),
        "mean": {key: _mean([r[key] for r in rows]) for key in numeric},
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def _fmt(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(summary: Dict) -> str:
    dims = [f"dim_{d.key}" for d in DIMENSIONS]
    headers = ["题目", "总分", *[d.name for d in DIMENSIONS], "代码成功率", "章节齐全", "数字可追溯", "耗时(s)", "tokens"]
    keys = ["overall", *dims, "code_success_rate", "section_coverage", "number_trace_rate", "seconds", "total_tokens"]
    lines = [
        f"共 {summary['problems']} 题，产出论文 {summary['papers_produced']} 篇，出错 {summary['errors']} 次。"
        f"数字来源评测不可用 {summary['number_trace_errors']} 题（不计入该项平均）。",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]
    for row in summary["rows"]:
        lines.append("| " + " | ".join([row["problem_id"], *[_fmt(row[k]) for k in keys]]) + " |")
    lines.append("| **平均** | " + " | ".join(_fmt(summary["mean"][k]) for k in keys) + " |")
    review = [f"{r['problem_id']}: {', '.join(r['needs_review'])}" for r in summary["rows"] if r["needs_review"]]
    if review:
        lines += ["", "低置信度、建议人工复核的子项：", *[f"- {item}" for item in review]]
    unavailable = [f"{r['problem_id']}: {r['number_trace_error']}" for r in summary["rows"] if r["number_trace_error"]]
    if unavailable:
        lines += ["", "数字来源评测不可用：", *[f"- {item}" for item in unavailable]]
    return "\n".join(lines) + "\n"


def load_problems(bench_dir: Path, problem_ids: List[str]) -> List[Problem]:
    return [load_problem(bench_dir, pid) for pid in problem_ids]
