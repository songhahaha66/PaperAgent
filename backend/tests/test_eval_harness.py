from services.file_services.workspace_layout import workspace_path
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.judge import Answers, ScoreAnswer
from evals.metrics import code_runs, artifact_files
from evals.mmbench import build_task_prompt, load_problem, stage_datasets
from evals.rubric import DIMENSIONS
from evals.runner import score_problem, summarize


PAPER = """# 摘要
我们建立了动量模型，预测准确率为 0.873，AUC 达到 91.25%。

# 一、问题重述
题目给出 2023 年温网数据。

# 二、问题分析
三个子问题。

# 三、模型假设
假设比赛独立。

# 四、符号说明
| 符号 | 含义 |

# 五、模型的建立与求解
逻辑回归系数为 1.732，残差 0.0451。编造的结果 12.345。

```python
print(3.1415)
```

# 六、灵敏度分析
参数扰动 10% 时结果稳定。

# 七、模型评价
优点与缺点。
"""


def _make_bench(tmp_path: Path) -> Path:
    bench = tmp_path / "MMBench"
    (bench / "problem").mkdir(parents=True)
    (bench / "dataset" / "2024_C").mkdir(parents=True)
    (bench / "dataset" / "2024_C" / "matches.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (bench / "problem" / "2024_C.json").write_text(json.dumps({
        "background": "Wimbledon 2023 final. Serve win rate 0.65.",
        "problem_requirement": "Model momentum.",
        "dataset_path": ["matches.csv"],
        "dataset_description": {"matches": "point by point data"},
        "variable_description": {"a": "set number"},
        "addendum": "",
    }), encoding="utf-8")
    return bench


def _make_workspace(root: Path) -> Path:
    ws = root / "workspace"
    from services.file_services.workspace_fs import WorkspaceFS
    fs = WorkspaceFS(ws); fs.init_structure(); fs._save_manifest()
    run = workspace_path(ws, 'runs') / "run_1"
    run.mkdir(parents=True)
    (run / "stdout.log").write_text("accuracy=0.87312\nauc 0.912512\ncoef [1.73205 0.04511]\n", encoding="utf-8")
    (workspace_path(ws, 'manifest.json')).write_text(json.dumps({"files": {}, "runs": {
        "run_1": {"status": "success", "stdout": "runs/run_1/stdout.log"},
        "run_2": {"status": "error"},
        "run_3": {"status": "success"},
    }}), encoding="utf-8")
    from ai_system.research import ENGINE_VERSION
    from ai_system.research.store import ResearchStore
    from ai_system.research.models import WorkerResult
    store = ResearchStore(ws, "eval")
    store.initialize("Model momentum", {"subproblems": [{"id": "Q1", "question": "Model momentum"}],
        "requirements": [{"id": "RQ1", "statement": "Model momentum", "source_quote": "Model momentum", "kind": "goal", "task_ids": ["Q1"]}],
        "assumptions": []})
    results = [{"id": store.allocate_ref("R"), "kind": "number", "subproblem": "Q1", "name": name, "value": value, "run_id": "run_1"}
               for name, value in {"accuracy": "0.87312", "auc": "0.912512", "coef": "1.73205", "residual": "0.04511"}.items()]
    store.commit(WorkerResult(order=store.claim("Q1"), execution_status="succeeded", acceptance="accepted", results=results,
        report={"requirement_evidence": [{"requirement_id": "RQ1", "evidence_refs": [results[0]["id"]]}]}))
    (workspace_path(ws, '.system/graph_thread.json')).write_text(json.dumps({"thread_id": "eval", "engine_version": ENGINE_VERSION}))
    (ws / "paper.md").write_text(PAPER, encoding="utf-8")
    return ws


class FixedJudge:
    """每个 Score 都回答同一个等级，用来检验分数换算"""

    name = "fixed"

    def __init__(self, level: float, confidence: float = 0.9):
        self.level = level
        self.confidence = confidence
        self.states = []

    async def ask(self, state, questions):
        self.states.append(state)
        return Answers(
            scores={k: ScoreAnswer(score=self.level, probabilities={}, confidence=self.confidence) for k in questions},
            judge="fixed",
            input_tokens=100,
        )


def test_load_and_stage_problem(tmp_path):
    bench = _make_bench(tmp_path)
    problem = load_problem(bench, "2024_C")
    staged = stage_datasets(problem, tmp_path / "ws")

    assert staged == ["data/matches.csv"]
    assert (tmp_path / "ws" / "data" / "matches.csv").exists()
    prompt = build_task_prompt(problem, staged)
    assert "Model momentum." in prompt
    assert "data/matches.csv" in prompt
    assert "point by point data" in prompt


def test_file_evaluation_reads_actual_artifacts_not_registered_numbers(tmp_path):
    from services.file_services.workspace_fs import WorkspaceFS
    ws = _make_workspace(tmp_path)
    fs = WorkspaceFS(str(ws))
    fs.write_text('outputs/actual.csv', 'mean\n4.0\n')
    fs.register_file('outputs/actual.csv', kind='data', source_run='run_1', visibility='user')
    assert artifact_files(ws)['rate'] == 1
    (ws/'outputs/actual.csv').write_text('')
    checked=artifact_files(ws)
    assert checked['rate'] == 0 and checked['failures'][0]['path']=='outputs/actual.csv'


def test_score_problem_and_summary(tmp_path):
    bench = _make_bench(tmp_path)
    problem = load_problem(bench, "2024_C")
    out = tmp_path / "out"
    _make_workspace(out / "2024_C")
    (out / "2024_C" / "run.json").write_text(json.dumps({
        "paper_exists": True, "error": None, "seconds": 12.0, "usage": {"total_tokens": 5000},
    }), encoding="utf-8")

    judge = FixedJudge(level=2.0, confidence=0.3)
    score = asyncio.run(score_problem(problem, out, judge))
    assert score["judge"]["overall"] == 5.5
    assert set(score["judge"]["dimensions"]) == {d.key for d in DIMENSIONS}
    assert score["judge"]["input_tokens"] == 100 * len(DIMENSIONS)
    assert len(score["judge"]["needs_review"]) == 8
    assert score["code_runs"] == {"total": 3, "success": 2, "error": 1, "success_rate": 0.667}

    summary = summarize(out, ["2024_C"])
    assert summary["mean"]["overall"] == 5.5
    assert summary["mean"]["total_tokens"] == 5000
    assert "| 2024_C | 5.50 |" in (out / "summary.md").read_text(encoding="utf-8")
