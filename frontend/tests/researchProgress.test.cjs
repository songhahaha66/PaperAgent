const assert = require('node:assert/strict')
const { loadModule, vue } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')

;(async () => {
  const { researchHeadline, researchExecution, currentActivity, orderedQuestions } = await loadModule('src/utils/researchProgress.ts', {})
  const snapshot = {
    work_status: 'completed', questions: [{ id: 'Q1', title: '需求预测' }],
    experiments: [], runs: [{ id: 'run_one', status: 'success' }], results: [], artifacts: [], delivery_stages: {},
  }
  assert.equal(researchHeadline(snapshot, 'completed', true), '已有运行记录与输出，论文产物尚不可用')
  // An older server response cannot resurrect the removed answer status display.
  assert.equal(researchHeadline({ ...snapshot, questions: [{ ...snapshot.questions[0],
    judgment: 'old answer', answer_status: 'needs_update', answer_gaps: ['old gap'] }] }, 'completed', true),
    '已有运行记录与输出，论文产物尚不可用')
  assert.equal(researchHeadline({ ...snapshot, artifacts: ['paper.md'] }, 'completed', true), '论文产物已保存，可查看')
  assert.equal(researchHeadline({ ...snapshot, runs: [] }, 'completed', true), '等待研究执行与产出')
  assert.equal(researchHeadline(snapshot, 'paused', true), '研究已暂停，进度已保留')
  const active = { ...snapshot, work_status: 'in_progress', runtime: {
    current: { id: 'old', status: 'running' },
    actions: [{ id: 'old', status: 'completed', started_at: 1, finished_at: 2 },
      { id: 'new', status: 'running', started_at: 3, label: '读取实际输出' }],
  } }
  assert.equal(currentActivity(active.runtime).id, 'new')
  assert.equal(researchExecution(active).current.id, 'new')
  assert.equal(researchHeadline(active, 'in_progress', true), '读取实际输出')
  active.runtime.models = [{ id: 'model', kind: 'model', status: 'sent', label: '研究计算', started_at: 4 }]
  assert.equal(researchHeadline(active, 'in_progress', true), '等待模型响应')
  assert.equal(researchExecution(active, 'paused').current, null)
  const questions = [{ id: 'Q1', title: '问题3 滚动调整' }, { id: 'Q2', title: '问题1 日前优化' }]
  assert.deepEqual(orderedQuestions(questions).map(q => q.id), ['Q2', 'Q1'])
  assert.deepEqual(questions.map(q => q.id), ['Q1', 'Q2'])
  const listeners = new Map()
  const { default: PlanProgress } = await loadModule('src/components/ResearchPlanProgress.vue', { vue }, {
    document: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) },
  }, true)
  const props = vue.reactive({ plan: null, isZh: true, workStatus: 'in_progress' })
  const host = hostRenderer()
  const app = host.renderer.createApp({ setup: () => () => vue.h(PlanProgress, props) })
  app.mount(host.root)
  assert.equal(host.find(n => n.kind === 'details'), undefined, 'old projects do not show invented progress')
  props.plan = { steps: [{ title: '读取数据', status: 'completed', note: '' },
    { title: '计算与分析', status: 'in_progress', note: '' },
    { title: '整理报告', status: 'pending', note: '' }], updated_at: 1793448000 }
  await vue.nextTick()
  assert.equal(host.find(n => n.props?.class === 'plan-count').text, '已完成 1 / 3 项')
  assert.equal(host.find(n => n.kind === 'li' && n.props?.class === 'in_progress').children[1].children[0].text, '计算与分析')
  props.plan = { ...props.plan, steps: props.plan.steps.map(s => ({ ...s, status: 'completed' })) }
  await vue.nextTick()
  assert.equal(host.find(n => n.props?.class === 'plan-count').text, '已完成 3 / 3 项')
  function nodeText(n) { return (n.text || '') + (n.children || []).map(nodeText).join('') }
  assert.equal(nodeText(host.find(n => n.props?.class === 'plan-current')), '全部完成')
  app.unmount()
  assert.equal(listeners.size, 0, 'leaving the workspace removes the outside-click listener')
  console.log('PASS: chat execution status follows real runs, files and model calls without an answer ledger')
})().catch(error => { console.error(error); process.exitCode = 1 })
