"""Ordered, durable assistant replies. No second execution store.

Text is checkpointed at most every 500 ms (or 4 KiB), and before every semantic
boundary. Only committed deltas are published. A hard process loss can discard
at most the unpublished text buffer. Execution hooks are synchronous: they run
at the durable activity boundary, never through deferred invalidation events.
"""
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4
import json
import re
import sqlite3
from contextlib import closing

TIMELINE = ContextVar('paperagent_conversation_timeline', default=None)
RESEARCH_SOURCE_REFS = {'research:feedback', 'research:consistency'}


def execution_boundary(root, research_id, kind, item):
    writer = TIMELINE.get()
    if writer is not None:
        writer.execution(root, research_id, kind, item)


def safe_file(root, value):
    # Only public workspace products; never expose internal DBs, config or secrets.
    if not isinstance(value, str) or '\\' in value: return None
    path = Path(value)
    public_internal = value in {'.system/validation.json', '.system/consistency.json'}
    if path.is_absolute() or (not public_internal and any(p.startswith('.') or p in {'..', 'node_modules'} for p in path.parts)): return None
    target = (Path(root) / path).resolve()
    if not target.is_relative_to(Path(root).resolve()) or not target.is_file(): return None
    if path.suffix.lower() not in {'.md','.txt','.log','.csv','.json','.png','.jpg','.jpeg','.svg','.pdf','.docx','.tex','.bib','.py','.xlsx'}: return None
    return path.as_posix()



def safe_diagnostic(root, value, limit=400):
    """Bound existing error_detail output; remove secrets, prompt dumps and paths.

    Activity exceptions already use review_cache.error_detail (validation errors
    omit input values). Credential patterns extend supervised.diagnostic's rules
    to quoted config values; no raw input/output/config object is projected.
    """
    if not isinstance(value, str): return ''
    text = value[:4000]
    if re.search(r'(?i)(?:system[_ -]?prompt|messages|input_value|api[_-]?key|password)[\"\']?\s*[:=]\s*[\[{]', text):
        return '错误包含内部请求数据；请检查运行状态或执行输出。'
    # Tracebacks can echo source lines containing credentials: retain the final
    # exception summary rather than stack frames or echoed code.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    exceptions = [line for line in lines if re.match(r'^[A-Za-z_][\w.]*(?:Error|Exception):', line)]
    if exceptions: text = exceptions[-1]
    else: text = '；'.join(line for line in lines[:3] if not line.startswith(('Traceback', 'File "')))
    text = re.sub(r'(?i)https?://[^\s<>\"\']+', '[服务地址]', text)
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[已隐藏]', text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|authorization|sa[_-]?token|password|secret|credential)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", r'\1[已隐藏]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[已隐藏]', text)
    text = text.replace(str(Path(root).resolve()) + '/', '')
    text = re.sub(r'(?<![\w.])(?:[A-Za-z]:[\\/]|/)[^\s<>\"\']+', '[工作区外路径]', text)
    text = re.sub(r'\.system/[^\s<>\"\']+', '[内部记录]', text)
    text = re.sub(r'(?i)(?:system[_ -]?prompt|prompt|messages)\s*[:=].*', '[内部请求已隐藏]', text)
    return text[:limit]


def operation_detail(root, item, model):
    # Describe recorded inputs and receipts, never invent progress from elapsed time.
    if model:return ''
    capability = item.get('capability')
    inputs, output = item.get('inputs') or {}, item.get('output') or {}
    if capability == 'read_file':
        total = output.get('total_chars')
        if total is not None:return f"已读取 {len(output.get('excerpt', ''))} 个字符，文件共 {total} 个字符。"
        start, size = inputs.get('start'), inputs.get('limit')
        if type(start) is int and type(size) is int:return f'从第 {start} 个字符起读取，最多 {size} 个字符。'
    if capability == 'run_python' and isinstance(inputs.get('code'), str):
        return f"本次代码共 {len(inputs['code'].splitlines())} 行。"
    if capability == 'experience.candidates' and 'candidates' in output:
        return f"已更新经验索引，共 {output['candidates']} 项。"
    if capability == 'record_results' and 'artifact_refs' in output:
        return f"已登记 {len(output['artifact_refs'])} 项结果。" + (f"另有 {output['rejected']} 项未通过核对。" if output.get('rejected') else '')
    if capability == 'record_figure':return output.get('caption', '')
    if capability == 'writing.save_source' and 'files' in output:
        return '已保存：' + '、'.join(output['files'])
    if capability == 'literature.collect':return safe_diagnostic(root, inputs.get('query', ''))
    return ''


def execution_projection(root, research_id, kind, item):
    from ai_system.research.views import LABELS
    model = kind == 'model'
    identity = 'model:' + str(item['sequence']) if model else item['id']
    row = {key: item[key] for key in ('action_id','observation_id','question_id','experiment_id','parent_call_id',
            'finished_at','run_id','input_tokens','output_tokens','subject') if item.get(key) is not None}
    row.update(id=identity, research_id=research_id, kind=kind,
               capability=item.get('capability'),
               label=(item.get('step') if item.get('step') in LABELS.values() else
                      {'observe':'判断下一步','title':'整理研究标题','amendment':'更新研究要求','abstract':'撰写摘要','references':'整理参考文献','检查实验报告':'检查实验报告'}.get(item.get('label'), '生成内容'))
                     if model else LABELS.get(item.get('capability'), '执行步骤'),
               status=item.get('state' if model else 'status', 'unknown'),
               started_at=item.get('sent_at' if model else 'started_at'))
    row['detail'] = operation_detail(root, item, model)
    if item.get('error_type') or row['status'] in {'failed','error'}:
        reason = safe_diagnostic(root, item.get('message')) if item.get('error_type') else ''
        row['message'] = reason or ('模型请求未完成，请检查连接或请求用量。' if model else '未记录具体错误；已有产物保留，可查看执行输出。')
    inputs = item.get('inputs') or {}
    file = safe_file(root, item.get('display_file') or (inputs.get('path') if isinstance(inputs, dict) else None))
    if file:
        row['file'] = file
        row['label'] += ' · ' + Path(file).name
    run_id = item.get('run_id')
    if isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        from services.file_services.workspace_fs import WorkspaceFS
        info = WorkspaceFS(Path(root)).get_run_info(run_id) or {}
        row['output_file'] = safe_file(root, info.get('stdout'))
        row['script_file'] = safe_file(root, info.get('input'))
        row['error_file'] = safe_file(root, info.get('stderr'))
    output = item.get('output') or {}
    refs = output.get('artifact_refs', []) if isinstance(output, dict) else []
    row['result_ids'] = [r for r in refs if isinstance(r, str) and re.fullmatch(r'[RF]\d+', r)]
    # Raw requests and inherited planning purposes are not user-facing progress.
    return row


def public_block(root, block):
    kind, content = block.get('type', 'unknown'), block.get('content')
    if kind == 'main_agent_error':
        content = safe_diagnostic(root, content if isinstance(content,str) else (content or {}).get('message')) or '操作失败，未记录具体原因。'
    elif kind in {'call_code_agent', 'call_writing_agent', 'word_tool_call'}:
        content = '操作记录已保存。'
    if kind == 'research_context' and isinstance(content, dict):
        content = {**{k: content[k] for k in ('schema_version','work_id','subproblem_id','truncated') if k in content},
                   'research_id': content.get('research_id'),
                   'files': [p for p in content.get('files', []) if isinstance(p, str) and (p in RESEARCH_SOURCE_REFS or safe_file(root, p))],
                   'result_ids': [r for r in content.get('result_ids', []) if isinstance(r,str) and re.fullmatch(r'[RF]\d+',r)]}
    elif kind == 'paper_phase' and isinstance(content, dict):
        content = {key: content[key] for key in ('stage', 'status', 'note') if isinstance(content.get(key), str)}
        if 'note' in content:content['note'] = safe_diagnostic(root, content['note'])
    elif not isinstance(content, str):
        content = {key: content[key] for key in ('message','summary','reason','status','ok','success')
                   if isinstance(content,dict) and isinstance(content.get(key), (str,bool))}
    return {'type': kind, 'content': content}


class ReplyWriter:
    def __init__(self, history, work_id, chat_task_id, root, publish):
        self.history, self.work_id, self.root, self.publish = history, work_id, Path(root), publish
        self.lock = RLock()
        self.dirty = {}
        self.pending_chars = 0
        self.reply = {'id': uuid4().hex, 'role': 'assistant', 'content': '',
                      'timestamp': datetime.now().isoformat(), 'message_type': 'timeline',
                      'segments': [], 'json_blocks': [],
                      'metadata': {'system_type': 'brain', 'chat_task_id': chat_task_id,
                                   'reply_id': None, 'revision': 0, 'status': 'running'}}
        self.reply['metadata']['reply_id'] = self.reply['id']
        self.flush(force=True)

    @property
    def id(self): return self.reply['id']

    def text(self, text):
        with self.lock:
            if self.reply['metadata']['status'] != 'running': return
            rows = self.reply['segments']
            if not rows or rows[-1]['type'] != 'text':
                rows.append({'id': uuid4().hex, 'type': 'text', 'text': ''})
            rows[-1]['text'] += text
            self.reply['content'] += text
            self.dirty[rows[-1]['id']] = rows[-1]
            self.pending_chars += len(text)
            if self.pending_chars >= 4096: self.flush()

    def block(self, block):
        with self.lock:
            if self.reply['metadata']['status'] != 'running': return
            block = public_block(self.root, block)
            row = {'id': uuid4().hex, 'type': 'event', 'block': block}
            self.reply['json_blocks'].append(block)
            if block['type'] == 'main_agent_complete' and isinstance(block['content'], str):
                self.reply['content'] += ('\n\n' if self.reply['content'] else '') + block['content']
            self.reply['segments'].append(row)
            self.dirty[row['id']] = row
            self.flush()

    def execution(self, root, research_id, kind, item):
        with self.lock:
            if Path(root).resolve() != self.root.resolve() or self.reply['metadata']['status'] != 'running': return
            detail = execution_projection(root, research_id, kind, item)
            ref = {'research_id': research_id, 'kind': kind, 'id': detail['id']}
            rows = self.reply['segments']
            row = next((r for r in rows if r.get('ref') == ref), None)
            if row is None:
                row = {'id': uuid4().hex, 'type': 'execution', 'ref': ref}
                rows.append(row)
            row['execution'] = detail
            self.dirty[row['id']] = row
            self.flush()

    def flush(self, force=False):
        with self.lock:
            if not self.dirty and not force: return
            self.reply['metadata']['revision'] += 1
            self.history.upsert_reply(self.work_id, self.reply)
            update = {'reply_id': self.id, 'chat_task_id': self.reply['metadata']['chat_task_id'],
                      'revision': self.reply['metadata']['revision'], 'status': self.reply['metadata']['status'],
                      'segments': deepcopy(list(self.dirty.values()))}
            self.dirty.clear()
            self.pending_chars = 0
            self.publish(update)

    def finish(self, status, notice=None):
        with self.lock:
            if self.reply['metadata']['status'] != 'running': return
            if notice: self.text('\n\n' + notice)
            for row in self.reply['segments']:
                detail = row.get('execution')
                if detail and not detail.get('finished_at') and detail['status'] in {'running','sent','http_response'}:
                    detail['status'] = 'interrupted'
                    self.dirty[row['id']] = row
            self.reply['metadata']['status'] = status
            self.flush(force=True)


def resolve_executions(root, reply, ids):
    """Resolve only refs actually in this authorized reply, including old journal rows."""
    root = Path(root)
    path = root / '.system/research.sqlite'
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()): return []
    references = {r['id']: r for r in reply.get('segments', []) if r.get('type') == 'execution'}
    result = []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        for segment_id in dict.fromkeys(ids):
            segment = references.get(segment_id)
            if not segment: continue
            ref = segment['ref']
            if ref['kind'] == 'model':
                raw = db.execute('SELECT payload FROM model_requests WHERE sequence=? AND json_extract(payload,\'$.research_id\')=?',
                                 (int(ref['id'].split(':')[1]), ref['research_id'])).fetchone()
            else:
                raw = db.execute("SELECT payload FROM journal WHERE research_id=? AND kind IN ('activity_started','activity_finished') AND json_extract(payload,'$.id')=? ORDER BY seq DESC LIMIT 1",
                                 (ref['research_id'], ref['id'])).fetchone()
            detail = execution_projection(root, ref['research_id'], ref['kind'], json.loads(raw[0])) if raw else segment.get('execution')
            if detail:
                if reply.get('metadata', {}).get('status') != 'running' and not detail.get('finished_at'):
                    detail['status'] = 'interrupted'
                result.append({'segment_id': segment_id, **detail})
    return result
