"""Writers receive actual references and adopted premise versions without padding requirements."""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.modules.writing.sections import section_request, STYLE
from helpers.graph import FakeModel, _analysis
from helpers.submissions import seed
from research_tools_helpers import record_writing_inputs


def test_saved_freeform_outline_can_resume_writing_with_real_results(tmp_path):
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    result=seed(ctx,[],name='MAE')
    base=record_writing_inputs(ctx,'比较销量预测方法',_analysis().model_dump()['subproblems'])
    # Shape persisted by the planner in fdb9f007, before basis had a schema.
    for key in ('modeling','validation','limitations'):
        state={**base,
               'outline':[{'key':key,'title':'预测结果','purpose':'说明计算结果与局限',
                           'basis':[{'statement':'验证误差','artifact_ref':result.id}]}]}
        prompt=section_request(key,'预测结果',state,ctx)
        assert '说明计算结果与局限' in prompt and '{{R:'+result.id+'}}' in prompt


def test_writing_context_has_only_real_scoped_references_and_no_phantom_figure(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    own = seed(ctx, [], name='answer')
    unrelated = seed(ctx, [], sid='Q2', name='unrelated', value='12')
    state = record_writing_inputs(ctx,'研究给定计算结果',_analysis().model_dump()['subproblems'])
    request = section_request('solve:Q1', '求解', state, ctx)
    assert '{{R:' + own.id + '}}' in request
    assert '{{R:' + unrelated.id + '}}' not in request
    assert '本节没有可用图表' in request
    assert '{{F:F1}}' not in STYLE + request
    run = ctx.fs.create_run()
    ctx.fs.save_run_stdout(run, 'figure ready')
    ctx.fs.finish_run(run, 'success')
    (ctx.fs.run_dir(run) / 'artifacts/plot.png').write_bytes(b'png')
    figure = ctx.registry.record_figure('Q1', run, 'plot.png', '实际图表')
    from research_tools_helpers import continue_experiment
    from ai_system.research.models import WorkerResult
    from dataclasses import asdict
    continue_experiment(ctx.research,'Q1','登记已有实际图表')
    ctx.research.commit(WorkerResult(order=ctx.research.claim('Q1'),execution_status='succeeded',acceptance='accepted',
        report={'status':'completed','summary':'已登记实际图表'},results=[asdict(figure)]))
    request = section_request('solve:Q1', '求解', state, ctx)
    assert '{{F:' + figure.id + '}}' in request and '本节没有可用图表' not in request
