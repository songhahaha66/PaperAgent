"""Views of direct execution receipts; no experiment or scientific verdict."""
from services.file_services.workspace_fs import WorkspaceFS


def direct_runs(store, fs=None, question_id=None):
    fs = fs or WorkspaceFS(str(store.root))
    return {rid: info for rid, info in fs._manifest.get('runs', {}).items()
            if info.get('research_id') == store.research_id and info.get('scope') == 'research'
            and (question_id is None or info.get('question_id') == question_id)}


def run_result(ctx, run_id, observation=None):
    info = ctx.fs.get_run_info(run_id) or {}
    status = ('interrupted' if info.get('delivery_error') or info.get('publication_error') or info.get('failure_kind') == 'transport_unknown'
              or info.get('status') not in {'success', 'error', 'cancelled', 'timeout'} else
              'succeeded' if info['status'] == 'success' else
              'cancelled' if info['status'] == 'cancelled' else 'failed')
    if observation is None:
        observation = ctx.fs.read_text(info['stdout'])[-6000:] if info.get('stdout') and ctx.fs.exists(info['stdout']) else ''
    return {'run_id': run_id, 'question_id': info.get('question_id'), 'execution_status': status,
            'failure_stage':info.get('failure_stage'),'submission_state':info.get('submission_state'),
            'compute_state':info.get('compute_state'),'delivery_state':info.get('delivery_state'),
            'runtime_epoch':info.get('runtime_epoch'),'context_restored':info.get('context_restored'),
            'execution_mode':info.get('execution_mode'),'exit_code':info.get('exit_code'),
            'duration_seconds':info.get('duration_seconds'),
            'observation': observation, 'error': info.get('error'), 'failure_kind': info.get('failure_kind'),
            'delivery_error': info.get('delivery_error') or info.get('publication_error'),
            'runtime_note': ('执行环境已中断，旧Python变量不可依赖；读取保存的代码和文件恢复，不重放已完成计算。'
                             if info.get('submission_state')!='not_submitted' and info.get('failure_kind') in {'transport_unknown','timeout','cancelled','resource'} else None),
            'input_path': info.get('input'), 'stdout_path': info.get('stdout'),
            'files': [path for path, meta in ctx.fs._manifest.get('files', {}).items()
                      if meta.get('source_run') == run_id and meta.get('visibility') == 'user']}


def run_context(store, question_id=None):
    fs = WorkspaceFS(str(store.root))
    return [{'run_id': rid, 'question_id': info.get('question_id'), 'title': info.get('title', ''),
             'status': info.get('status'), 'error': info.get('error'),
             'input_path': info.get('input'), 'stdout_path': info.get('stdout'),
             'files': [p for p, meta in fs._manifest.get('files', {}).items()
                       if meta.get('source_run') == rid and meta.get('visibility') == 'user']}
            for rid, info in direct_runs(store, fs, question_id).items()]
