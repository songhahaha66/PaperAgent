const assert = require('node:assert/strict')
const { loadModule } = require('./moduleHarness.cjs')

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
  console.log('PASS: chat execution status follows real runs, files and model calls without an answer ledger')
})().catch(error => { console.error(error); process.exitCode = 1 })
