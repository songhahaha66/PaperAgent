const assert=require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
;(async()=>{
  const pending=[],recoveries=[],events=[],timers=[]
  const view=(research,state='failed',can_resume=true)=>({research_id:research,research_revision:1,history:[],pending_candidates:[],tasks:[{stage:'sections',label:'章节一致性',version:research.repeat(64).slice(0,64),state,coverage:'全文',can_resume}]})
  const mod=await loadModule('src/components/PaperChecks.vue',{
    vue:{...vue,onMounted(fn){fn()},onUnmounted(){}},
    '@/api/researchSetup':{reviewTasksAPI:{read(work){return new Promise(resolve=>pending.push({work,resolve}))},recover(work,body){return new Promise(resolve=>recoveries.push({work,body,resolve}))}}},
  },{crypto:{randomUUID:()=> 'nonce'},setInterval(fn){timers.push(fn);return 1},clearInterval(){}})
  const props=vue.reactive({workId:'one'}),scope=vue.effectScope()
  const panel=scope.run(()=>mod.default.setup(props,{expose(){},emit:(...args)=>events.push(args)}))
  assert.equal(pending[0].work,'one')
  props.workId='two';await flush();pending[1].resolve(view('b'));await flush();pending[0].resolve(view('a'));await flush()
  assert.equal(panel.state.value.research_id,'b','Late reads from a prior work must be discarded')
  const task=panel.state.value.tasks[0],first=panel.recover(task),second=panel.recover(task)
  assert.equal(recoveries.length,1,'Repeated clicks must not create a second POST')
  assert.equal(recoveries[0].body.research_id,'b');assert.equal(recoveries[0].body.command_id,'nonce')
  props.workId='three';await flush();pending[2].resolve(view('c','failed',false));await flush()
  recoveries[0].resolve({state:'running'});await Promise.all([first,second]);await flush()
  assert.equal(panel.state.value.research_id,'c');assert.equal(events.length,0,'Old recovery response cannot affect the new work')
  await panel.recover(panel.state.value.tasks[0]);assert.equal(recoveries.length,1,'Budget-disabled task cannot POST')
  panel.state.value=view('c','running',false);timers[0]();assert.equal(pending.length,4,'Active reviews poll after reconnect')
  pending[3].resolve(view('c','completed',false));await flush();timers[0]();assert.equal(pending.length,4)
  scope.stop();console.log('review task recovery, polling, budget and cross-work response guards passed')
})().catch(e=>{console.error(e);process.exitCode=1})
