const assert=require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
;(async()=>{
 const source={issue:{id:'I1',task_id:'Q1',message:'未经检验使用显著',category:'writing',status:'confirmed'},route:'writing',version:'a'.repeat(64),section:'evaluation',quote:'唯一问题原文。',location:'精确原文（唯一匹配）',origin_current:true,active:true,evidence:[],evidence_note:'登记证据'}
 const doc={research_id:'study',commit_seq:5,source_hash:'b'.repeat(64),history:[],tasks:[],sections:[{key:'abstract',title:'摘要',revision:1,content_hash:'c'.repeat(64),text:'其他章节',validity:'current',task_refs:[]},{key:'evaluation',title:'评价',revision:2,content_hash:'d'.repeat(64),text:'当前章节。唯一问题原文。',validity:'current',task_refs:['Q1']}]}
 const payloads=[],events=[]
 const module=await loadModule('src/components/PaperRevision.vue',{'vue':vue,'@/api/paper':{paperAPI:{read:async()=>doc,issue:async()=>source,propose:async(work,p)=>{payloads.push(p);return {id:'candidate',source_issue:p.source_issue,status:'pending',section:'evaluation',before:doc.sections[1].text,after:p.content}}}},'@/lang/studio':{useStudioCopy:()=>({isZh:vue.ref(true)})}})
 const props=vue.reactive({workId:'work',latex:false,issueRequest:{id:'I1',at:1}}),scope=vue.effectScope()
 const editor=scope.run(()=>module.default.setup(props,{expose(){},emit:(...args)=>events.push(args)}));await flush();await flush()
 assert.equal(editor.selected.value,'evaluation');assert.equal(editor.fragment.value,source.quote);assert.equal(editor.instruction.value,source.issue.message)
 props.refreshKey=1;await flush();assert.equal(editor.fragment.value,source.quote,'Same-version background refresh must retain the located fragment')
 editor.manual.value=true;editor.draft.value='修订候选';await editor.propose();assert.equal(payloads[0].source_issue.id,'I1');assert.equal(payloads[0].source_issue.version,source.version)
 source.quote=null;props.issueRequest={id:'I1',at:2};await flush();await flush();assert.equal(editor.fragment.value,'','Missing quote cannot fabricate a fragment')
 source.route='research';source.issue.category='model';props.issueRequest={id:'I1',at:3};await flush();await flush();assert.equal(editor.mode.value,'research');await editor.research()
 const research=events.find(e=>e[0]==='research');assert.equal(research[2],'Q1');assert.equal(research[3].version,source.version);assert.match(research[1],/源意见 I1/)
 source.version='f'.repeat(64);editor.issueContext.value={...source,version:'a'.repeat(64)};await editor.research();assert.equal(events.filter(e=>e[0]==='research').length,1,'Changed opinion must not send research modification')
 scope.stop();console.log('issue location, honest missing quote, source-version payload and scientific routing passed')
})().catch(e=>{console.error(e);process.exitCode=1})
