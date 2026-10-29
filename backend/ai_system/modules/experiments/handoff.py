"""Auditable formal handoff of the exact frozen estimator, separate from model prose."""
from services.file_services.workspace_layout import workspace_path
import hashlib
import json
import numpy as np
from .supervised import prepare,root_store,current
from ai_system.modules.experiments.store import ExperimentStore
from ...research.store import digest


async def publish_comparison(ctx,branch,record,run_log):
    """Publish all measured candidates, without refitting or promoting one.

    Frozen prediction artifacts and the common protocol are the source; candidate
    stdout and model estimates are never used as measurements.
    """
    from .supervised import metric_value,baseline_value
    # This path publishes saved predictions, not a live-estimator handoff.
    # Close every candidate before constructing the publication kernel.
    for row in record['branches']:
        if row.get('kernel_key'):
            await ctx.kernels.release(row['kernel_key'])
    from ai_system.graph.state import SolveReport
    if record.get('protocol_error') or not record.get('protocol') or not current(ctx,record,branch):
        return {'status':'failed','error':record['reason'],'summary':record['reason'],'reviews':[]}
    frozen,_,truth,_,train,_=prepare(ctx.workspace,record['protocol'])
    if digest(frozen)!=record['protocol_hash']:raise ValueError('比较输入或评价条件已改变，不能登记旧实验')
    source=(root_store(ctx).root/'experiments'/record['owner_attempt']/'workspace'
            if record.get('owner_attempt') else ctx.workspace)
    if source!=ctx.workspace:
        import shutil
        manifest=json.loads((workspace_path(source, 'manifest.json')).read_text())
        for row in record['branches']:
            for rid in row.get('run_ids',[]):
                shutil.copytree(workspace_path(source, 'runs')/rid,workspace_path(ctx.workspace, 'runs')/rid,dirs_exist_ok=True)
                ctx.fs._manifest.setdefault('runs',{})[rid]=manifest['runs'][rid]
        ctx.fs._save_manifest()
    for row in record['branches']:
        run_log.extend({'run_id':rid,'code':row.get('code','')+'\n'+row.get('driver_code',''),
                        'ok':row['status'] in {'valid','control_inconclusive'}} for rid in row.get('run_ids',[]))
    values={};measurements=[];predictions={}
    for row in record['branches']:
        if row['status'] not in {'valid','control_inconclusive'}:continue
        raw=(source/'outputs/intermediate/comparisons'/record['id']/f"predictions-{row['index']}.json").read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['prediction_hash']:raise ValueError('冻结预测产物已改变')
        prediction=json.loads(raw)['prediction']
        predictions[row['name']]=prediction
        metric=metric_value(frozen['metric'],truth,prediction)
        if not np.isclose(metric,row['metric'],rtol=1e-9,atol=1e-10):raise ValueError('冻结预测与登记指标不一致')
        values[row['name']+' · 验证'+frozen['metric']]=metric
        measurements.append({'index':row['index'],'name':row['name'],'metric':metric,'prediction_hash':row['prediction_hash'],'run_ids':row['run_ids']})
    if not measurements:
        return {'status':'failed','error':record['reason'],'summary':record['reason'],'reviews':[]}
    values['共同基线 · '+frozen['metric']]=baseline_value(frozen['metric'],train,truth)
    # Print independently recomputed values through the existing execution and
    # registry boundary, preserving normal run provenance and source checks.
    code='import json\nfor name,value in '+repr(values)+'.items(): print(name,value)\n'
    code+='import matplotlib.pyplot as plt\nplt.figure()\nplt.plot('+repr(truth.tolist())+",label='观测值')\n"
    for name,prediction in predictions.items():code+='plt.plot('+repr(prediction)+',label='+repr(name)+')\n'
    code+="plt.title('共同验证样本上的候选预测')\nplt.xlabel('验证样本顺序')\nplt.ylabel('目标值')\nplt.legend()\nplt.tight_layout()\n"
    result=await ctx.kernels.get(branch['subproblem']['id']).execute(code)
    run_log.append({'run_id':result.run_id,'code':code,'ok':result.ok})
    if not result.ok or result.delivery_error:raise ValueError('比较结果登记执行或归档未完成')
    proof={'attempt_id':ctx.experience_order.attempt_id if ctx.experience_order else None,
           'protocol_hash':record['protocol_hash'],'run_id':result.run_id,
           'values':values,'measurements':measurements}
    record.setdefault('measurements',[]).append(proof)
    ExperimentStore(root_store(ctx)).save(record)
    await ctx.events.stage('候选实验已完成：'+record['reason']+'；实测结果交回研究控制器。')
    report=SolveReport(summary=record['reason'],approach=branch['design']['chosen']['name'],key_findings=[],
        limitations=['结论仅适用于本次共同数据范围与评价条件；执行失败不代表方法无效。'],
        validation='从冻结预测重算共同指标并核对预测文件哈希。').model_dump()
    return {**report,'status':'completed','comparison_id':record['id'],'reviews':[],
            'measurements':values,'runs':[{'run_id':r['run_id'],'ok':r['ok']} for r in run_log],
            'files':result.files}
