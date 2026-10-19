const assert=require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
const {hostRenderer}=require('./vueHost.cjs')
const text=n=>[n.text||'',...(n.children||[]).map(text)].join('')
;(async()=>{
 let response={research_id:'study',research_revision:2,tasks:[{stage:'sections',label:'章节一致性',version:'a'.repeat(64),state:'failed',coverage:'逐块核查',can_resume:true,segmented:{total_blocks:3,completed_blocks:2,covered_chars:4000,total_chars:7000,remaining_requests_upper_bound:2,blocks:[{id:'b1',section:'evaluation',revision:1,start:0,end:2000,window_start:0,window_end:2000,state:'completed'}],global:{state:'pending',scope:'独立的跨节核查'}}}],history:[],pending_candidates:[]}
 const m=await loadModule('src/components/ReviewTasks.vue',{'vue':vue,'@/api/researchSetup':{reviewTasksAPI:{read:async()=>response,recover:async()=>({state:'running'})}}},{setInterval(){return 1},clearInterval(){},crypto:{randomUUID:()=> 'fixture-command'}},true)
 const host=hostRenderer(),app=host.renderer.createApp(m.default,{workId:'work'});app.mount(host.root);await flush()
 assert.match(text(host.root),/正文覆盖 2 \/ 3 块/);assert.match(text(host.root),/跨节结论：待审查/)
 response={...response,tasks:[{...response.tasks[0],segmented:{...response.tasks[0].segmented,completed_blocks:3,covered_chars:7000,global:{state:'failed',scope:'独立的跨节核查'}}}]}
 await host.find(n=>n.kind==='button'&&text(n)==='刷新审查').props.onClick();await flush()
 assert.match(text(host.root),/正文覆盖 3 \/ 3 块/);assert.match(text(host.root),/跨节结论：失败，可恢复/);assert.match(text(host.root),/章节一致性 · 失败，可恢复/)
 response={...response,tasks:[{...response.tasks[0],state:'completed',can_resume:false,segmented:{...response.tasks[0].segmented,global:{state:'completed',scope:'独立的跨节核查'}}}]}
 await host.find(n=>n.kind==='button'&&text(n)==='刷新审查').props.onClick();await flush();assert.match(text(host.root),/跨节结论：已完成/)
 app.unmount();console.log('segmented progress preserves local coverage and keeps global failure separate from success')
})().catch(e=>{console.error(e);process.exitCode=1})
