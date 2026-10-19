const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')

;(async () => {
  const component = await loadModule(
    'src/components/ResearchProgress.vue',
    {
      vue,
      '@/lang/studio': { useStudioCopy: () => ({ isZh: vue.ref(true) }) },
    },
    {},
    true,
  )
  const task = (id, state, dependencies = [], revision = 1) => ({
    task_id: id,
    objective: { Q1: '建立需求预测模型', Q2: '验证误差', Q3: '汇总结果', Q4: '复核容量' }[id] || '',
    kind: 'compute',
    revision,
    dependencies,
    execution_status: state === 'running' ? 'running' : 'succeeded',
    acceptance: state === 'accepted' ? 'accepted' : 'pending',
    validity: state === 'rework' ? 'stale' : 'current',
    progress: {
      state,
      waiting_for: state === 'waiting' ? dependencies : [],
      issue_ids: [],
      attempts: 1,
      attempt_limit: 3,
      rework: state === 'rework',
    },
  })
  const snapshot = vue.ref({
    work_id: 'one',
    work_status: 'in_progress',
    research_id: 'research',
    research_revision: 1,
    commit_seq: 5,
    tasks: [task('Q1', 'accepted'), task('Q2', 'waiting', ['Q1']), task('Q3', 'accepted')],
    issues: [],
    artifacts: [],
    results: [],
    delivery: { ready: false, blockers: [] },
    delivery_stages: {},
  })
  const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
  function mount(value) {
    const host = hostRenderer()
    const app = host.renderer.createApp({
      setup: () => () => vue.h(component.default, { snapshot: {...value.value, outcomes: value.value.tasks.map(t => ({task_id:t.task_id, checking:t.acceptance==='accepted' && t.validity==='current' ? 'passed' : 'pending',availability:'unavailable',results:[]}))} }),
    })
    app.mount(host.root)
    return { host, app }
  }
  const { host, app } = mount(snapshot)
  await flush()
  assert.match(text(host.root), /2\/3 已完成检查/)
  assert.match(text(host.root), /等待前置任务检查/)
  assert.match(text(host.root), /依赖 Q1/)
  const mainText = (node) =>
    node.kind === 'details'
      ? ''
      : [node.text || '', ...(node.children || []).map(mainText)].join('')
  assert.match(mainText(host.root), /建立需求预测模型已完成检查/)
  assert.doesNotMatch(mainText(host.root), /Q1|Q2|v1|当前版本尝试|依赖/)

  snapshot.value = {
    ...snapshot.value,
    research_revision: 2,
    tasks: [task('Q1', 'rework', [], 2), task('Q2', 'waiting', ['Q1'], 2), task('Q3', 'accepted')],
  }
  await flush()
  assert.match(text(host.root), /1\/3 已完成检查/)
  assert.match(text(host.root), /建立需求预测模型待返工.*运行记录/)
  assert.match(text(host.root), /汇总结果已完成检查/)
  snapshot.value = {
    ...snapshot.value,
    tasks: snapshot.value.tasks.map((task) =>
      task.task_id === 'Q1'
        ? {
            ...task,
            reason: '关键假设改变，需要重算',
            progress: { ...task.progress, issue_ids: ['recheck', 'second', 'old'] },
          }
        : task,
    ),
    issues: [
      { id: 'recheck', status: 'confirmed', message: '重新验证容量约束' },
      { id: 'second', status: 'suspected', message: '核对误差' },
      { id: 'old', status: 'resolved', message: '已处理' },
    ],
  }
  await flush()
  const reason = host.find((node) => node.props?.class === 'task-reason')
  assert.equal(reason.text, '关键假设改变，需要重算')
  assert.match(mainText(host.root), /有 2 项问题需要处理/)
  assert.match(mainText(host.root), /重新验证容量约束/)
  assert.equal(
    reason.parent.props.class,
    'task-content',
    'the reason is visible without opening details',
  )
  const details = host.find((node) => node.kind === 'details')
  assert.equal(details.props.open, undefined, 'version, attempts and issue records start folded')
  assert.match(text(details), /运行记录.*问题记录 recheck、second/)

  snapshot.value = {
    ...snapshot.value,
    tasks: [
      task('Q1', 'rework', ['Q4'], 2),
      ...snapshot.value.tasks.slice(1),
      { ...task('Q4', 'running'), kind: 'verify' },
    ],
  }
  await flush()
  assert.match(text(host.root), /1\/4 已完成检查/)
  assert.match(text(host.root), /独立核验任务/)
  assert.equal(
    host.find((node) => node.kind === 'li').props['data-task'],
    'Q4',
    'new upstream checks appear before their consumers',
  )
  snapshot.value = {
    ...snapshot.value,
    work_status: 'paused',
    delivery_stages: { validate: { status: 'blocked', note: '章节仍有缺项' } },
  }
  await flush()
  assert.match(text(host.root), /已中断，待恢复/)
  assert.match(text(host.root), /论文校验 · 需处理.*章节仍有缺项/)
  const serialized = JSON.parse(JSON.stringify(snapshot.value))
  app.unmount()
  const restored = mount(vue.ref(serialized))
  await flush()
  assert.match(text(restored.host.root), /1\/4 已完成检查/)
  assert.match(text(restored.host.root), /汇总结果已完成检查/)
  assert.match(text(restored.host.root), /已中断，待恢复/)
  restored.app.unmount()
  const fallback = mount(
    vue.ref({ ...serialized, tasks: [{ ...task('Q9', 'pending'), objective: ' ' }] }),
  )
  await flush()
  assert.match(mainText(fallback.host.root), /Q9待执行/)
  fallback.app.unmount()
  console.log(
    'PASS: mounted task progress follows dependencies, rework, dynamic verification and reload without chat events',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
