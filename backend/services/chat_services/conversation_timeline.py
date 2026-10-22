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



OPERATION_DETAILS = {
    'research.observe': '核对已有证据与未完成事项，选择下一步研究操作。',
    'research.update_question': '更新问题范围与研究要求。',
    'research.read_question': '读取问题、已有判断和关联证据。',
    'research.schedule_experiment': '为当前研究问题安排实验。',
    'research.continue_experiment': '从已有实验结果继续处理未完成事项。',
    'research.update_answer': '依据已登记结果更新研究判断与局限。',
    'research.read_artifact': '读取已保存的研究证据。',
    'research.ask_user': '收集继续研究所必需的信息。',
    'research.pause': '保存当前进度并暂停研究。',
    'research.deliver': '核对研究结论并准备交付结果。',
    'research.tool_error': '检查未完成的研究能力调用。',
    'experiments.inspect': '检查实验输入材料与已有数据。',
    'experiments.execute': '执行当前实验并登记可核对的产物。',
    'read_file': '读取工作区已有文件的指定内容范围。',
    'run_python': '在持久 Python 内核中运行实验代码。',
    'literature.collect': '检索并保存研究所需的文献证据。',
    'check_interpretation': '核查实验解释与已登记结果是否一致。',
    'research.check_interpretation': '核查实验解释与已登记结果是否一致。',
    'research.revise_interpretation': '修订实验解释并保留已有执行结果。',
    'writing.write': '根据已有研究依据更新论文正文。',
    'writing.validate': '检查论文结构、引用与结果一致性。',
    'writing.repair': '修订论文中已定位的问题。',
    'writing.render': '渲染当前论文源码并检查排版。',
    'writing.export': '导出当前论文的可交付文档。',
}


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
    if model:
        known_steps = {'检查实验报告': '检查实验报告', 'observe': '选择下一步研究操作',
                       'amendment': '更新研究要求', 'abstract': '撰写摘要', 'references': '整理参考文献'}
        step = item.get('step') or item.get('label')
        return known_steps.get(step, '等待并接收当前步骤的模型响应。')
    capability = item.get('capability')
    detail = OPERATION_DETAILS.get(capability, '执行当前研究能力并保存结果。')
    inputs = item.get('inputs') or {}
    if isinstance(inputs, dict):
        if capability == 'read_file':
            start, size = inputs.get('start'), inputs.get('limit')
            if type(start) is int and type(size) is int and start >= 0 and 1 <= size <= 24000:
                detail += f' 从第 {start} 个字符起读取，最多 {size} 个字符。'
        if capability == 'run_python' and isinstance(inputs.get('code'), str):
            detail += f" 本次代码共 {len(inputs['code'].splitlines())} 行。"
    return detail

def execution_projection(root, research_id, kind, item):
    from ai_system.research.views import LABELS
    model = kind == 'model'
    identity = 'model:' + str(item['sequence']) if model else item['id']
    row = {key: item[key] for key in ('action_id','observation_id','question_id','experiment_id','parent_call_id',
            'finished_at','run_id','input_tokens','output_tokens') if item.get(key) is not None}
    row.update(id=identity, research_id=research_id, kind=kind,
               label='模型请求' if model else LABELS.get(item.get('capability'), '研究操作'),
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
    # Intentionally omit raw inputs/output, prompts, host, purpose and labels.
    return row


def public_block(root, block):
    kind, content = block.get('type', 'unknown'), block.get('content')
    if kind in {'call_code_agent', 'call_writing_agent', 'word_tool_call', 'main_agent_error'}:
        content = '操作未完成，已有记录已保存。' if kind == 'main_agent_error' else '操作记录已保存。'
    if kind == 'research_context' and isinstance(content, dict):
        content = {**{k: content[k] for k in ('schema_version','work_id','subproblem_id','truncated') if k in content},
                   'research_id': content.get('research_id'),
                   'files': [p for p in content.get('files', []) if isinstance(p, str) and (p in RESEARCH_SOURCE_REFS or safe_file(root, p))],
                   'result_ids': [r for r in content.get('result_ids', []) if isinstance(r,str) and re.fullmatch(r'[RF]\d+',r)]}
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
