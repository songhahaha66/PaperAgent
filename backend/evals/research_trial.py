"""One bounded research trial and a separate, clean-sandbox replay.

Run from backend: python -m evals.research_trial --out eval_runs/<trial>
The output directory must contain inputs/ and prompt.txt. Credentials are read
from the selected existing connection and never written into trial metadata.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

BACKEND = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup(out):
    from dotenv import load_dotenv
    load_dotenv(BACKEND / '.env')
    os.environ['PA_DATA_PATH'] = str(out / 'private-state')
    os.environ['PAPERAGENT_SANDBOX_APPLICATION'] = 'paperagent-eval'
    os.environ.setdefault('PAPERAGENT_SANDBOX_CONFIG', str(BACKEND / '.sandbox.env'))


async def generate(args, out):
    from database.database import SessionLocal
    from models.models import ModelConfig
    from ai_system.core_handlers.llm_providers import create_llm_from_model_config
    from ai_system.agent.agent import PaperAgent
    from ai_system.research.runtime import ResearchRuntime
    from langchain_core.callbacks import get_usage_metadata_callback
    from .runner import _sum_usage

    protocol_path = out / 'protocol.json'
    previous_protocol = json.loads(protocol_path.read_text()) if args.resume else None
    if protocol_path.exists() and not args.resume:
        raise ValueError('该试跑已开始；请使用新的 out，保留已有记录')
    workspace = out / 'workspace'
    previous = json.loads((out / 'run.json').read_text()) if args.resume else {}
    if not args.resume:
        shutil.copytree(out / 'inputs', workspace / 'attachment')
    prompt = (out / 'prompt.txt').read_text(encoding='utf-8')
    runtime = ResearchRuntime(workspace)
    policy = runtime.policy() if args.resume else {
        'request_limit': 80, 'output_token_limit': 90000,
        'total_token_limit': 2000000, 'timeout_seconds': 300}
    if args.request_timeout is not None:
        policy['timeout_seconds'] = args.request_timeout
    if args.total_token_limit is not None:
        policy['total_token_limit'] = args.total_token_limit
    if not args.resume or args.request_timeout is not None or args.total_token_limit is not None:
        runtime.configure(policy)
    generation = previous_protocol['generation'] if args.resume else {
        'max_output_tokens': 16000, 'context_window': 128000,
        'reasoning_effort': 'medium', 'image_inputs': True}
    with SessionLocal() as db:
        source = db.get(ModelConfig, args.config_id)
        if source is None:
            raise ValueError('模型连接不存在')
        connection = SimpleNamespace(id=None, provider=source.provider,
            model_id=args.model, api_key=source.api_key, base_url=source.base_url,
            is_active=True, thinking_enabled=source.thinking_enabled,
            timeout_seconds=policy['timeout_seconds'], generation_settings=generation)
    model = create_llm_from_model_config(connection, streaming=False, temperature=None)
    trial_id = (previous_protocol.get('trial_id') or previous_protocol['work_id']) if args.resume else uuid.uuid4().hex
    diff = subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=BACKEND)
    protocol = {'model': args.model, 'provider': connection.provider,
        'source_config_id': args.config_id, 'api_host': urlsplit(connection.base_url).hostname,
        'generation': generation, 'budget': policy, 'wall_seconds': args.wall_seconds,
        'trial_id': trial_id, 'work_id': None, 'output_mode': 'latex', 'prices': None,
        'engine': 'ai_system.agent.loop.AgentLoop',
        'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=BACKEND, text=True).strip(),
        'harness_sha256': digest(Path(__file__)),
        'tracked_diff_sha256': hashlib.sha256(diff).hexdigest(),
        'source_sha256': {str(p.relative_to(BACKEND)): digest(p)
                          for p in sorted((BACKEND / 'ai_system').rglob('*')) if p.suffix in {'.py','.md'}},
        'input_sha256': {str(p.relative_to(out / 'inputs')): digest(p)
                         for p in sorted((out / 'inputs').rglob('*')) if p.is_file()},
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
        'scope': 'one research-engine trial; no product intent-classification or pi baseline'}
    if not args.resume:
        save(protocol_path, protocol)
    started = time.time()
    segments = previous.get('segments', [])
    if previous:
        segments = [*segments, {k: previous[k] for k in ('state','started_at','segment_seconds','usage_by_model') if k in previous}]
        save(out / f'continuation-{len(segments)}.json', {**protocol, 'resuming_after': previous['state']})
    record = {'state': 'running', 'started_at': started, 'model': args.model, 'segments': segments}
    save(out / 'run.json', record)
    # Evaluations have no product DB project. A fictitious work_id would be
    # treated as a deleted project by the production sandbox sweeper.
    agent = PaperAgent(model, workspace_dir=str(workspace),
                            output_mode='latex', experience_scope='trial:' + trial_id)
    with get_usage_metadata_callback() as usage:
        try:
            async with asyncio.timeout(args.wall_seconds - previous.get('seconds', 0)):
                record['final_message'] = await agent.resume_failed() if args.resume else await agent.run(prompt)
            record['state'] = 'finished'
            record['agent_result'] = agent.last_result
            if record['final_message'].startswith('任务执行失败'):
                record['state'] = 'failed'
        except TimeoutError:
            record.update(state='timeout', error='trial wall-clock budget reached')
        except asyncio.CancelledError:
            record.update(state='interrupted')
            raise
        except Exception as exc:
            record.update(state='failed', error=f'{type(exc).__name__}: {exc}')
        finally:
            segment_seconds = round(time.time() - started, 3)
            segment_usage = _sum_usage(usage.usage_metadata)
            import sys
            record['framework_modules_loaded']=[name for name in sys.modules if name=='langgraph' or name.startswith('langgraph.')]
            record.update(seconds=round(segment_seconds + previous.get('seconds', 0), 3),
                          segment_seconds=segment_seconds,
                          usage={k: v + previous.get('usage', {}).get(k, 0) for k,v in segment_usage.items()},
                          usage_by_model=usage.usage_metadata, runtime=runtime.status())
            save(out / 'run.json', record)
    print(json.dumps({k: record[k] for k in ('state', 'seconds', 'usage')}, ensure_ascii=False), flush=True)


def compare_numbers(before, after, path='$'):
    if isinstance(before, dict) and isinstance(after, dict):
        if before.keys() != after.keys():
            return [path + ': keys changed']
        return [issue for key in before for issue in compare_numbers(before[key], after[key], path + '.' + key)]
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [path + ': length changed']
        return [issue for i, (a, b) in enumerate(zip(before, after))
                for issue in compare_numbers(a, b, f'{path}[{i}]')]
    if type(before) in (int, float) and type(after) in (int, float):
        import math
        return [] if math.isclose(before, after, abs_tol=1e-6, rel_tol=1e-6) else [path + ': numeric mismatch']
    return [] if before == after else [path + ': value changed']


async def replay(out):
    from ai_system.sandbox.factory import create_kernel
    source = out / 'workspace'
    target = out / 'replay'
    target.mkdir(exist_ok=False)
    shutil.copytree(out / 'inputs', target / 'attachment')
    for name in ('code', 'config', 'data'):
        if (source / name).is_dir():
            shutil.copytree(source / name, target / name)
    inputs = {str(p.relative_to(target)): digest(p) for p in target.rglob('*') if p.is_file()}
    kernel = create_kernel(target, timeout=180)
    started = time.time()
    try:
        result = await kernel.execute("import runpy, sys\nsys.path.insert(0, 'code')\nsys.argv=['code/reproduce.py']\nrunpy.run_path('code/reproduce.py',run_name='__main__')")
        record = {'ok': result.ok, 'error': result.error, 'stdout': result.stdout,
                  'run_id': result.run_id, 'seconds': round(time.time()-started, 3),
                  'staged_sha256': inputs, 'tolerance': {'absolute': 1e-6, 'relative': 1e-6},
                  'scope': 'clean-sandbox repeatability; scientific correctness checked separately'}
        if result.ok:
            original = json.loads((source / 'outputs/answer.json').read_text())
            actual = json.loads((target / 'outputs/answer.json').read_text())
            record['differences'] = compare_numbers(original, actual)
            record['matches'] = not record['differences']
        record['execution'] = kernel.fs.get_run_info(result.run_id)
        save(out / 'replay.json', record)
        print(json.dumps({k: record[k] for k in ('ok', 'seconds', 'matches') if k in record}), flush=True)
    finally:
        await kernel.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--config-id', type=int)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--wall-seconds', type=int, default=2400)
    parser.add_argument('--request-timeout', type=float)
    parser.add_argument('--total-token-limit', type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--replay', action='store_true')
    mode.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    if args.resume:
        protocol=json.loads((out/'protocol.json').read_text())
        args.config_id=protocol['source_config_id'];args.model=protocol['model'];args.wall_seconds=protocol['wall_seconds']
    elif not args.replay and args.config_id is None:
        parser.error('--config-id is required for a new trial')
    setup(out)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    asyncio.run(replay(out) if args.replay else generate(args, out))


if __name__ == '__main__':
    main()
