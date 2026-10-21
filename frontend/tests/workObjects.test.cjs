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
  first.openObject('study',{type:'file',path:'outputs/reports/method.json'})
  const state=first.session('study')
  state.draft='尚未发送的研究问题';state.scroll['result:registered:R3']=270
  assert.equal(state.panels.filter(p=>p.kind==='paper').length,1)
  assert.equal(state.panels.filter(p=>p.kind==='progress').length,1)
  assert.equal(state.panels.filter(p=>p.kind==='result').length,1)
  const restored=panels.useWorkPanelsStore(pinia.createPinia()).session('study')
  assert.equal(restored.draft,'尚未发送的研究问题')
  assert.equal(restored.activeId,'file:outputs/reports/method.json')
  assert.equal(restored.panels.find(p=>p.kind==='file').path,'outputs/reports/method.json')
  assert.equal(restored.scroll['result:registered:R3'],270)
  auth.currentUser={id:8}
  assert.equal(panels.useWorkPanelsStore(pinia.createPinia()).session('study').draft,'')
  console.log('PASS: canonical objects deduplicate, and refresh restores user-scoped tabs, reading position and file identity')

  const project={research_id:'study',version:'project-v1',main:'paper.tex',has_source:true,files:[
    {path:'paper.tex',version:'main-v1',text:'原摘要',size:9},
    {path:'chapters/evaluation.tex',version:'chapter-v1',text:'原局限',size:9},
  ],sources:[{key:'C2',title:'已登记文献来源'}],threads:[],executions:[],changes:[],feedback:[],compile:{state:'untracked'},pdf_available:false}
  const proposals=[]
  const module=await loadModule('src/components/PaperEditor.vue',{vue,'@/api/paper':{paperProjectAPI:{read:async()=>project,propose:async(...args)=>proposals.push(args)}}},{sessionStorage})
  const mount=()=>{const scope=vue.effectScope();const props=vue.reactive({workId:'study',ownerId:7,latex:true,citationRequest:null});return {scope,props,editor:scope.run(()=>module.default.setup(props,{expose(){},emit(){}}))}}
  const one=mount();await flush()
  one.editor.setView('source');one.editor.draft.value='未保存的摘要编辑';await flush()
  one.editor.file.value='chapters/evaluation.tex';await flush()
  one.editor.draft.value='未保存的局限编辑';await flush();one.scope.stop()
  const two=mount();await flush()
  assert.equal(two.editor.view.value,'source')
  assert.equal(two.editor.draft.value,'未保存的摘要编辑')
  assert.equal(two.editor.draftVersion.value,'project-v1')
  two.editor.file.value='chapters/evaluation.tex';await flush()
  assert.equal(two.editor.draft.value,'未保存的局限编辑')
  assert.equal(two.editor.dirty.value,true)
  project.version='project-v2';await two.editor.load();await flush()
  assert.equal(two.editor.draft.value,'未保存的局限编辑')
  assert.equal(two.editor.draftVersion.value,'project-v1','A background version update must not rebase an unsaved draft')
  two.props.citationRequest={id:'C2',at:1};await flush()
  assert.equal(two.editor.side.value,'sources')
  assert.equal(two.editor.sourceCard.value.key,'C2','Citation requests resolve registered sources in the current file project')
  assert.equal(proposals.length,0,'Reading a source or restoring drafts does not submit an edit')
  two.scope.stop()
  console.log('PASS: file drafts retain original versions through switches, refresh and remount; citations open current project sources')
})().catch(e=>{console.error(e);process.exitCode=1})
