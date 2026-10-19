const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
;(async () => {
  const pending = []
  const mod = await loadModule('src/components/PaperEditor.vue', {
    vue,
    '@/api/paper': { paperAPI: { read: work => new Promise(resolve => pending.push({ work, resolve })) } },
  })
  const section = (key, tasks, refs) => ({ key, title: key, text: key, readable: key, task_refs: tasks, evidence_refs: refs, validity: 'current' })
  const doc = { sections: [section('abstract', ['Q1','Q2'], []), section('solve:Q1', ['Q1'], ['R1']), section('solve:Q2', ['Q2'], ['R2'])],
    outline: [{ key:'abstract',title:'摘要',level:2,available:true },{ key:'solve',title:'求解',level:2,available:false },{ key:'solve:Q1',title:'预测',level:3,available:true },{ key:'solve:Q2',title:'评估',level:3,available:true }],
    artifacts:['paper.pdf'], feedback:[{id:'I1',section:'solve:Q1'},{id:'I2',section:'solve:Q2'}] }
  const props = vue.reactive({ workId:'A',latex:true,snapshot:{ results:[{id:'R1',subproblem_id:'Q1'},{id:'R2',subproblem_id:'Q2'}],runs:[] } })
  const scope = vue.effectScope()
  const reader = scope.run(() => mod.default.setup(props,{emit(){},expose(){}}))
  pending[0].resolve(doc); await flush()
  assert.equal(reader.view.value,'pdf','A real PDF is the default reading view')
  assert.equal(reader.outlineTarget('solve'),'solve:Q1','Structural headings navigate to the first child')
  reader.setView('body'); await reader.navigate('solve:Q2')
  assert.equal(reader.evidence.value.length,1); assert.equal(reader.evidence.value[0].id,'R2')
  assert.equal(reader.feedback.value.length,1); assert.equal(reader.feedback.value[0].id,'I2')
  const refresh = reader.load(); pending[1].resolve(doc); await refresh
  assert.equal(reader.view.value,'body','A background refresh must not discard the chosen reading mode')
  props.workId='B';await flush();props.workId='C';await flush()
  pending[3].resolve({...doc,artifacts:[]});await flush();pending[2].resolve(doc);await flush()
  assert.equal(reader.pdfAvailable.value,false,'A late response from another project must not expose its PDF')
  reader.setView('pdf');assert.match(reader.pdfStatus.value.title,/尚未生成/)
  scope.stop()
  console.log('PASS: ordered paper navigation, real PDF availability, scoped evidence and cross-project response guards')
})().catch(error => { console.error(error);process.exitCode=1 })
