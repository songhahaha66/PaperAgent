"""Writers receive actual references and adopted premise versions without padding requirements."""
from helpers.legacy_registry import value as legacy_value, figure as legacy_figure, claims as legacy_claims

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.research.planner import decision_context
import json
from helpers.graph import FakeModel, _analysis
from helpers.submissions import seed
from research_tools_helpers import record_writing_inputs


def test_writing_context_has_only_real_scoped_references_and_no_phantom_figure(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    own = seed(ctx, [], name='answer')
    unrelated = seed(ctx, [], sid='Q2', name='unrelated', value='12')
    state = record_writing_inputs(ctx,'研究给定计算结果',_analysis().model_dump()['subproblems'])
    snapshot=ctx.research.snapshot()
    snapshot['action']={'question_id':snapshot['experiments']['Q1']['question_id']}
    request = json.dumps(decision_context(ctx.research,snapshot)[1],ensure_ascii=False)
    assert '{{R:' + own.id + '}}' in request
    assert '{{R:' + unrelated.id + '}}' not in request
    assert '{{F:F1}}' not in request
    run = ctx.fs.create_run()
    ctx.fs.save_run_stdout(run, 'figure ready')
    ctx.fs.finish_run(run, 'success')
    (ctx.fs.run_dir(run) / 'artifacts/plot.png').write_bytes(b'png')
    from ai_system.sandbox.evidence import seal_run
    seal_run(ctx.fs, run)
    figure = legacy_figure(ctx.registry,'Q1', run, 'plot.png', '实际图表')
    from research_tools_helpers import continue_experiment
    from ai_system.research.models import WorkerResult
    from dataclasses import asdict
    continue_experiment(ctx.research,'Q1','登记已有实际图表')
    ctx.research.commit(WorkerResult(order=ctx.research.claim('Q1'),execution_status='succeeded',acceptance='accepted',
        report={'status':'completed','summary':'已登记实际图表'},results=[asdict(figure)]))
    snapshot=ctx.research.snapshot()
    snapshot['action']={'question_id':snapshot['experiments']['Q1']['question_id']}
    request = json.dumps(decision_context(ctx.research,snapshot)[1],ensure_ascii=False)
    assert '{{F:' + figure.id + '}}' in request
