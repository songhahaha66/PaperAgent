"""File-backed paper drafts, immutable source versions and range-local changes.

Research sections are a projection used by the scheduler, never an alternative
editable manuscript. Legacy TeX is imported byte-for-byte; no reverse conversion.
"""
from services.file_services.workspace_layout import workspace_path
import difflib
import hashlib
import json
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from services.file_services.workspace_lock import edit_lock
from ai_system.research.store import digest
from ai_system.research.models import ResearchConflict

TEXT_SUFFIXES = {'.tex', '.bib', '.sty', '.cls', '.bst', '.def', '.cfg', '.bbx', '.cbx', '.md'}
ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.pdf', '.eps', '.svg', '.webp', '.ttf', '.otf'}
META = '.system/paper-project.json'
VERSIONS = '.system/paper-project/versions'
MARKER = '.system/paper-project/publishing.json'


def read_utf8(path):
    return Path(path).read_bytes().decode('utf-8')


def atomic_text(path, text):
    from services.file_services.file_helper import atomic_write
    atomic_write(path, lambda f: f.write(text.encode('utf-8')))


def meta(root):
    path = workspace_path(Path(root), META)
    return json.loads(read_utf8(path)) if path.exists() else {}


def safe_path(root, name):
    root = Path(root).resolve()
    from services.file_services.workspace_layout import safe_join
    path = safe_join(root, name)
    if '.system' in Path(name).parts or path.suffix.lower() not in TEXT_SUFFIXES | ASSET_SUFFIXES:
        raise ValueError('请选择论文项目内的源码或图片文件')
    if (root/name).is_symlink() or path.is_symlink() or any(p.is_symlink() for p in (root / name).parents if p != root and p.is_relative_to(root)):
        raise ValueError('论文文件不能使用符号链接')
    return path


def files(root, main=None):
    """Follow local TeX dependencies, without exposing unrelated workspace files."""
    root = Path(root)
    main = main or meta(root).get('main') or ('paper.tex' if (root/'paper.tex').is_file() else 'paper.md')
    pending, found = [main], {}
    while pending:
        name = pending.pop()
        if name in found:
            continue
        path = safe_path(root, name)
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if len(raw) > 20_000_000:
            raise ValueError('论文项目单个文件超过 20 MB')
        text = raw.decode('utf-8') if path.suffix in TEXT_SUFFIXES else None
        found[name] = {'path': name, 'version': digest(text) if text is not None else hashlib.sha256(raw).hexdigest(),
                       'text': text, 'size': len(raw)}
        if text is not None and path.suffix == '.tex':
            dependency_text=re.sub(r'\\detokenize\{([^{}]+)\}',r'\1',re.sub(r'(?m)(?<!\\)%.*$', '', text))
            for command, value in re.findall(r'\\(input|include|bibliography|addbibresource|includegraphics)(?:\[[^\]]*\])?\{([^{}]+)\}', dependency_text):
                for item in value.split(','):
                    relative = (Path(name).parent / item.strip()).as_posix()
                    suffixes = [''] if Path(relative).suffix else (['.bib'] if command in {'bibliography','addbibresource'} else ['.tex'] if command in {'input','include'} else sorted(ASSET_SUFFIXES))
                    for suffix in suffixes:
                        candidate = relative + suffix
                        try:
                            if safe_path(root, candidate).is_file():
                                pending.append(candidate)
                                break
                        except ValueError:
                            continue
        if len(found) > 200:
            raise ValueError('论文项目超过 200 个文件')
    if main.endswith('.tex'):
        for path in root.rglob('*'):
            if path.is_file() and path.suffix in {'.sty','.cls','.bst','.def','.cfg','.bbx','.cbx','.ttf','.otf'} and '.system' not in path.parts:
                name=path.relative_to(root).as_posix()
                safe_path(root,name)
                raw=path.read_bytes();text=raw.decode('utf-8') if path.suffix in TEXT_SUFFIXES else None
                found.setdefault(name,{'path':name,'version':digest(text) if text is not None else hashlib.sha256(raw).hexdigest(),'text':text,'size':len(raw)})
    # Generated bibliography metadata is part of the project even with inline links.
    if main.endswith('.tex') and (root/'paper-sources.bib').is_file() and 'paper-sources.bib' not in found:
        text = (root/'paper-sources.bib').read_text()
        found['paper-sources.bib'] = {'path':'paper-sources.bib','version':digest(text),'text':text,'size':len(text.encode())}
    return found


def snapshot(root):
    entries = files(root)
    version = digest({name: row['version'] for name,row in sorted(entries.items())})
    archive = workspace_path(Path(root), VERSIONS) / version
    archive.mkdir(parents=True, exist_ok=True)
    for name,row in entries.items():
        target = archive/name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(root,name),target)
    manifest = (archive/'manifest.json')
    if not manifest.exists():
        atomic_text(manifest,json.dumps({name:{k:v for k,v in row.items() if k!='text'} for name,row in entries.items()}))
    return {'version':version,'files':list(entries.values()),'main':meta(root).get('main') or ('paper.tex' if 'paper.tex' in entries else 'paper.md')}


def base_text(root, version, name):
    if not re.fullmatch(r'[a-f0-9]{64}',version):
        raise ValueError('源码版本无效')
    safe_path(root,name)
    archive = workspace_path(Path(root), VERSIONS)/version
    manifest = json.loads(read_utf8((archive/'manifest.json')))
    if name not in manifest:
        raise ValueError('该文件不属于选区所绑定的版本')
    text = read_utf8(archive/name)
    if digest(text) != manifest[name]['version']:
        raise ResearchConflict('历史源码版本完整性检查失败')
    return text


def bind(root, selection):
    name,version,start,end,quote = (selection[k] for k in ('file','version','start','end','quote'))
    original = base_text(root,version,name)
    if not isinstance(start,int) or not isinstance(end,int) or not (0 <= start < end <= len(original) or start==end==0 and original=='') or original[start:end]!=quote:
        raise ResearchConflict('选区与该文件版本不一致，请重新选择')
    return {**selection,'quote':quote}


def relocate(before, current, start, end):
    """Map an exact versioned range only through unchanged diff blocks.

    Offsets distinguish duplicate passages. Other edits can move the target;
    edits touching the target fail closed, even if identical text exists elsewhere.
    """
    if before == current:
        return start,end
    for tag,i,j,a,b in difflib.SequenceMatcher(None,before,current,autojunk=False).get_opcodes():
        if tag == 'equal' and i <= start and end <= j:
            anchor=before[i:j]
            def unique(text, value):
                first=text.find(value)
                return first>=0 and text.find(value,first+1)<0
            if not unique(before,anchor) or not unique(current,anchor):
                raise ResearchConflict('重复原文在并发编辑后无法唯一跟踪，请重新确认源码范围')
            return a+start-i,a+end-i
    raise ResearchConflict('所选原文已被修改，补丁保留。请比较当前源码后重新定位。')


def current_selection(root, selection):
    bound = bind(root,selection)
    before = base_text(root,bound['version'],bound['file'])
    current = read_utf8(safe_path(root,bound['file']))
    start,end = relocate(before,current,bound['start'],bound['end'])
    return current,start,end


def candidates(root, quote):
    """A read selection is never an edit target until a person confirms a range."""
    result = []
    doc = snapshot(root)
    for row in doc['files']:
        text = row['text']
        if text is None or not row['path'].endswith(('.tex','.md')):
            continue
        start = 0
        while quote and len(result)<30:
            start = text.find(quote,start)
            if start < 0:
                break
            end = start+len(quote)
            result.append({'file':row['path'],'version':doc['version'],'start':start,'end':end,'quote':quote,
                           'line':text.count('\n',0,start)+1,'context':text[max(0,start-100):end+100]})
            start += 1
    return result


def initialize(store):
    with store._transaction() as db:
        for table in ('paper_threads','paper_executions','paper_changes'):
            db.execute(f'CREATE TABLE IF NOT EXISTS {table}(research_id TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(research_id,id))')
    recover(store)


def read_records(store, table):
    with store._connect() as db:
        return [json.loads(r[0]) for r in db.execute(f'SELECT payload FROM {table} WHERE research_id=? ORDER BY rowid',(store.research_id,))]


def put(db, store, table, value):
    db.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?,?)',(store.research_id,value['id'],json.dumps(value,ensure_ascii=False)))


def recover(store):
    marker = workspace_path(store.root, MARKER)
    if not marker.exists():
        return
    with store._transaction() as db:
        if not marker.exists():
            return
        record = json.loads(read_utf8(marker))
        row = db.execute('SELECT payload FROM paper_changes WHERE research_id=? AND id=?',(store.research_id,record['id'])).fetchone()
        applied = row and json.loads(row[0]).get('status') == record['decision']
        if not applied:
            for name,text in record['before'].items():
                target=safe_path(store.root,name)
                active=read_utf8(target) if target.exists() else None
                if active==record.get('after',{}).get(name):
                    atomic_text(target,text)
                elif active!=text:
                    data=meta(store.root)
                    data['migration_notice']='中断保存后该文件又有编辑，已保留人工稿和历史版本；请比较修改集后重新应用。'
                    atomic_text(workspace_path(store.root, META),json.dumps(data,ensure_ascii=False))
        marker.unlink()


def validate_patches(root, patches):
    if not 1 <= len(patches) <= 24:
        raise ValueError('一次修改应包含一至二十四个局部补丁')
    grouped = {}
    for patch in patches:
        current,start,end = current_selection(root,patch)
        row = grouped.setdefault(patch['file'],{'before':current,'edits':[]})
        if any(start < e and end > s for s,e,_ in row['edits']):
            raise ValueError('同一修改集内的补丁不能重叠')
        row['edits'].append((start,end,patch['replacement']))
    for row in grouped.values():
        text = row['before']
        for start,end,replacement in sorted(row['edits'],reverse=True):
            text = text[:start]+replacement+text[end:]
        row['after'] = text
    return grouped


def propose_change(store, thread_id, execution_id, patches):
    grouped = validate_patches(store.root,patches)
    change = {'id':uuid.uuid4().hex,'thread_id':thread_id,'execution_id':execution_id,'status':'pending',
              'patches':patches,'created_at':time.time(),'diff':''.join(''.join(difflib.unified_diff(r['before'].splitlines(True),r['after'].splitlines(True),fromfile=name,tofile=name)) for name,r in grouped.items())}
    with store._transaction() as db:
        put(db,store,'paper_changes',change)
    return change


async def decide(store, change_id, decision, *, compile_after=True, expected_version=None):
    initialize(store)
    with edit_lock(store.root), store._transaction() as db:
        row = db.execute('SELECT payload FROM paper_changes WHERE research_id=? AND id=?',(store.research_id,change_id)).fetchone()
        if not row:
            raise ValueError('修改集不存在')
        change = json.loads(row[0])
        if decision == 'reject':
            if change['status']=='pending':
                change['status']='rejected';put(db,store,'paper_changes',change)
            return change
        expected = 'applied' if decision=='revert' else 'pending'
        if change['status'] != expected:
            return change
        if expected_version and snapshot(store.root)['version']!=expected_version:
            raise ResearchConflict('写作期间项目已更新，修改集已保留，未覆盖新源码')
        patches = change['undo'] if decision=='revert' else change['patches']
        grouped = validate_patches(store.root,patches)
        # Local source checks protect references without making a model judge a
        # prerequisite for accepting a user-reviewed draft.
        from .paper_sources import validate_references
        for name,value in grouped.items():
            validate_references(store.root,value['before'],value['after'])
        status = 'reverted' if decision=='revert' else 'applied'
        atomic_text(workspace_path(store.root, MARKER),json.dumps({'id':change_id,'decision':status,'before':{k:v['before'] for k,v in grouped.items()},'after':{k:v['after'] for k,v in grouped.items()}}))
        try:
            for name,value in grouped.items():
                # Recheck immediately before replace, including outside-editor writes.
                if read_utf8(safe_path(store.root,name))!=value['before']:
                    raise ResearchConflict('保存期间源码发生变化，未覆盖新的编辑')
                atomic_text(safe_path(store.root,name),value['after'])
            doc = snapshot(store.root)
            undo=[]
            for name,value in grouped.items():
                delta=0
                for start,end,replacement in sorted(value['edits']):
                    a=start+delta;b=a+len(replacement)
                    # Empty replacements need an insertion anchor for undo.
                    if a==b:
                        after=value['after'];left=max(0,a-1);right=min(len(after),a+1)
                        # Empty files carry an exact zero-length versioned anchor.
                        undo.append({'file':name,'version':doc['version'],'start':left,'end':right,'quote':after[left:right],
                                     'replacement':after[left:a]+value['before'][start:end]+after[a:right]})
                    else:
                        undo.append({'file':name,'version':doc['version'],'start':a,'end':b,'quote':replacement,'replacement':value['before'][start:end]})
                    delta += len(replacement)-(end-start)
            change.update(status=status,undo=undo,applied_version=doc['version'])
            if change.get('thread_id') and len(undo)==1:
                row=db.execute('SELECT payload FROM paper_threads WHERE research_id=? AND id=?',(store.research_id,change['thread_id'])).fetchone()
                if row:
                    thread=json.loads(row[0]);thread['selection']={k:v for k,v in undo[0].items() if k!='replacement'}
                    put(db,store,'paper_threads',thread)
            put(db,store,'paper_changes',change)
        except BaseException:
            for name,value in grouped.items():
                # Do not roll back a third-party write that raced a failed group.
                if read_utf8(safe_path(store.root,name))==value['after']:
                    atomic_text(safe_path(store.root,name),value['before'])
            (workspace_path(store.root, MARKER)).unlink(missing_ok=True)
            raise
    (workspace_path(store.root, MARKER)).unlink(missing_ok=True)
    if compile_after and meta(store.root).get('main','paper.tex').endswith('.tex') and (store.root/'paper.tex').exists():
        await compile_project(store)
    return change


async def compile_project(store):
    """Compile an immutable source snapshot; failed builds retain the last PDF."""
    from ai_system.export.latex import compile_pdf, BUILD_DIR, LatexResult
    before=snapshot(store.root);version=before['version'];main=before['main']
    (store.root/'.build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='paper-project-', dir=store.root/'.build') as folder:
        staging=Path(folder)
        for row in before['files']:
            target=staging/row['path'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(workspace_path(store.root, VERSIONS)/version/row['path'],target)
        try:
            result=await compile_pdf(staging,main)
        except Exception as exc:
            from ai_system.research.review_cache import error_detail
            result=LatexResult(tex_path=main,error='编译服务未完成：'+error_detail(exc)+'。上次成功 PDF 已保留。')
            import traceback
            result.log_path=f'{BUILD_DIR}/compile.log'
            atomic_text(staging/result.log_path,traceback.format_exc())
        log=staging/(result.log_path or f'{BUILD_DIR}/{Path(main).stem}.log')
        log_path=f'{BUILD_DIR}/{version}/{time.time_ns()}/compile.log'
        atomic_text(store.root/log_path,log.read_text(errors='replace') if log.is_file() else result.error or '编译完成')
        compilation={'success':result.success,'error_summary':result.error,'diagnostics':result.diagnostics,
                     'source_version':version,'log_path':log_path,'exit_code':result.exit_code,
                     'warnings':result.warnings,'pages':result.pages}
        with store._transaction() as db:
            data=meta(store.root)
            if snapshot(store.root)['version']!=version:
                compilation.update(success=False,error_summary='编译期间源码已更新，请重新编译。上次成功 PDF 已保留。')
                data['compile']={**compilation,'state':'stale','message':compilation['error_summary']}
            elif result.success:
                target=store.root/'paper.pdf';tmp=target.with_suffix('.pdf.paper-tmp')
                shutil.copyfile(staging/result.pdf_path,tmp);tmp.replace(target)
                data['pdf_version']=version
                data['compile']={**compilation,'state':'current','message':'PDF 已与当前源码同步'}
                state=store._read(db)
                closed=[]
                for issue in state['feedback']:
                    if issue['source']=='latex.compile' and issue['status'] not in {'resolved','dismissed','superseded'}:
                        issue.update(status='resolved',review_resolution={'source_version':version,'source':'xelatex'})
                        closed.append(issue['id'])
                if closed:store._save(db,state,'latex_source_errors_resolved',{'issues':closed,'source_version':version})
            else:
                data['compile']={**compilation,'state':'failed','message':result.error or 'PDF 编译失败，上次成功预览已保留'}
            atomic_text(workspace_path(store.root, META),json.dumps(data,ensure_ascii=False))
    return meta(store.root)


def describe(store):
    initialize(store)
    doc=snapshot(store.root);data=meta(store.root)
    if doc['files'] and not data:
        data={'main':doc['main'],'migration_notice':'原有源码及原文件已保留，当前阅读与编辑使用同一项目版本；旧章节提案仅供历史参考。'}
        atomic_text(workspace_path(store.root, META),json.dumps(data,ensure_ascii=False))
    pdf=store.root/'paper.pdf'
    compile_state=data.get('compile',{'state':'untracked','message':'尚未记录编译版本；已有 PDF 仅供参考，请编译以同步源码。'})
    if data.get('pdf_version') and data['pdf_version']!=doc['version'] and compile_state['state']=='current':
        compile_state={'state':'stale','message':'源码已更新，PDF 仍为上次成功版本。'}
    from .paper_comments import is_live
    executions=read_records(store,'paper_executions')
    executions=[{**r,'state':'interrupted'} if r['state']=='running' and not is_live(r) else r for r in executions]
    return {**doc,'compile':compile_state,'pdf_available':pdf.is_file() and bool(pdf.stat().st_size),
            'pdf_version':data.get('pdf_version'),'feedback':[{k:r.get(k) for k in ('id','message','section','task_id','status','paper_origin')} for r in (store.snapshot() or {}).get('feedback',[]) if r['status'] not in {'resolved','dismissed','superseded'}],'threads':read_records(store,'paper_threads'),
            'executions':executions,'changes':read_records(store,'paper_changes'),
            'migration_notice':data.get('migration_notice','')}


def is_latex(store):
    state = store.snapshot() or {}
    mode = state.get('spec', {}).get('output_mode')
    return mode == 'latex' or mode is None and (store.root/'paper.tex').is_file() or str(state.get('paper_version', '')).endswith('.tex')


def source_state(store, state):
    """Derive review units from exact current files, without guessing TeX structure."""
    doc = snapshot(store.root)
    return {**state, 'sections': {row['path']: row['text'] for row in doc['files']
                                if row['path'].endswith('.tex') and row['text'] is not None},
            'source_version': doc['version'], 'source_files': doc['files']}


def source_targets(state):
    """Server-owned ranges in one source snapshot; labels are local to that version.

    Paragraph boundaries are editing units, not a TeX parser. File targets allow
    structural edits and findings spanning paragraphs without guessed offsets.
    """
    targets = {}
    for name, text in state['sections'].items():
        def selection(start, end):
            return {'file':name, 'version':state['source_version'],
                    'start':start, 'end':end, 'quote':text[start:end]}
        targets[name] = selection(0, len(text))
        for index, match in enumerate(re.finditer(r'.+?(?:\n[ \t]*\n|\Z)', text, re.DOTALL), 1):
            targets[f'{name}#{index}'] = selection(match.start(), match.end())
    return targets


def source_prompt(state):
    targets = source_targets(state)
    return [{'file':name, 'target':name, 'blocks':[
        {'target':key, 'text':value['quote']} for key,value in targets.items()
        if key != name and value['file'] == name]} for name in state['sections']]
