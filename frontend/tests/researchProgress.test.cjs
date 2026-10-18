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
    objective: `研究${id}`,
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
    delivery: { ready: false, blockers: [] },
    delivery_stages: {},
  })
  const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
  function mount(value) {
    const host = hostRenderer()
    const app = host.renderer.createApp({
      setup: () => () => vue.h(component.default, { snapshot: value.value, expanded: true }),
    })
    app.mount(host.root)
    return { host, app }
  }
  const { host, app } = mount(snapshot)
  await flush()
  assert.match(text(host.root), /2\/3 项已验收/)
  assert.match(text(host.root), /等待上游验收/)
  assert.match(text(host.root), /依赖：Q1/)
  snapshot.value = {
    ...snapshot.value,
    research_revision: 2,
    tasks: [task('Q1', 'rework', [], 2), task('Q2', 'waiting', ['Q1'], 2), task('Q3', 'accepted')],
  }
  await flush()
  assert.match(text(host.root), /1\/3 项已验收/)
  assert.match(text(host.root), /Q1 · v2.*待返工/)
  assert.match(text(host.root), /Q3 · v1.*已验收/)
  snapshot.value = {
    ...snapshot.value,
    tasks: [
      task('Q1', 'rework', ['Q4'], 2),
      ...snapshot.value.tasks.slice(1),
      { ...task('Q4', 'running'), kind: 'verify' },
    ],
  }
  await flush()
  assert.match(text(host.root), /1\/4 项已验收/)
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
  assert.match(text(restored.host.root), /1\/4 项已验收/)
  assert.match(text(restored.host.root), /Q3 · v1.*已验收/)
  assert.match(text(restored.host.root), /已中断，待恢复/)
  restored.app.unmount()
  console.log(
    'PASS: mounted task progress follows dependencies, rework, dynamic verification and reload without chat events',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
