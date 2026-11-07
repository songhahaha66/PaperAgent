"""Public tool details. Never persist raw tool requests or model results as Items."""
from pathlib import Path
import re
from services.file_services.workspace_layout import workspace_path
RESEARCH_SOURCE_REFS = {'research:feedback', 'research:consistency'}

def safe_file(root, value, *, nonempty=False):
    # Only public workspace products; never expose internal DBs, config or secrets.
    if root is None or not isinstance(value, str) or '\\' in value: return None
    path = Path(value)
    public_internal = value in {'.system/validation.json', '.system/consistency.json'}
    if path.is_absolute() or (not public_internal and any(p.startswith('.') or p in {'..', 'node_modules'} for p in path.parts)): return None
    try:
        target = workspace_path(Path(root), value)
    except ValueError:
        return None
    if not target.is_file(): return None
    if nonempty and target.stat().st_size == 0: return None
    if path.suffix.lower() not in {'.md','.txt','.log','.csv','.json','.png','.jpg','.jpeg','.svg','.pdf','.docx','.tex','.bib','.py','.xlsx'}: return None
    return path.as_posix()



def safe_diagnostic(root, value, limit=400):
    """Bound existing error_detail output; remove secrets, prompt dumps and paths.

    Tool exceptions use review_cache.error_detail (validation errors
    omit input values). Credential patterns cover common authorization and token forms
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
    if root is not None:text = text.replace(str(Path(root).resolve()) + '/', '')
    text = re.sub(r'(?<![\w.])(?:[A-Za-z]:[\\/]|/)[^\s<>\"\']+', '[工作区外路径]', text)
    text = re.sub(r'\.system/[^\s<>\"\']+', '[内部记录]', text)
    text = re.sub(r'(?i)(?:system[_ -]?prompt|prompt|messages)\s*[:=].*', '[内部请求已隐藏]', text)
    return text[:limit]


def page_range(pages):
    if not isinstance(pages, list):return ''
    numbers = sorted({p for p in pages if type(p) is int and p > 0})[:4]
    groups = []
    for number in numbers:
        if groups and number == groups[-1][-1] + 1: groups[-1].append(number)
        else: groups.append([number])
    return '、'.join(str(g[0]) if len(g) == 1 else f'{g[0]}–{g[-1]}' for g in groups)


def operation_detail(root, item):
    # Describe recorded inputs and receipts, never invent progress from elapsed time.
    capability = item.get('tool_name')
    inputs, output = item.get('inputs') or {}, item.get('output') or {}
    if capability == 'writing.preview':
        pages = page_range(output.get('pages') or inputs.get('pages') or [])
        parts = [f'第 {pages} 页'] if pages else []
        if type(output.get('total_pages')) is int: parts.append(f"共 {output['total_pages']} 页")
        if inputs.get('historical'): parts.append('历史版本')
        if item.get('status') == 'running':
            stage = output.get('preview_stage')
            if stage == 'compiling': parts.append('正在编译 LaTeX')
            elif stage == 'reading': parts.append('正在读取 PDF 页面')
        elif item.get('status') == 'completed' and type(output.get('warning_count')) is int:
            parts.append(f"{output['warning_count']} 条编译警告")
        return '；'.join(parts)
    if capability == 'writing.edit':
        if item.get('status') == 'completed':return '已保存局部修改。' if inputs.get('edit_mode') == 'replace' else '已保存文件。'
        if item.get('status') == 'running':return '正在替换指定原文。' if inputs.get('edit_mode') == 'replace' else '正在保存文件。'
    if capability == 'writing.use_asset':
        source = safe_file(root, inputs.get('source'))
        if source:return f"来源：{source}" + ('；已绑定固定版本。' if item.get('status') == 'completed' else '')
    if capability == 'writing.propose_edits' and output.get('status') == 'pending':
        return '修改建议已生成，等待接受；正文尚未修改。'
    if capability == 'research.list_files' and type(output.get('total')) is int:
        return f"已列出 {len(output.get('files', []))} 个文件，共 {output['total']} 个。"
    if capability == 'research.search_files' and 'matches' in output:
        return f"本批搜索找到 {len(output['matches'])} 个匹配文件。"
    if capability == 'research.list_skills' and isinstance(output.get('skills'), list):
        return f"已找到 {len(output['skills'])} 项可用研究技能。"
    if capability == 'research.read_skill' and isinstance(output.get('name'), str):
        return '已读取研究技能：' + safe_diagnostic(root, output['name'], limit=160) + '。'
    if capability == 'research.delegate':
        return safe_diagnostic(root, inputs.get('task', ''), limit=240)
    if capability in {'read_file', 'research.read_artifact'}:
        total = output.get('total_chars')
        if total is not None:return f"已读取 {len(output.get('excerpt', ''))} 个字符，文件共 {total} 个字符。"
        start, size = inputs.get('start'), inputs.get('limit')
        if type(start) is int and type(size) is int:return f'从第 {start} 个字符起读取，最多 {size} 个字符。'
    if capability == 'experience.candidates' and 'candidates' in output:
        return f"已更新经验索引，共 {output['candidates']} 项。"
    if capability == 'record_results' and 'artifact_refs' in output:
        return f"已登记 {len(output['artifact_refs'])} 项结果。" + (f"另有 {output['rejected']} 项未通过核对。" if output.get('rejected') else '')
    if capability == 'record_figure':return output.get('caption', '')
    if capability == 'writing.save_source' and 'files' in output:
        return '已保存：' + '、'.join(output['files'])
    if capability == 'literature.collect':return safe_diagnostic(root, inputs.get('query', ''))
    return ''


def run_files(root, fs, run_id):
    """Prefer immutable published paths and collapse aliases of the same artifact."""
    files = []; seen = set()
    entries = sorted(fs._manifest.get('files', {}).items(),
                     key=lambda pair: (not pair[0].startswith('outputs/research/'), pair[0]))
    for value, meta in entries:
        archive = meta.get('archive_path')
        if meta.get('source_run') != run_id or meta.get('visibility') != 'user' or not archive:continue
        path = safe_file(root, value)
        if path and archive not in seen:
            seen.add(archive); files.append(path)
    return files


def tool_projection(root, research_id, item, *, fs=None):
    from ai_system.research.views import LABELS
    row = {key: item[key] for key in ('action_id','observation_id','question_id','experiment_id',
            'finished_at','run_id','subject','task_id','phase','error_type') if item.get(key) is not None}
    row.update(id=item['id'], research_id=research_id,
               tool_name=item.get('tool_name'),
               label=item.get('display_label') or LABELS.get(item.get('tool_name'), '执行步骤'),
               status=item.get('status', 'unknown'), started_at=item.get('started_at'))
    row['detail'] = operation_detail(root, item)
    output = item.get('output') or {}
    if item.get('tool_name') in {'writing.preview', 'writing.export'}:
        if output.get('compiled'): row['label'] = '编译并预览论文 PDF'
        row['warnings'] = [safe_diagnostic(root, warning) for warning in output.get('warnings', [])[:20] if isinstance(warning, str)]
    if item.get('error_type') or row['status'] in {'failed','error'}:
        reason = safe_diagnostic(root, item.get('message')) if item.get('error_type') else ''
        row['message'] = reason or '未记录具体错误；已有产物保留，可查看执行输出。'
    elif item.get('message'):
        row['message'] = safe_diagnostic(root, item['message'])
    inputs = item.get('inputs') or {}
    file = safe_file(root, item.get('display_file') or output.get('path') or inputs.get('path') or inputs.get('destination'))
    if file:
        row['file'] = file
        row['label'] += ' · ' + Path(file).name
    run_id = item.get('run_id')
    if root is not None and isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        from services.file_services.workspace_fs import WorkspaceFS
        fs = fs or WorkspaceFS(Path(root))
        info = fs.get_run_info(run_id) or {}
        row['output_file'] = safe_file(root, info.get('stdout'), nonempty=True)
        row['script_file'] = safe_file(root, info.get('input'))
        row['error_file'] = safe_file(root, info.get('stderr'), nonempty=True)
        row['files'] = run_files(root, fs, run_id)
        if row['files'] and not row['detail'] and row['status'] != 'running':
            row['detail'] = f"已保留 {len(row['files'])} 个结果文件。"
    if item.get('tool_name') in {'run_python','research.run_python'} and isinstance(output.get('output_tail'), str):
        row['output_tail'] = output['output_tail'][-6000:]
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
