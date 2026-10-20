const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')

;(async () => {
  const component = await loadModule(
    'src/components/ResearchProgress.vue',
    {
      vue,
      './MarkdownRenderer.vue': {
        default: { props: ['content'], setup: (props) => () => vue.h('p', props.content) },
      },
    },
    {},
    true,
  )
  const { researchHeadline } = await loadModule('src/utils/researchProgress.ts', {})
  const question = (id, title, experimentId, judgment = '') => ({
    id, title, question: title, experiment_ids: [experimentId], sources: [],
    judgment, artifact_refs: judgment ? ['R2'] : [], limitations: judgment ? ['仅适用于当前样本'] : [],
    next_step: '', issues: [], judgments: judgment ? [{ question_id: id, statement: judgment,
      artifact_refs: ['R2'], limitations: ['仅适用于当前样本'], next_step: '' }] : [],
  })
  const experiment = (id, questionId, title) => ({
    id, task_id: id, question_id: questionId, title, objective: title, kind: 'compute',
    revision: 1, dependencies: [], execution_status: 'succeeded', acceptance: 'accepted',
    validity: 'current', progress: { state: 'accepted' }, availability: 'current',
    results: [], artifacts: [], summary: '', reason: '已提交实验结果', experiment: null,
  })
  const judgment = '当前拆分的误差已得到验证，外推能力仍未确定。'
  const snapshot = vue.ref({
    schema_version: 4, work_id: 'one', work_status: 'completed', research_id: 'research',
    research_revision: 1, commit_seq: 5, has_research: true,
    questions: [question('question:E1', '怎样预测需求？', 'E1'),
      question('question:E2', '预测误差是否可接受？', 'E2', judgment)],
    experiments: [experiment('E1', 'question:E1', '需求预测实验'), experiment('E2', 'question:E2', '误差验证实验')],
    results: [
      { id: 'R1', subproblem_id: 'E1', name: '预测需求', value: '8', unit: '', kind: 'number', run_id: 'run_one', figure_path: null },
      { id: 'R2', subproblem_id: 'E2', name: '验证误差', value: '0.2', unit: '', kind: 'number', run_id: 'run_two', figure_path: null },
    ],
    runs: [], claims: [], feedback: [], artifacts: [],
    delivery: { ready: true, blockers: [] }, delivery_stages: {},
  })
  const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
  const mainText = (node) => node.kind === 'details' ? '' : [node.text || '', ...(node.children || []).map(mainText)].join('')
  const host = hostRenderer()
  const asked = []
  const app = host.renderer.createApp({
    setup: () => () => vue.h(component.default, { snapshot: snapshot.value, onAsk: (id) => asked.push(id) }),
  })
  app.mount(host.root)
  await flush()
  assert.match(mainText(host.root), /2 个问题，2 项可用结果/)
  assert.match(mainText(host.root), /已有结果，尚无判断/)
  assert.match(mainText(host.root), /尚未形成判断。已有实验结果不代表该问题已回答/)
  assert.doesNotMatch(mainText(host.root), /研究验收通过|Q1|E1|question:/)
  assert.equal(researchHeadline(snapshot.value, 'completed', true), '已有实验结果，部分问题尚无判断')

  const selectSecond = host.find((node) => node.kind === 'button' && text(node).includes('预测误差是否可接受？'))
  selectSecond.props.onClick()
  await flush()
  assert.match(mainText(host.root), /当前判断/)
  assert.match(mainText(host.root), /当前拆分的误差已得到验证，外推能力仍未确定/)
  assert.match(mainText(host.root), /仅适用于当前样本/)
  assert.match(mainText(host.root), /验证误差/)
  assert.doesNotMatch(mainText(host.root), /尚未形成判断。已有实验结果不代表该问题已回答/)
  host.find((node) => node.kind === 'button' && text(node) === '追问当前问题').props.onClick()
  assert.equal(asked.at(-1), 'question:E2')

  // Invalidating the current judgment must retain the only historical judgment.
  snapshot.value = { ...snapshot.value, questions: snapshot.value.questions.map((item) =>
    item.id === 'question:E2' ? { ...item, judgment: '', artifact_refs: [] } : item) }
  await flush()
  assert.match(mainText(host.root), /尚未形成判断。已有实验结果不代表该问题已回答/)
  const previous = host.find((node) => node.kind === 'details' && text(node).includes('之前的判断'))
  assert.ok(previous, 'a single invalidated judgment remains available as history')
  assert.match(text(previous), /当前拆分的误差已得到验证，外推能力仍未确定/)
  assert.doesNotMatch(mainText(host.root), /当前拆分的误差已得到验证/)
  app.unmount()
  console.log('PASS: question selection, judgments, available results and invalidated judgment history use question records')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
