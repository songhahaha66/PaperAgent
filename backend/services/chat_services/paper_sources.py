"""Source cards contain retrieved text only, never model-created quotations."""
import json
import re
from urllib.parse import urlsplit
from ai_system.research.literature import LiteratureStore
from ai_system.research.store import digest

SOURCE_LINK = 'https://paperagent.local/citations/'


def cards(store):
    from ai_system.research.citations import BOUNDARIES
    rows=[]
    for passage in LiteratureStore(store.root).passages():
        url=passage['source_url']
        if urlsplit(url).scheme not in {'http','https'}:
            url=''
        rows.append({'key':passage['id'],'title':passage['source'].get('title') or '未命名来源',
                     'excerpt':passage['text'],'boundary':BOUNDARIES.get(passage['boundary'],'来源类型未知'),
                     'abstract_only':passage['boundary']=='abstract','locator':passage.get('locator') or '无法定位原文',
                     'page':passage.get('page'),'url':url,'version':passage['content_hash'],
                     'retrieved_at':passage.get('retrieved_at'),'source_id':passage['source_id'],
                     'note':'来源关联不代表该结论已证实。'})
    # Experimental sources are actual frozen inputs, code and stdout, not guessed
    # associations based on matching numbers in recent logs.
    snapshot=store.snapshot() or {}
    manifest_path=store.root/'manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for key,row in snapshot.get('artifacts',{}).items():
        payload=row.get('payload',{});run=payload.get('run_id','')
        if not re.fullmatch(r'run_[A-Za-z0-9_]+',run):
            continue
        info=manifest.get('runs',{}).get(run,{})
        paths=[]
        for field in ('input','stdout'):
            name=info.get(field)
            if name and name.startswith('runs/'+run+'/') and (store.root/name).resolve().is_relative_to(store.root) and (store.root/name).is_file():
                paths.append(name)
        stdout=next((p for p in paths if p.endswith('.log')),None)
        excerpt=(store.root/stdout).read_text(errors='replace')[:4000] if stdout else ''
        rows.append({'key':key,'title':payload.get('name') or '实验结果','excerpt':excerpt,
                     'boundary':'实验实际输出','locator':stdout or '原始输出不可用','paths':paths,
                     'page':None,'url':'','version':digest([payload,excerpt]),'source_id':run,
                     'note':'该记录保留实际执行来源；不能单凭来源关联证明结论成立。'})
    with store._connect() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_executions'").fetchone()
        executions=[json.loads(r[0]) for r in db.execute('SELECT payload FROM paper_executions WHERE research_id=?',(store.research_id,))] if exists else []
    for execution in executions:
        result=execution.get('tool_result') or {}
        if result.get('ok') is not True:
            continue
        paths=[]
        for name in (result.get('code_path'),result.get('stdout_path')):
            if name and (store.root/name).resolve().is_relative_to(store.root) and (store.root/name).is_file():
                paths.append(name)
        output=result.get('stdout_path')
        excerpt=(store.root/output).read_text(errors='replace')[:4000] if output in paths else ''
        rows.append({'key':'E'+execution['id'],'title':'评论中的辅助计算','excerpt':excerpt,
                     'boundary':'辅助计算实际输出（未自动通过研究验收）','locator':output if output in paths else '原始输出不可用',
                     'paths':paths,'inputs':result.get('inputs',[]),'page':None,'url':'',
                     'version':digest(result),'source_id':execution['id'],
                     'note':'输入、代码、输出已独立保留；该辅助计算没有自动改动主研究的验收状态。'})
    return rows


def write_bibliography(root):
    from .paper_project import atomic_text,meta,META,read_utf8
    literature=LiteratureStore(root)
    rows=literature.passages()
    if not rows:
        return
    def value(s):
        return str(s or '').replace('\\',r'\textbackslash{}').replace('{',r'\{').replace('}',r'\}').replace('\n',' ')
    entries=[]
    for row in rows:
        source=row['source']
        fields={'title':source.get('title'),'author':' and '.join(source.get('authors') or []),
                'year':source.get('year'),'url':row['source_url'],'doi':source.get('doi'),
                'note':row['locator']+'; '+row['boundary']}
        entries.append('@misc{'+row['id']+',\n'+',\n'.join('  '+k+' = {'+value(v)+'}' for k,v in fields.items() if v)+'\n}')
    path=root/'paper-sources.bib'
    # This file is generated only within a delimited block. Preserve user entries.
    begin='% paperagent:sources:begin\n';end='\n% paperagent:sources:end'
    block=begin+'\n\n'.join(entries)+end
    text=read_utf8(path) if path.exists() else ''
    original=text
    state_path=root/'.system/paper-sources-bib.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    conflict=False
    if begin in text and end in text:
        a=text.index(begin);b=text.index(end,a)+len(end)
        if state.get('block_hash')!=digest(text[a:b]):
            conflict=True
        else:
            text=text[:a]+block+text[b:]
    elif text and re.search(r'@\w+\s*\{\s*C[1-9]\d*\s*,',text):
        conflict=True
    else:
        text=text+('' if not text or text.endswith('\n') else '\n')+block+'\n'
    with literature.research._transaction():
        active=read_utf8(path) if path.exists() else ''
        if conflict or active!=original:
            data=meta(root)
            data['migration_notice']='参考文献文件有人工编辑，已保留原文件；新增来源可在来源面板核对后合并。'
            atomic_text(root/META,json.dumps(data,ensure_ascii=False))
            return
        atomic_text(path,text)
        atomic_text(state_path,json.dumps({'block_hash':digest(block)}))



def validate_references(root,before,after):
    """Authenticity checks, not a claim-support verdict or whole-paper review."""
    from ai_system.graph.registry import REF_PATTERN
    from ai_system.research.literature import CITATION
    if REF_PATTERN.search(after) or CITATION.search(after):
        raise ValueError('源码补丁应使用已有来源键或已渲染结果，不得留下未解析的内部标记')
    import sqlite3
    with sqlite3.connect('file:'+str(root/'.system/research.sqlite')+'?mode=ro',uri=True) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='literature_passages'").fetchone()
        known={json.loads(r[0])['id'] for r in db.execute('SELECT payload FROM literature_passages')} if exists else set()
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_executions'").fetchone():
            for row in db.execute('SELECT payload FROM paper_executions'):
                execution=json.loads(row[0]);result=execution.get('tool_result') or {}
                output=result.get('stdout_path')
                if result.get('ok') is True and output and (root/output).resolve().is_relative_to(root) and (root/output).is_file():
                    known.add('E'+execution['id'])
    added=set(re.findall(r'paperagent\.local/citations/([A-Za-z0-9_-]+)',after))-set(re.findall(r'paperagent\.local/citations/([A-Za-z0-9_-]+)',before))
    if added-known:
        raise ValueError('补丁包含未知来源键，不能编造引用')
    # Accept existing arbitrary bibliography keys; reject newly invented keys.
    bib='\n'.join(p.read_text() for p in root.rglob('*.bib') if '.system' not in p.parts)
    keys=set(re.findall(r'@\w+\s*\{\s*([^,\s]+)',bib))
    cites=lambda t:{k.strip() for group in re.findall(r'\\cite\w*\*?(?:\[[^\]]*\])*\{([^{}]+)\}',t) for k in group.split(',')}
    if (cites(after)-cites(before))-keys:
        raise ValueError('新增文献引用没有对应的 .bib 来源，请先检索或导入来源')
