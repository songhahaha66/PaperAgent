const assert=require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
const {hostRenderer}=require('./vueHost.cjs')
const text=n=>n.kind==='comment'?'':[n.text||'',...(n.children||[]).map(text)].join('')
;(async()=>{
  const component=await loadModule('src/components/JsonChatRenderer.vue',{
    vue,
    '@/components/MarkdownRenderer.vue':{default:{props:['content'],setup:p=>()=>vue.h('div',{class:'markdown'},p.content)}},
    './ConversationOperations.vue':{default:{props:['operations'],setup:p=>()=>vue.h('aside',{},p.operations.map(i=>i.type==='plan'?'更新研究计划':i.label).join(' '))}},
  },{},true)
  const base={thread_id:'lead',turn_id:'turn',status:'completed',started_at:1,finished_at:2}
  const state=vue.reactive({messages:[{id:'reply',role:'assistant',datetime:'',avatar:'',content:'',isStreaming:false,
    metadata:{thread_id:'lead',turn_id:'turn',status:'completed'},items:[
      {...base,id:'before',sequence:1,type:'agentMessage',phase:'commentary',text:'先检查 {"type":"confirm"}。'},
      {...base,id:'tool',sequence:2,type:'commandExecution',tool_name:'run_python',call_id:'c',label:'计算统计量'},
      {...base,id:'child',thread_id:'child-thread',sequence:1,type:'agentMessage',text:'CHILD_ONLY'},
      {...base,id:'plan',sequence:3,type:'plan',steps:[{step:'核对数据',status:'completed',note:''}]},
      {...base,id:'answer',sequence:4,type:'agentMessage',phase:'final_answer',text:'最终结果 42。'},
      {...base,id:'sources',sequence:5,type:'notice',name:'research_context',content:{files:['data/input.csv'],result_ids:['R1']}},
      {...base,id:'confirm',sequence:6,type:'inputRequest',name:'paper_confirm',content:'请确认下一步。'},
    ]}],workStatus:'waiting'})
  const host=hostRenderer(),files=[],results=[];let confirmations=0
  const app=host.renderer.createApp({render:()=>vue.h(component.default,{...state,onOpenFile:p=>files.push(p),onOpenResult:id=>results.push(id),onConfirmPlan:()=>confirmations++})})
  app.mount(host.root);await flush()
  const rendered=text(host.root)
  assert.ok(rendered.indexOf('先检查')<rendered.indexOf('计算统计量'))
  assert.ok(rendered.indexOf('计算统计量')<rendered.indexOf('最终结果'))
  assert.doesNotMatch(rendered,/CHILD_ONLY/,'child items stay in their own thread')
  assert.match(rendered,/更新研究计划/)
  const answer=host.find(n=>n.props?.class==='markdown'&&text(n)==='最终结果 42。')
  assert.ok(answer&&answer.parent.kind!=='details','final answer stays outside tool details')
  const button=label=>host.find(n=>n.kind==='button'&&text(n).trim()===label)
  button('input.csv').props.onClick();button('查看实测结果').props.onClick()
  assert.deepEqual(files,['data/input.csv']);assert.deepEqual(results,['R1'])
  button('继续研究').props.onClick();assert.equal(confirmations,1)
  state.messages.push({id:'user',role:'user',content:'新要求',datetime:'',avatar:''});await flush()
  assert.equal(button('继续研究'),undefined,'an earlier request cannot authorize a new turn')
  assert.match(text(host.root),/新要求/)
  app.unmount()
  console.log('PASS: typed messages, tools, plans, sources and scoped confirmation')
})().catch(e=>{console.error(e);process.exitCode=1})
