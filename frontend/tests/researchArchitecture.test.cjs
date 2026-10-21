const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = n => [n.text || '', ...(n.children || []).map(text)].join('')
const mainText = n => n.kind === 'details' ? '' : [n.text || '', ...(n.children || []).map(mainText)].join('')
;(async () => {
  const module = await loadModule('src/components/ResearchExperiment.vue', {
    vue,
    '@/lang/studio': { useStudioCopy: () => ({ isZh: vue.ref(true) }) },
    './ResearchMeasurement.vue': { default: { props: ['result'], setup: p => () => vue.h('p', p.result.name) } },
    './MarkdownRenderer.vue': { default: { props: ['content'], setup: p => () => vue.h('p', p.content) } },
  }, { document: { querySelectorAll: () => [] } }, true)
  const experiment = vue.reactive({
    id: 'E1', task_id: 'E1', question_id: 'question:E1', title: '方法实验', objective: '回答原目标',
    execution_status: 'succeeded', acceptance: 'accepted', checking: 'unreviewed', availability: 'current',
    experiment: { conditions: '明确数据范围', evaluation: '保留统计口径', candidates: [] },
    primary_result_refs: ['R1'], record: { limitations: ['推广未知'] },
    results: [{ id: 'R1', name: '解释实际使用的结果', meaning: { object: '记录', scope: '全部输入', statistic: '原始记录数', lineage: [] } }, { id: 'R2', name: 'opaque_aux_var' }],
    claims: [{ id: 'C2', statement: '当前支持的解释', strength: '倾向' }],
    current_actions: [{ id: 'a1', label: '读取已有文件', status: 'completed', inputs: { path: 'saved.csv' } }],
    interpretation_history: [{ report_ref: 'old', status: 'superseded', report: { summary: '已撤回旧观点' } }],
    historical_claims: [], sources: [], artifacts: [], history: [], issues: [],
  })
  const host = hostRenderer()
  const app = host.renderer.createApp({ setup: () => () => vue.h(module.default, { workId: 'w', experiment, runs: [] }) })
  app.mount(host.root); await flush()
  assert.match(mainText(host.root), /回答原目标|明确数据范围/)
  assert.match(mainText(host.root), /当前支持的解释/)
  assert.match(mainText(host.root), /解释实际使用的结果/)
  assert.doesNotMatch(mainText(host.root), /opaque_aux_var|已撤回旧观点/)
  assert.match(text(host.root), /opaque_aux_var|已撤回旧观点|saved.csv/)
  assert.equal(host.find(n => n.kind === 'details' && text(n).includes('opaque_aux_var')) !== undefined, true)
  app.unmount()
  const names = await loadModule('src/utils/researchEvidence.ts', {})
  assert.equal(names.resultName('linear_valid_pred_sequence'), 'linear_valid_pred_sequence')
  console.log('PASS: current findings and cited evidence are primary; auxiliary records/history remain in details without inferred labels')
})().catch(error => { console.error(error); process.exitCode = 1 })
