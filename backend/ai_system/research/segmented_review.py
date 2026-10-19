"""Deterministic coverage manifests, reusable local reviews and an independent global check.

Only review_records are durable. Core spans partition every source character;
left-neighbour windows overlap intentionally and do not count twice as coverage.
"""

from ai_system.research.domain import findings as research_findings
import asyncio,json,re,time
from contextlib import closing
from pydantic import BaseModel,Field
from .store import digest
from .review_cache import ReviewCache,ReviewInProgress,ReviewSuperseded,review_context,science_version
from .runtime import ResearchRuntime,ResearchBudgetExhausted
from ..graph.state import ClaimAudit,ClaimIssue
from ..graph.llm_utils import as_messages,call_structured,cap_output

POLICY='segmented-sections-v1'
ATOM_LIMIT=3000
WINDOW_LIMIT=6000
GLOBAL_LIMIT=26000
MAX_BLOCKS=128

class CoverageError(ValueError):pass


def needed(sections):return sum(len(text) for text in sections.values())>WINDOW_LIMIT

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


def plan_for(sections,versions=None):
    blocks=[];metadata={};versions=versions or {}
    for section,text in sections.items():
        previous_version=versions.get(section,{})
        revision=previous_version.get('revision',1)
        if previous_version.get('content_hash') and previous_version['content_hash']!=digest(text):revision+=1
        metadata[section]={'hash':digest(text),'length':len(text),'revision':revision}
        previous=None
        for start,end in atoms(text):
            window_start=previous[0] if previous else start
            core=text[start:end];window=text[window_start:end]
            binding={'section':section,'section_hash':digest(text),'revision':metadata[section]['revision'],'start':start,'end':end,
                'window_start':window_start,'window_end':end,'core_hash':digest(core),'window_hash':digest(window)}
            blocks.append({**binding,'id':digest(binding),'text':core,'left':text[window_start:start]})
            previous=(start,end)
    if len(blocks)>MAX_BLOCKS:raise CoverageError('本次超过128个必需块，需先整理章节结构；未发送模型请求')
    plan={'policy':POLICY,'atom_limit':ATOM_LIMIT,'offset_unit':'unicode_code_point','sections':metadata,'blocks':blocks}
    plan['id']=digest(plan);validate_plan(plan)
    return plan


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


def make_plan(ctx,state):
    snapshot=ctx.research.snapshot() if getattr(ctx,'research',None) else {}
    return plan_for(state.get('sections') or {},(snapshot or {}).get('sections',{}))


def aggregate_context(ctx,state,plan=None,candidate=False):
    plan=plan or make_plan(ctx,state)
    context=review_context(ctx,state,'sections',POLICY,plan['id'],ClaimAudit,sections=state['sections'],extra={'non_blocking':candidate,'segmented_plan':plan,'prompt_contract':{'requirements':state['analysis']['requirements'],'assumptions':state['analysis']['assumptions'],'consistency':state.get('consistency') or []}})
    context['section_coverage']={'policy':POLICY,'complete':True,'sections':{k:{'total_chars':v['length'],'included_chars':v['length']} for k,v in plan['sections'].items()},'plan_id':plan['id']}
    return context


def _rows(store):
    with closing(store._connect()) as db:
        return [{**json.loads(r[1]),'state':r[0]} for r in db.execute('SELECT state,payload FROM review_records WHERE research_id=? ORDER BY rowid DESC',(store.research_id,))]


def block_material(ctx,state,block):
    from ai_system.modules.writing.consistency import AUDIT_SYSTEM,section_audit_prompt
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


def reusable(rows,stage,context,schema):
    key=context['extra']['reuse_key']
    for row in rows:
        if row['stage']!=stage or row['state']!='completed' or row.get('result_hash')!=digest(row.get('result')) or row['context'].get('extra',{}).get('reuse_key')!=key:continue
        if row['context_hash']!=digest(row['context']) or row['context'].get('model_id')!=context.get('model_id') or row['context'].get('reconcile_projection_hash')!=context.get('reconcile_projection_hash'):continue
        if row['context']['request_hash']!=context['request_hash'] or science_version(row['context'])!=science_version(context) or row['context'].get('citation_sources')!=context.get('citation_sources'):continue
        try:return schema.model_validate(row['result']),row
        except ValueError:pass
    return None


def _budget(ctx):
    value=ResearchRuntime(ctx.research.root,ctx.research.research_id).status()
    if value['state']=='provider_blocked' or (value.get('request_limit') is not None and value['used']>=value['request_limit']):raise ResearchBudgetExhausted('分段审查预算不足；已完成块保留，恢复不会清零')


async def child(ctx,state,stage,schema,context,system,prompt,validate):
    from .review_cache import run_review
    hit=reusable(_rows(ctx.research),stage,context,schema)
    if hit:validate(hit[0]);return hit
    async def perform():
        _budget(ctx)
        return await call_structured(cap_output(ctx.planner_llm,2600),schema,as_messages(system,prompt),validate=validate,retries=0)
    answer=await run_review(ctx,state,stage,schema,system,prompt,perform,sections={k:v for k,v in state['sections'].items()},extra=context['extra'],validate=validate)
    hit=reusable(_rows(ctx.research),stage,context,schema)
    if not hit:raise CoverageError('局部核查没有可验证的完成记录')
    return answer,hit[1]


def global_material(ctx,state,bindings):
    from ai_system.modules.writing.consistency import AUDIT_SYSTEM,section_audit_prompt
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
        state={'analysis':{'requirements':contract['requirements'],'assumptions':contract['assumptions'],'subproblems':[t['subproblem'] for t in store.snapshot()['tasks'].values() if not t['retired']]},'consistency':contract['consistency'],
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


async def audit(ctx,state,*,candidate=False):
    from .runtime import budget_scope
    with budget_scope(ctx.research.root,ctx.research.research_id):
        return await _audit(ctx,state,candidate=candidate)


async def _audit(ctx,state,*,candidate=False):
    plan=make_plan(ctx,state);context=aggregate_context(ctx,state,plan,candidate);cache=ReviewCache(ctx.research)
    hit=cache.get('sections',context,ClaimAudit)
    if hit:cache.reuse(hit[1],current_context=lambda:aggregate_context(ctx,state,candidate=candidate));return hit[0]
    try:record=cache.start('sections',context,'segmented-aggregate')
    except ReviewInProgress as running:return await cache.wait(running.record,ClaimAudit,lambda:aggregate_context(ctx,state,candidate=candidate))
    heartbeat=asyncio.create_task(cache.heartbeat(record))
    try:
        bindings=[]
        for block in plan['blocks']:
            selected={**state,'sections':{block['section']:block['left']+block['text']}}
            local,system,prompt=block_material(ctx,state,block)
            answer,row=await child(ctx,selected,'section_blocks',BlockAudit,local,system,prompt,lambda a,b=block:validate_block(a,b,ctx,state))
            bindings.append((block,answer,row))
        global_context,system,prompt,ledger=global_material(ctx,state,bindings)
        answer,global_row=await child(ctx,state,'section_global',GlobalAudit,global_context,system,prompt,lambda a:validate_global(a,ledger,ctx,state))
        findings=[i for _,result,_ in bindings for i in result.issues]+answer.issues
        unique={digest(i.model_dump()):i for i in findings};result=ClaimAudit(issues=list(unique.values()))
        proof={'blocks':[{'binding_id':b['id'],'review_id':r['id'],'result_hash':r['result_hash'],'reuse_key':r['context']['extra']['reuse_key']} for b,_,r in bindings],
            'global':{'review_id':global_row['id'],'result_hash':global_row['result_hash']},'policy':POLICY}
        cache.complete(record,result.model_dump(),provenance=proof,current_context=lambda:aggregate_context(ctx,state,candidate=candidate));return result
    except asyncio.CancelledError:
        cache.failed(record,RuntimeError('interrupted'),current_context=lambda:aggregate_context(ctx,state,candidate=candidate));raise
    except Exception as exc:
        cache.failed(record,exc,current_context=lambda:aggregate_context(ctx,state,candidate=candidate));raise
    finally:
        heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)


def progress(ctx,state):
    plan=make_plan(ctx,state);rows=_rows(ctx.research);done=0;covered=0;bindings=[];blocks=[]
    for b in plan['blocks']:
        context,_,_=block_material(ctx,state,b);hit=reusable(rows,'section_blocks',context,BlockAudit)
        if hit:
            try:validate_block(hit[0],b,ctx,state)
            except ValueError:hit=None
        matches=[r for r in rows if r['stage']=='section_blocks' and r['context'].get('extra',{}).get('reuse_key')==context['extra']['reuse_key']]
        latest=matches[0] if matches else None
        status='completed' if hit else 'running' if latest and latest['state']=='running' and latest.get('lease',{}).get('expires_at',0)>time.time() else 'failed' if latest else 'pending'
        if hit:done+=1;covered+=b['end']-b['start'];bindings.append((b,hit[0],hit[1]))
        blocks.append({k:b[k] for k in ('id','section','start','end','window_start','window_end','revision')}|{'state':status,'review_id':hit[1]['id'] if hit else latest['id'] if latest else None})
    global_state='pending';global_id=None
    global_reason=None
    if done==len(plan['blocks']):
        try:
            context,_,_,ledger=global_material(ctx,state,bindings);hit=reusable(rows,'section_global',context,GlobalAudit)
            if hit:
                try:validate_global(hit[0],ledger,ctx,state)
                except ValueError:hit=None
            if hit:global_state='completed';global_id=hit[1]['id']
            else:
                latest=next((r for r in rows if r['stage']=='section_global' and r['context'].get('extra',{}).get('reuse_key')==context['extra']['reuse_key']),None)
                if latest:global_state='running' if latest['state']=='running' else 'failed';global_id=latest['id']
        except CoverageError as exc:global_state='blocked';global_reason=str(exc)
    sections=[]
    for key,meta in plan['sections'].items():
        group=[b for b in blocks if b['section']==key];complete=[b for b in group if b['state']=='completed']
        sections.append({'section':key,'revision':meta['revision'],'completed_blocks':len(complete),'total_blocks':len(group),
            'covered_chars':sum(b['end']-b['start'] for b in complete),'total_chars':meta['length'],'local_complete':len(complete)==len(group)})
    return {'plan_id':plan['id'],'offset_unit':'unicode_code_point','sections':sections,'total_blocks':len(blocks),'completed_blocks':done,'covered_chars':covered,'total_chars':sum(s['length'] for s in plan['sections'].values()),
        'blocks':blocks,'global':{'state':global_state,'review_id':global_id,'scope':'全部块提取的可定位主张；独立于局部核查','reason':global_reason},
        'remaining_requests_upper_bound':len(blocks)-done+(global_state!='completed')}
