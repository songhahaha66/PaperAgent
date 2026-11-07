const assert=require('node:assert/strict')
const test=require('node:test')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
const {hostRenderer}=require('./vueHost.cjs')
const text=n=>n.kind==='comment'?'':[n.text||'',...(n.children||[]).map(text)].join('')
const item=(status='running',extra={})=>({id:'call',thread_id:'lead',turn_id:'turn',sequence:1,type:'commandExecution',tool_name:'run_python',call_id:'call',label:'核对数据',status,started_at:1,...extra})
async function mount(t) {
  let resolve,reject
  const component=await loadModule('src/components/ConversationTool.vue',{
    vue,'@/api/chat':{chatAPI:{resolveItems:()=>new Promise((a,b)=>{resolve=a;reject=b})}},
    './MarkdownRenderer.vue':{default:{props:['content'],setup:p=>()=>vue.h('p',{},p.content)}},
  },{setInterval:()=>1,clearInterval(){}},true)
  const state=vue.reactive({item:item(),items:[],replyId:'reply',workId:'work'})
  const host=hostRenderer(),files=[]
  const app=host.renderer.createApp({render:()=>vue.h(component.default,{...state,onFile:p=>files.push(p)})})
  app.component('ConversationTool',component.default)
  app.mount(host.root);await flush();t.after(()=>app.unmount())
  const heading=()=>host.find(n=>n.kind==='button'&&n.props?.['aria-controls']==='item-call')
  return {state,host,heading,files,resolve:v=>resolve(v),reject:()=>reject(new Error('offline'))}
}
test('one expansion displays streaming output and survives item replacement',async t=>{
  const m=await mount(t)
  assert.equal(m.heading().props['aria-expanded'],false)
  m.heading().props.onClick();await flush()
  m.state.item=item('running',{output_tail:'first\nsecond'});await flush()
  assert.equal(m.heading().props['aria-expanded'],true)
  assert.match(text(m.host.root),/first\nsecond/)
  m.resolve({items:[item('completed',{output_tail:'stale'})]});await flush()
  assert.doesNotMatch(text(m.host.root),/stale/,'late HTTP details cannot overwrite newer events')
  m.heading().props.onClick();await flush()
  assert.doesNotMatch(text(m.host.root),/second/)
})
test('failed operation retains its message and file actions after completion',async t=>{
  const m=await mount(t)
  m.state.item=item('failed',{message:'shape mismatch',finished_at:3,file:'outputs/result.csv',output_tail:'retained output'});await flush()
  assert.match(text(m.host.root),/shape mismatch/)
  assert.match(text(m.heading()),/失败/)
  m.heading().props.onClick();await flush()
  const file=m.host.find(n=>n.kind==='button'&&text(n)==='result.csv')
  file.props.onClick();assert.deepEqual(m.files,['outputs/result.csv'])
  m.reject();await flush();assert.match(text(m.host.root),/详情加载失败/)
})
test('compilation warnings use the same tool detail area',async t=>{
  const m=await mount(t)
  m.state.item=item('completed',{type:'toolCall',tool_name:'writing.export',label:'导出论文',warnings:['Overfull box'],detail:'15 页',finished_at:3});await flush()
  assert.doesNotMatch(text(m.host.root),/Overfull/)
  m.heading().props.onClick();await flush()
  assert.match(text(m.host.root),/Overfull/)
  assert.ok(m.host.find(n=>n.props?.['aria-label']==='编译警告'))
  assert.equal(m.host.find(n=>n.props?.class==='child-thread'),undefined,'ordinary tools have no inferred nesting')
})
test('only an explicit child_thread_id creates a nested thread',async t=>{
  const m=await mount(t)
  m.state.item=item('completed',{type:'collabToolCall',tool_name:'research.delegate',child_thread_id:'child',child_turn_id:'child-turn',finished_at:3})
  m.state.items=[{...item('completed'),id:'child-answer',thread_id:'child',turn_id:'child-turn',type:'agentMessage',text:'独立复核结果',phase:'final_answer'}]
  m.heading().props.onClick();await flush()
  assert.ok(m.host.find(n=>n.props?.['aria-label']==='子 Agent 会话'))
  assert.match(text(m.host.root),/独立复核结果/)
})
