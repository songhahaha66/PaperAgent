"""
评测命令行

    cd backend
    uv run python -m evals run   --bench /path/to/LLM-MM-Agent/MMBench --out eval_runs/research
    uv run python -m evals score --bench /path/to/LLM-MM-Agent/MMBench --out eval_runs/research
    uv run python -m evals all   --bench ... --out ...        # run + score + 汇总（默认 research 版本化研究引擎）
    uv run python -m evals all   --pipeline research-no-explore ...                   # 消融：关掉方案探索（另有 research-no-review / research-no-consistency）

生成模型：EVAL_MODEL / EVAL_API_KEY / EVAL_PROVIDER(默认 openai) / EVAL_BASE_URL / EVAL_EXTRA_BODY（JSON，如 {"thinking": {"type": "disabled"}}）
判分：--judge jev（需要 TYPESAFE_API_KEY，--jev-model 固定版本）
      --judge llm（EVAL_JUDGE_MODEL / EVAL_JUDGE_API_KEY / ...，应与生成模型不同）
      --judge auto（默认：有 Jev key 用 Jev，否则用 LLM 判分）
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from ai_system.judge import JevJudge, LLMJudge, jev_available  # noqa: E402

from .mmbench import DEFAULT_SUBSET  # noqa: E402
from .runner import PIPELINES, ModelSpec, load_problems, run_problem, score_problem, summarize  # noqa: E402

DEFAULT_JEV_MODEL = "jev-1.14.0"


def build_judge(kind: str, jev_model: str):
    if kind == "none":
        return None
    if kind == "jev" or (kind == "auto" and jev_available()):
        if not jev_available():
            raise SystemExit("--judge jev 需要设置 TYPESAFE_API_KEY")
        return JevJudge(model=jev_model)
    spec = ModelSpec.from_env("EVAL_JUDGE")
    if spec is None:
        if kind == "llm":
            raise SystemExit("--judge llm 需要设置 EVAL_JUDGE_MODEL 和 EVAL_JUDGE_API_KEY")
        logging.warning("没有 Jev key，也没有 EVAL_JUDGE_* 配置：只计算确定性指标")
        return None
    return LLMJudge(spec.create_llm(streaming=False, temperature=0), label=f"llm:{spec.label}")


async def main(args) -> None:
    problem_ids = args.problems.split(",") if args.problems else DEFAULT_SUBSET
    problems = load_problems(Path(args.bench), problem_ids)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.command in ("run", "all"):
        model = ModelSpec.from_env("EVAL")
        if model is None:
            raise SystemExit("运行需要设置 EVAL_MODEL 和 EVAL_API_KEY")
        for problem in problems:
            if args.skip_existing and (out_dir / problem.id / "run.json").exists():
                continue
            logging.info("%s %s", "恢复" if args.resume else "运行", problem.id)
            record = await run_problem(problem, out_dir, model, args.pipeline, resume=args.resume)
            logging.info("%s 完成：%.0fs，论文=%s", problem.id, record["seconds"], record["paper_exists"])

    if args.command in ("score", "all"):
        judge = build_judge(args.judge, args.jev_model)
        for problem in problems:
            if not (out_dir / problem.id / "run.json").exists():
                logging.warning("跳过 %s：还没有运行记录", problem.id)
                continue
            logging.info("评分 %s", problem.id)
            await score_problem(problem, out_dir, judge)

    summary = summarize(out_dir, problem_ids)
    print((out_dir / "summary.md").read_text(encoding="utf-8"))
    logging.info("平均总分：%s", summary["mean"]["overall"])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m evals", description="PaperAgent MM-Bench 评测")
    parser.add_argument("command", choices=["run", "score", "all", "report"])
    parser.add_argument("--pipeline", choices=PIPELINES, default="research", help="research=版本化研究控制，research-no-*=消融版本")
    parser.add_argument("--bench", required=True, help="本地 MMBench 目录（含 problem/ 与 dataset/）")
    parser.add_argument("--out", required=True, help="本次评测的输出目录")
    parser.add_argument("--problems", default="", help="逗号分隔的题号，默认 10 题子集")
    parser.add_argument("--judge", choices=["auto", "jev", "llm", "none"], default="auto")
    parser.add_argument("--jev-model", default=DEFAULT_JEV_MODEL, help="固定 Jev 版本，保证前后可比")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有 run.json 的题目")
    parser.add_argument("--resume", action="store_true", help="从已提交研究状态恢复未完成任务")
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main(parse_args()))
