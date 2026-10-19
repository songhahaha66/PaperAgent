const assert = require('node:assert/strict')
const {loadModule,vue,pinia,flush}=require('./moduleHarness.cjs')
const data=new Map()
const sessionStorage={getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value)}
;(async()=>{
  const auth=vue.reactive({token:'test',currentUser:{id:7}})
  const panels=await loadModule('src/stores/workPanels.ts',{vue,pinia,'@/stores/auth':{useAuthStore:()=>auth}},{sessionStorage})
  const first=panels.useWorkPanelsStore(pinia.createPinia())
  first.openObject('study',{type:'file',path:'./paper.md'})
  first.openObject('study',{type:'paper'})
  first.open('study','progress')
  first.open('study','progress')
  first.openObject('study',{type:'result',resultType:'registered',id:'R3'})
  first.openObject('study',{type:'result',resultType:'registered',id:'R3'})
  first.openObject('study',{type:'literature',evidenceId:'C2'})
  const state=first.session('study')
  state.draft='尚未发送的研究问题';state.scroll['result:registered:R3']=270
  assert.equal(state.panels.filter(p=>p.kind==='paper').length,1)
  assert.equal(state.panels.filter(p=>p.kind==='progress').length,1)
  assert.equal(state.panels.filter(p=>p.kind==='result').length,1)
  const restored=panels.useWorkPanelsStore(pinia.createPinia()).session('study')
  assert.equal(restored.draft,'尚未发送的研究问题')
  assert.equal(restored.activeId,'materials')
  assert.equal(restored.panels.find(p=>p.kind==='materials').location,'C2')
  assert.equal(restored.scroll['result:registered:R3'],270)
  auth.currentUser={id:8}
  assert.equal(panels.useWorkPanelsStore(pinia.createPinia()).session('study').draft,'')
  console.log('PASS: canonical objects deduplicate, and refresh restores user-scoped tabs, reading position and citation location')

  const paper={research_id:'study',commit_seq:8,source_hash:'a'.repeat(64),history:[],tasks:[],sections:[
    {key:'abstract',title:'摘要',revision:1,content_hash:'b'.repeat(64),text:'原摘要',task_refs:[]},
    {key:'evaluation',title:'局限',revision:1,content_hash:'c'.repeat(64),text:'原局限',task_refs:[]},
  ]}
  const module=await loadModule('src/components/PaperRevision.vue',{vue,'@/api/paper':{paperAPI:{read:async()=>paper}},'@/lang/studio':{useStudioCopy:()=>({isZh:vue.ref(true)})}},{sessionStorage})
  const mount=()=>{const scope=vue.effectScope();return {scope,editor:scope.run(()=>module.default.setup({workId:'study',ownerId:7,latex:false},{expose(){},emit(){}}))}}
  const one=mount();await flush()
  one.editor.manual.value=true;one.editor.draft.value='未保存的摘要编辑';one.editor.instruction.value='更清晰'
  one.editor.selected.value='evaluation';await flush()
  one.editor.manual.value=true;one.editor.draft.value='未保存的局限编辑';await flush();one.scope.stop()
  const two=mount();await flush()
  assert.equal(two.editor.draft.value,'未保存的局限编辑')
  two.editor.selected.value='abstract';await flush()
  assert.equal(two.editor.instruction.value,'更清晰')
  assert.equal(two.editor.draft.value,'未保存的摘要编辑')
  assert.equal(two.editor.draftCommit.value,8)
  two.editor.selected.value='evaluation';await flush()
  assert.equal(two.editor.draft.value,'未保存的局限编辑')
  assert.equal(two.editor.manual.value,true)
  two.scope.stop()
  console.log('PASS: manual section drafts and original base commits survive section switches and editor remounts')
})().catch(e=>{console.error(e);process.exitCode=1})
