"""Read-only verification of historical segmented review receipts.

The old limits and prompt contract apply only to existing records. No new review
is dispatched here; current reviews use the normal conclusion/source-issue path.
"""

import json,re
from contextlib import closing
from pydantic import BaseModel,Field
from .store import digest
from .review_cache import review_context,science_version
from ..graph.state import ClaimIssue

LEGACY_AUDIT_SYSTEM='区分实测数值差异与统计显著性：固定拆分上误差更低、相对均值基线改善，并不能单独支持统计显著。即使结论表的strength标为显著，仍要核查具体检验证据；不得把结果明确当作已做显著性检验。正文同时声明未做显著性检验却使用显著优于/显著改善时，须要求限定为该拆分上的实测差异。方法公式须对照实际核验的估计器设置：截距、是否惩罚截距、训练集中心化和标准化的拟合范围。明确的中心化方程加截距恢复，或含常数列且正确处理截距惩罚的方程均有效；不要要求固定符号写法。构造反例前先核对登记序列的对应关系：对象、日期和统计范围必须一致；逐日不等式须逐日比较，不能用某日最小值与全周均值比较来反证。若根因是数据、模型或假设错误，填写research_issue并引用登记证据和检查编号，进入研究核验；仅写作错误只填section和problem。补写没有证据支持的数值、夸大结论或遗漏适用条件，应按已有证据重写，不能为此重算原本正确的上游。research_issue只用于已有上游结果本身可能错误的情况；检查必须直接验证疑点，不能用目标值有限或其他无关检查代替。另一个例外是确实发现原题研究目的尚无实际产物回答：用research_issue.category=coverage说明缺少的原要求及实际依据，交回同一研究控制器；这不是已证明的上游错误，不虚构检查或阈值。仅文稿漏写已有结果时仍按writing处理，不要求新增实验。你是论文终审。下面给出主要结论、统一口径、已登记证据，以及保留原始引用的章节正文。正文引用对应的数值和含义以登记证据表为准。主要结论表只是关键结论摘录，不是证据全集；已登记的变量或辅助结果可以用于论证，不得因为它未被主要结论表列出就判为虚构。引用格式和编号存在性由程序检查，本次只审查语义矛盾，不要要求增加内部编号、照抄数值或逐句标注证据强度。逐句检查这些章节是否与结论表一致：结论方向、显著与否、强弱程度、适用范围是否被改写或夸大。只报告与结论表或统一口径实际矛盾的句子；一致的、或只是没有提到某条结论的都不要列入，措辞风格问题也不要报告。'

POLICY='segmented-sections-v1'
ATOM_LIMIT=3000
WINDOW_LIMIT=6000
GLOBAL_LIMIT=26000
MAX_BLOCKS=128

class CoverageError(ValueError):pass


class Statement(BaseModel):
    quote:str=Field(min_length=1,max_length=800)
    claim_ids:list[str]=Field(default_factory=list,max_length=12)

class BlockAudit(BaseModel):
    issues:list[ClaimIssue]
    statements:list[Statement]=Field(max_length=32,description='本块正文的主要事实/结论/适用条件的逐字原文；不从左侧上下文重复提取；没有主张须明确返回空数组')

class GlobalAudit(BaseModel):
    block_keys:list[str]=Field(description='逐字列出全部输入块的key，不能遗漏或重复')
    issues:list[ClaimIssue]


def protected(text):
    from ..graph.registry import MATH_PATTERN,REF_PATTERN
    from .literature import CITATION
    spans=[m.span() for p in (MATH_PATTERN,REF_PATTERN,CITATION) for m in p.finditer(text)]
    spans += [m.span() for m in re.finditer(r'```[^\n]*\n[\s\S]*?```',text)]
    spans += [m.span() for m in re.finditer(r'\\(?:cite\w*|eqref|ref)\*?(?:\[[^\]]*\])*\{[^}]*\}|\[[^\]]*\]\([^)]*\)',text)]
    stack=[]
    for m in re.finditer(r'\\(begin|end)\{([^}]+)\}',text):
        if m[1]=='begin':stack.append((m[2],m.start()))
        elif stack and stack[-1][0]==m[2]:
            _,start=stack.pop();spans.append((start,m.end()))
        else:raise CoverageError('LaTeX环境不匹配，不能生成可靠分块')
    if stack:raise CoverageError('LaTeX环境未闭合，先修复原文')
    cursor=0;unprotected=[]
    for a,b in sorted(spans):
        if a>cursor:unprotected.append(text[cursor:a])
        cursor=max(cursor,b)
    unprotected.append(text[cursor:]);remaining=' '.join(unprotected)
    if re.search(r'(?<!\\)\$|\\[\[\]()]|\{\{[RFC]:',remaining):
        raise CoverageError('公式或引用标记未闭合，不能保证安全分块')
    return spans


def atoms(text):
    spans=protected(text)
    safe=lambda n:not any(a<n<b for a,b in spans)
    paragraphs=[0]+[m.end() for m in re.finditer(r'\n[ \t]*\n+',text) if safe(m.end())]+[len(text)]
    result=[]
    for a,b in zip(paragraphs,paragraphs[1:]):
        if a==b:continue
        if b-a<=ATOM_LIMIT:result.append((a,b));continue
        cuts=[a]+[a+m.end() for m in re.finditer(r'[。！？；;](?:[ \t]*\n)?|[.!?](?=\s|$)\s*',text[a:b]) if safe(a+m.end())]+[b]
        group_start=a;group_end=a
        for start,end in zip(cuts,cuts[1:]):
            if start==end:continue
            if end-start>ATOM_LIMIT:raise CoverageError('单个不可安全拆分的句子、公式或环境超过3000字符；须先整理原文，不能硬截断放行')
            if end-group_start>ATOM_LIMIT:
                result.append((group_start,group_end));group_start=start
            group_end=end
            # Content anchors avoid regrouping the rest of a long paragraph
            # after a local insertion; the cap handles paragraphs without anchors.
            if group_end-group_start>=512 and int(digest(text[start:end])[:2],16)%8==0:
                result.append((group_start,group_end));group_start=group_end
        if group_end>group_start:result.append((group_start,group_end))
    return result


def validate_plan(plan):
    if plan.get('policy')!=POLICY or plan.get('atom_limit')!=ATOM_LIMIT or plan.get('offset_unit')!='unicode_code_point' or len(plan['blocks'])>MAX_BLOCKS or digest({k:v for k,v in plan.items() if k!='id'})!=plan.get('id'):raise CoverageError('覆盖清单校验失败')
    seen=set()
    for section,meta in plan['sections'].items():
        cursor=0;whole='';previous='';previous_start=0
        for b in [b for b in plan['blocks'] if b['section']==section]:
            binding={k:b[k] for k in ('section','section_hash','revision','start','end','window_start','window_end','core_hash','window_hash')}
            if b['id'] in seen or digest(binding)!=b['id']:raise CoverageError('覆盖块重复或绑定已改变')
            seen.add(b['id'])
            if b['start']!=cursor or b['end']!=cursor+len(b['text']) or b['end']<=cursor:raise CoverageError('原文覆盖存在缺口或重叠')
            if b['left']!=previous or b['window_start']!=(previous_start if previous else cursor) or b['window_end']!=b['end']:raise CoverageError('跨块上下文不匹配')
            if b['core_hash']!=digest(b['text']) or b['window_hash']!=digest(b['left']+b['text']) or len(b['left']+b['text'])>WINDOW_LIMIT:raise CoverageError('块原文或窗口哈希不匹配')
            if b['section_hash']!=meta['hash'] or b['revision']!=meta['revision']:raise CoverageError('章节版本不匹配')
            previous=b['text'];previous_start=b['start'];whole+=b['text'];cursor=b['end']
        if cursor!=meta['length'] or digest(whole)!=meta['hash']:raise CoverageError('原文没有完整覆盖')
        if [(b['start'],b['end']) for b in plan['blocks'] if b['section']==section]!=atoms(whole):raise CoverageError('分块不符合确定性段落/公式边界')
    if len(seen)!=len(plan['blocks']):raise CoverageError('清单有未知章节')
    return True


def _rows(store):
    with closing(store._connect()) as db:
        return [{**json.loads(r[1]),'state':r[0]} for r in db.execute('SELECT state,payload FROM review_records WHERE research_id=? ORDER BY rowid DESC',(store.research_id,))]


def block_material(ctx,state,block):
    from ai_system.modules.writing.consistency import section_audit_prompt
    AUDIT_SYSTEM=LEGACY_AUDIT_SYSTEM
    selected={**state,'sections':{block['section']:block['left']+block['text']}}
    # The normal evidence/contract prompt is reused; the window never exceeds its cap.
    prompt=section_audit_prompt(selected if block['section']!='references' else {**state,'sections':{'scope':'文献列表也须逐块核查，实际原文见下方范围说明。'}},ctx,force=True)[0]
    prompt+='\n# 范围说明\n'+json.dumps({'section':block['section'],'left_context':block['left'],'core':block['text']},ensure_ascii=False)
    system=AUDIT_SYSTEM+'\n这是局部块核查。左侧相邻上下文用于检查跨块衔接；statements只提取core的主要主张、结论强度和适用条件的逐字引文，不能编造或遗漏明显结论。局部没有问题不代表全局一致。issues和statements必须明确返回。'
    context=review_context(ctx,selected,'section_blocks',system,prompt,BlockAudit,sections=selected['sections'],extra={'non_blocking':True,'binding':{k:v for k,v in block.items() if k not in {'text','left'}}})
    key=digest({'policy':POLICY,'model':context['model_id'],'science':science_version(context),'projection':context['reconcile_projection_hash'],
        'request':context['request_hash'],'sources':context['citation_sources']})
    context['extra']['reuse_key']=key
    return context,system,prompt


def validate_block(answer,block,ctx,state):
    from ai_system.modules.writing.consistency import validate_findings
    if any(i.section!=block['section'] or i.quote and i.quote not in block['left']+block['text'] for i in answer.issues):raise CoverageError('局部意见超出实际原文窗口')
    if any(s.quote not in block['text'] or not set(s.claim_ids)<=set(ctx.registry.claims) for s in answer.statements):raise CoverageError('主要主张不是本块逐字原文或引用了未知结论')
    validate_findings([i.research_issue for i in answer.issues if i.research_issue],state,ctx)


def global_material(ctx,state,bindings):
    from ai_system.modules.writing.consistency import section_audit_prompt
    AUDIT_SYSTEM=LEGACY_AUDIT_SYSTEM
    ledger=[]
    for index,(block,answer,row) in enumerate(bindings):
        ledger.append({'key':f'B{index+1}','section':block['section'],'core_hash':block['core_hash'],'review_key':row['context']['extra']['reuse_key'],
            'result_hash':row['result_hash'],'statements':[s.model_dump() for s in answer.statements]})
    background=section_audit_prompt({**state,'sections':{'scope':'跨节结论审查，原文已分别核查。'}},ctx,force=True)[0]
    prompt=background+'\n# 全部块提取的可定位主张（独立检查跨节与远距块的逻辑、条件和结论强度）\n'+json.dumps(ledger,ensure_ascii=False)
    if len(prompt)>GLOBAL_LIMIT:raise CoverageError('跨节主张账本超过26000字符，尚不能完整核查；局部块保留，不以局部通过推断全局一致')
    system=AUDIT_SYSTEM+'\n你现在独立检查所有输入块提取的主要主张之间的矛盾，包括跨章节和同节远距块。不能从局部issues为空推断一致。返回完整block_keys及明确issues；quote只用账本实际逐字引文，不编造原句。此检查覆盖已提取的主张，不代表所有隐含语义均已证明。'
    context=review_context(ctx,state,'section_global',system,prompt,GlobalAudit,sections=state['sections'],extra={'non_blocking':True})
    context['extra']['reuse_key']=digest({'policy':POLICY,'request':context['request_hash'],'model':context['model_id'],'science':science_version(context),'sources':context['citation_sources']})
    return context,system,prompt,ledger


def validate_global(answer,ledger,ctx,state):
    from ai_system.modules.writing.consistency import validate_findings
    expected=[r['key'] for r in ledger]
    if sorted(answer.block_keys)!=sorted(expected) or len(answer.block_keys)!=len(expected):raise CoverageError('跨节核查遗漏或重复输入块')
    quotes={(r['section'],s['quote']) for r in ledger for s in r['statements']}
    if any(i.section not in state['sections'] or i.quote and (i.section,i.quote) not in quotes for i in answer.issues):raise CoverageError('跨节意见没有对应账本原文')
    validate_findings([i.research_issue for i in answer.issues if i.research_issue],state,ctx)


def verify_proof(store,record):
    """Reconstruct every source character and recompute each dependency key."""
    try:
        from .review_migration import context_from_store
        plan=record['context']['extra']['segmented_plan'];validate_plan(plan)
        if record['context_hash']!=digest(record['context']):return False
        if {k:v['hash'] for k,v in plan['sections'].items()}!=record['context']['section_hashes']:return False
        contract=record['context']['extra']['prompt_contract']
        state={'analysis':{'requirements':contract['requirements'],'assumptions':contract['assumptions'],'subproblems':[t['subproblem'] for t in store.snapshot()['experiments'].values() if not t['retired']]},'consistency':contract['consistency'],
            'sections':{k:''.join(b['text'] for b in plan['blocks'] if b['section']==k) for k in plan['sections']}}
        ctx=context_from_store(store,record['context']['model_id'])
        proofs=record['provenance']['blocks']
        if len(proofs)!=len(plan['blocks']) or len({p['binding_id'] for p in proofs})!=len(proofs):return False
        rows={r['id']:r for r in _rows(store)};by_id={b['id']:b for b in plan['blocks']};bindings=[]
        for proof in proofs:
            block=by_id[proof['binding_id']];row=rows[proof['review_id']]
            if row['state']!='completed' or row['stage']!='section_blocks' or row.get('result_hash')!=digest(row.get('result')) or row['result_hash']!=proof['result_hash']:return False
            expected,_,_=block_material(ctx,state,block)
            if row['context_hash']!=digest(row['context']) or row['context']['model_id']!=expected['model_id'] or row['context'].get('reconcile_projection_hash')!=expected.get('reconcile_projection_hash'):return False
            if row['context'].get('extra',{}).get('reuse_key')!=expected['extra']['reuse_key'] or proof['reuse_key']!=expected['extra']['reuse_key']:return False
            if row['context']['section_hashes']!={block['section']:block['window_hash']} or science_version(row['context'])!=science_version(record['context']):return False
            answer=BlockAudit.model_validate(row['result']);validate_block(answer,block,ctx,state);bindings.append((block,answer,row))
        if [b['id'] for b,_,_ in bindings]!=[b['id'] for b in plan['blocks']]:return False
        global_proof=record['provenance']['global'];row=rows[global_proof['review_id']]
        if row['state']!='completed' or row['stage']!='section_global' or row.get('result_hash')!=digest(row.get('result')) or row['result_hash']!=global_proof['result_hash']:return False
        expected,_,_,ledger=global_material(ctx,state,bindings)
        if row['context_hash']!=digest(row['context']) or row['context']['model_id']!=expected['model_id'] or row['context'].get('reconcile_projection_hash')!=expected.get('reconcile_projection_hash'):return False
        if row['context'].get('extra',{}).get('reuse_key')!=expected['extra']['reuse_key'] or row['context']['section_hashes']!=record['context']['section_hashes']:return False
        answer=GlobalAudit.model_validate(row['result']);validate_global(answer,ledger,ctx,state)
        actual=[i for _,r,_ in bindings for i in r.issues]+answer.issues
        if record['result']!={'issues':list({digest(i.model_dump()):i.model_dump() for i in actual}.values())}:return False
        return True
    except (KeyError,ValueError,TypeError):return False
