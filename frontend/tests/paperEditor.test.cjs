const assert = require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')

;(async()=>{
  const payloads=[]
  const paper={research_id:'study',commit_seq:8,source_hash:'a'.repeat(64),history:[],tasks:[],sections:[{
    key:'evaluation',title:'局限',revision:2,validity:'current',content_hash:'b'.repeat(64),
    text:'保留长章节。'.repeat(2000)+'唯一短句。',readable:'',task_refs:['Q1'],evidence_refs:[],
  }]}
  const module=await loadModule('src/components/PaperEditor.vue',{
    vue,
    '@/api/paper':{paperAPI:{read:async()=>paper,propose:async(work,body)=>{
      payloads.push(JSON.parse(JSON.stringify(body)))
      return {id:'candidate',section:'evaluation',before:'唯一短句。',after:'另一短句。',status:'pending',section_revision:2}
    }}},
    '@/lang/studio':{useStudioCopy:()=>({isZh:vue.ref(true)})},
  })
  const effects=vue.effectScope()
  const editor=effects.run(()=>module.default.setup({workId:'work',latex:false},{expose(){},emit(){}}))
  await flush();editor.fragment.value='唯一短句。';editor.instruction.value='更简明';await flush()
  assert.equal(editor.fragmentReady.value,true)
  await editor.propose()
  assert.equal(payloads.length,1)
  assert.equal(payloads[0].fragment_scope.old,'唯一短句。')
  assert.equal(payloads[0].fragment_scope.section_hash,'b'.repeat(64))
  assert.equal(payloads[0].fragment_scope.source_hash,'a'.repeat(64))
  assert.equal(payloads[0].fragment_scope.section_revision,2)
  assert.equal(payloads[0].base_commit,8)
  assert.equal(payloads[0].content,undefined)
  assert(!JSON.stringify(payloads[0]).includes('保留长章节'))
  editor.manual.value=true;editor.draft.value='手动候选';await editor.propose()
  assert.equal(payloads[1].content,'手动候选')
  assert.equal(payloads[1].fragment_scope,undefined)
  editor.manual.value=false;editor.document.value.sections[0].text='aaaaa';editor.fragment.value='aaa';await flush()
  assert.equal(editor.fragmentReady.value,false,'Overlapping matches must not be offered as unique')
  effects.stop()
  console.log('paper editor fragment serialization and manual scope tests passed')
})().catch(error=>{console.error(error);process.exitCode=1})
