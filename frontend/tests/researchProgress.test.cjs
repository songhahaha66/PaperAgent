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
  const { researchHeadline, researchExecution, currentActivity, orderedQuestions } = await loadModule('src/utils/researchProgress.ts', {})
  const question = (id, title, experimentId, judgment = '') => ({
    id, title, question: title, experiment_ids: [experimentId], sources: [],
    judgment, artifact_refs: judgment ? ['R2'] : [], limitations: judgment ? ['仅适用于当前样本'] : [],
    next_step: '', issues: [], judgments: judgment ? [{ question_id: id, statement: judgment,
      artifact_refs: ['R2'], limitations: ['仅适用于当前样本'], next_step: '' }] : [],
    answer_gaps: judgment ? [] : ['尚未说明问题已答、部分回答或未答。'],
  })
  const experiment = (id, questionId, title) => ({
    id, task_id: id, question_id: questionId, title, objective: title, kind: 'compute',
    revision: 1, dependencies: [], execution_status: 'succeeded', acceptance: 'accepted',
    validity: 'current', progress: { state: 'accepted' }, availability: 'current',
    results: [], artifacts: [], summary: '', reason: '已提交实验结果', experiment: null,
  })
  const judgment = '当前拆分的误差已得到验证，外推能力仍未确定。'
  const snapshot = vue.ref({
    schema_version: 5, work_id: 'one', work_status: 'completed', research_id: 'research',
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
  const opened = [], actions = [], pauses = []
  const actionLabel = vue.ref('下载')
  const app = host.renderer.createApp({
    setup: () => () => vue.h(component.default, { snapshot: snapshot.value, onAsk: (id) => asked.push(id), onObject: (value) => opened.push(value),
      actionLabel: actionLabel.value, onAction: () => actions.push(true), onPause: () => pauses.push(true) }),
  })
  app.mount(host.root)
  await flush()
  assert.doesNotMatch(mainText(host.root), /项可用结果|研究问题|已回答/)
  assert.match(mainText(host.root), /本次执行已结束/)
    assert.match(mainText(host.root), /尚未形成结论，已有计算和输出可在执行记录中查看/)
  assert.doesNotMatch(mainText(host.root), /研究验收通过|Q1|E1|question:/)
  assert.doesNotMatch(mainText(host.root), /当前结论待更新/)
  assert.equal(researchHeadline(snapshot.value, 'completed', true), '已有实验结果，部分问题尚无判断')
  // Delegated and literature tasks can deliver without a parent experiment.
  assert.equal(researchHeadline({ ...snapshot.value, experiments: [], artifacts: ['paper.md'] },
    'completed', true), '论文产物已保存，可查看')
  assert.equal(researchHeadline({ ...snapshot.value, experiments: [], results: [],
    questions: [question('q', '独立子任务', '', judgment)] }, 'completed', true),
    '已记录问题判断，论文产物尚不可用')

  const selectSecond = host.find((node) => node.kind === 'button' && text(node).includes('预测误差是否可接受？'))
  selectSecond.props.onClick()
  await flush()
  assert.match(mainText(host.root), /当前结论/)
  assert.match(mainText(host.root), /当前拆分的误差已得到验证，外推能力仍未确定/)
  assert.match(mainText(host.root), /仅适用于当前样本/)
  assert.doesNotMatch(mainText(host.root), /验证误差|误差验证实验/)
  assert.doesNotMatch(mainText(host.root), /尚未形成结论，已有计算和输出可在执行记录中查看/)
  host.find((node) => node.kind === 'button' && text(node).trim() === '继续讨论').props.onClick()
  assert.equal(asked.at(-1), 'question:E2')

  // Invalidating the current judgment must retain the only historical judgment.
  snapshot.value = { ...snapshot.value, questions: snapshot.value.questions.map((item) =>
    item.id === 'question:E2' ? { ...item, judgment: '', artifact_refs: [] } : item) }
  await flush()
  assert.match(mainText(host.root), /尚未形成结论，已有计算和输出可在执行记录中查看/)
  const previous = host.find((node) => node.kind === 'details' && text(node).includes('之前的判断'))
  assert.ok(previous, 'a single invalidated judgment remains available as history')
  assert.match(text(previous), /当前拆分的误差已得到验证，外推能力仍未确定/)
  assert.doesNotMatch(mainText(host.root), /当前拆分的误差已得到验证/)
  // A stale answer is available only in disclosure, never presented as current.
  snapshot.value = { ...snapshot.value, questions: [{ ...question('q', '单一问题', 'E1', judgment),
    answer_status: 'needs_update', answer_gaps: ['依据已有变化'] }], artifacts: ['paper.pdf'],
    runs: [{ id: 'run_one', status: 'success', input_path: 'code.py', stdout_path: 'output.txt' }] }
  await flush()
  assert.match(mainText(host.root), /当前结论待更新/)
  assert.doesNotMatch(mainText(host.root), /当前拆分的误差已得到验证/)
  assert.equal(host.find((node) => node.kind === 'nav'), undefined, 'a single question needs no selector')
  host.find((node) => node.kind === 'button' && text(node).trim() === '打开报告').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(opened.at(-1))), { type: 'paper' })
  host.find((node) => node.kind === 'button' && text(node).trim() === '查看代码').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(opened.at(-1))), { type: 'file', path: 'code.py' })
  host.find((node) => node.kind === 'button' && text(node).trim() === '查看输出').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(opened.at(-1))), { type: 'file', path: 'output.txt' })

  // Report availability is independent of the execution outcome. Recovery uses
  // the parent's existing action, and merely discussing does not start a run.
  actionLabel.value = '继续'
  snapshot.value = { ...snapshot.value, work_status: 'failed', execution_reason: { message: '绘图执行失败' } }
  await flush()
  assert.match(mainText(host.root), /本次执行失败/)
  assert.match(mainText(host.root), /报告已生成/)
  assert.match(mainText(host.root), /绘图执行失败/)
  assert.doesNotMatch(mainText(host.root), /验证通过|已完成检查/)
  host.find((node) => node.kind === 'button' && text(node).trim() === '继续').props.onClick()
  assert.equal(actions.length, 1)
  snapshot.value = { ...snapshot.value, work_status: 'in_progress', runtime: {
    actions: [{ id: 'active', status: 'running', label: '正在比较预测误差', started_at: 1 }],
  } }
  await flush()
  assert.match(mainText(host.root), /正在比较预测误差/)
  snapshot.value.runtime.models = [{ id:'model',kind:'model',status:'sent',label:'执行实验',started_at:3 }]
  await flush()
  assert.match(mainText(host.root), /等待模型响应/)
  assert.equal(researchHeadline(snapshot.value,'in_progress',true),'等待模型响应')
  snapshot.value.runtime.models[0].status='http_response'
  assert.equal(researchHeadline(snapshot.value,'in_progress',true),'正在接收模型响应')
  assert.doesNotMatch(researchHeadline(snapshot.value,'completed',true),/等待模型|接收模型|正在处理实验/)
  host.find((node) => node.kind === 'button' && text(node).trim() === '暂停执行').props.onClick()
  assert.equal(pauses.length, 1)
  snapshot.value = { ...snapshot.value, work_status: 'waiting' }
  await flush()
  assert.match(mainText(host.root), /等待补充必要信息/)
  snapshot.value = { ...snapshot.value, work_status: 'completed', questions: [question('q', '图表问题', 'E1', judgment)],
    results: [{ id: 'F1', kind: 'figure', subproblem_id: 'E1', name: '预测图', figure_path: 'outputs/plot.png' },
      { id: 'F2', kind: 'figure', subproblem_id: 'E2', name: '无关图', figure_path: 'outputs/other.png' }] }
  await flush()
  assert.match(mainText(host.root), /预测图/)
  assert.doesNotMatch(mainText(host.root), /无关图/)
  host.find((node) => node.kind === 'button' && text(node).trim() === '预测图').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(opened.at(-1))), { type: 'file', path: 'outputs/plot.png' })
  const originalQuestions = [question('Q1', '问题3 滚动调整', ''),
    question('Q2', '问题1 日前优化', ''), question('Q3', '问题2 预测', ''),
    question('Q4', '问题4 波动电价', '')]
  const originalOrder = originalQuestions.map((q) => q.id).join(',')
  assert.equal(orderedQuestions(originalQuestions).map((q) => q.id).join(','), 'Q2,Q3,Q1,Q4')
  assert.equal(originalQuestions.map((q) => q.id).join(','), originalOrder, 'display sorting never mutates model-visible questions')
  const plainQuestions = [question('Q3', '预测能力如何？', ''), question('Q1', '怎样计算误差？', '')]
  assert.equal(orderedQuestions(plainQuestions).map((q) => q.id).join(','), 'Q3,Q1', 'opaque IDs do not determine source order')
  assert.equal(orderedQuestions([question('a','第十二问 方法',''), question('b','问题二 校验','')]).map(q => q.id).join(','), 'b,a')
  snapshot.value = { ...snapshot.value, questions: originalQuestions, work_status: 'in_progress', artifacts: [],
    runtime: { current: { id: 'stale', label: '旧操作', status: 'running', started_at: 1 },
      actions: [{ id: 'stale', label: '旧操作', status: 'failed', finished_at: 2, started_at: 1 },
        { id: 'retry', label: '重新计算', status: 'running', started_at: 3 }] } }
  await flush()
  const navigation = host.find(node => node.kind === 'nav')
  assert.equal((navigation.children || []).filter(node => node.kind === 'button').map(node => text(node).trim()).join('|'),
    '问题1 日前优化|问题2 预测|问题3 滚动调整|问题4 波动电价')
  assert.equal(researchExecution(snapshot.value).current.id, 'retry')
  assert.equal(researchHeadline(snapshot.value, 'in_progress', true), '重新计算')
  assert.match(mainText(host.root), /重新计算/)
  assert.match(mainText(host.root), /报告尚未生成/)
  assert.match(mainText(host.root), /尚未形成结论/)
  snapshot.value.runtime.actions[1] = { ...snapshot.value.runtime.actions[1], status: 'completed', finished_at: 4 }
  await flush()
  assert.equal(currentActivity(snapshot.value.runtime), null, 'a stale runtime.current cannot revive a finished call')
  assert.equal(researchHeadline(snapshot.value, 'in_progress', true), '研究执行中', 'between calls is still an active research turn')
  assert.equal(researchExecution(snapshot.value, 'paused').current, null)
  app.unmount()
  console.log('PASS: question selection, judgments, available results and invalidated judgment history use question records')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
