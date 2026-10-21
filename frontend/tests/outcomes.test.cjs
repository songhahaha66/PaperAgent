const assert=require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
const {hostRenderer}=require('./vueHost.cjs')
const text=n=>[n.text||'',...(n.children||[]).map(text)].join('')
const copy={'@/lang/studio':{useStudioCopy:()=>({isZh:vue.ref(true)})}}
;(async()=>{
 const markdown={default:{props:['content'],setup:p=>()=>vue.h('p',p.content)}}
 const measurement={default:{props:['result'],setup:p=>()=>vue.h('p',(p.result.meaning?.statistic||p.result.name)+' '+p.result.value+' '+(p.result.unit||''))}}
 const module=await loadModule('src/components/ResearchExperiment.vue',{vue,...copy,'./MarkdownRenderer.vue':markdown,'./ResearchMeasurement.vue':measurement},{},true)
 const host=hostRenderer(),experiment=vue.reactive({id:'arbitrary-task',task_id:'arbitrary-task',question_id:'question:one',title:'不同领域的实验',objective:'核实给定观测',execution_status:'succeeded',acceptance:'accepted',computation:'succeeded',availability:'current',checking:'service_failed',method:null,primary_result_refs:['R8'],results:[{id:'R8',name:'opaque_v',kind:'number',value:'2.5',unit:'',description:'',run_id:'run_1'}],claims:[],issues:[],sources:[],artifacts:[],record:{limitations:[]},history:[]})
 const mainText=n=>n.kind==='details'?'':[n.text||'',...(n.children||[]).map(mainText)].join('')
 const app=host.renderer.createApp({setup:()=>()=>vue.h(module.default,{workId:'one',experiment,runs:[]})});app.mount(host.root);await flush()
 assert.match(mainText(host.root),/检查服务故障，未通过/)
 assert.match(text(host.root),/opaque_v/)
 assert.doesNotMatch(mainText(host.root),/opaque_v/,'Uninterpreted legacy variables remain in details')
 assert.doesNotMatch(text(host.root),/缺少含义说明/)
 assert.doesNotMatch(text(host.root),/RMSE|销量|万元|检查已完成/)
 assert.doesNotMatch(mainText(host.root),/下一步/,'A missing next step must not be invented')
 experiment.results=[{id:'R9',name:'forecast_internal',kind:'number',value:'1234.57',unit:'万千瓦时',description:'',run_id:'run_1',meaning:{object:'年度用电量',scope:'给定预测期',statistic:'用电量预测',lineage:[]}}]
 experiment.primary_result_refs=['R9'];await flush()
 assert.match(mainText(host.root),/用电量预测/);assert.match(mainText(host.root),/万千瓦时/)
 assert.doesNotMatch(text(host.root),/缺少含义说明|Meaning was not recorded/,'An optional description is not a result gate')
 experiment.checking='unreviewed';experiment.availability='historical';await flush()
 assert.match(text(host.root),/尚未完成审查/);assert.match(mainText(host.root),/保留的是历史结果/)
 experiment.results=[];experiment.execution_status='failed';experiment.acceptance='inconclusive';await flush()
 assert.match(mainText(host.root),/本次实验未完成/)
 assert.doesNotMatch(mainText(host.root),/1234.57|万千瓦时|关键依据/,'No measurements may be inferred after results are absent')
 app.unmount()
 const project={research_id:'draft',main:'paper.tex',version:'v1',has_source:false,files:[],compile:{state:'untracked',message:'尚未编译'},pdf_available:false,sources:[],threads:[],executions:[],changes:[]}
 const reads=[]
 const paper=await loadModule('src/components/PaperEditor.vue',{vue,...copy,'@/api/paper':{paperProjectAPI:{read:async(work)=>{reads.push(work);return project}}}},{},true)
 const empty=hostRenderer(),paperApp=empty.renderer.createApp(paper.default,{workId:'new',latex:true,snapshot:{has_research:true,artifacts:[]}});paperApp.mount(empty.root);await flush()
 assert.deepEqual(reads,['new'])
 assert.match(text(empty.root),/论文源码尚未生成/)
 assert.match(text(empty.root),/无需等待整项研究审查完成/)
 assert.match(text(empty.root),/阅读|源码|刷新/)
 assert.doesNotMatch(text(empty.root),/选择章节|版本与候选历史|局部提案请求历史|生成修改对比/)
 assert.equal(empty.find(n=>n.kind==='textarea'),undefined)
 paperApp.unmount()
 // A draft uses the file project even while the research snapshot has no published artifacts.
 project.has_source=true
 project.files=[{path:'paper.tex',version:'file-v1',text:'未验收的草稿正文',size:30}]
 const draftModule=await loadModule('src/components/PaperEditor.vue',{vue,'@/api/paper':{paperProjectAPI:{read:async()=>project}}})
 const scope=vue.effectScope()
 const draft=scope.run(()=>draftModule.default.setup({workId:'new',latex:true,snapshot:{has_research:true,artifacts:[]}},{expose(){},emit(){}}))
 await flush()
 assert.equal(draft.project.value.files.length,1)
 draft.setView('source')
 assert.equal(draft.view.value,'source')
 assert.equal(draft.draft.value,'未验收的草稿正文')
 assert.equal(draft.selectedFile.value.path,'paper.tex')
 scope.stop()
 console.log('PASS: experiments retain sourced results and hide uninterpreted variables through service failure; empty papers and unpublished drafts use the file project API')
})().catch(e=>{console.error(e);process.exitCode=1})
