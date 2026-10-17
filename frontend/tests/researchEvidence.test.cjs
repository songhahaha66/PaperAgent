const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
;(async () => {
  const snapshot = vue.ref({
    schema_version: 2,
    work_id: 'one',
    has_research: true,
    work_status: 'completed',
    research_id: 'research-one',
    graph_status: 'done',
    validation_count: 1,
    truncated: false,
    results: [
      {
        id: 'R1',
        subproblem_id: 'Q1',
        name: 'AUC',
        value: '0.82',
        unit: '',
        kind: 'number',
        run_id: 'run_one',
        figure_path: null,
      },
      {
        id: 'R2',
        subproblem_id: 'Q2',
        name: 'Count',
        value: '42',
        unit: '',
        kind: 'number',
        run_id: 'run_two',
        figure_path: null,
      },
    ],
    runs: [
      {
        id: 'run_one',
        status: 'success',
        input_path: 'runs/run_one/input.py',
        stdout_path: 'runs/run_one/stdout.log',
      },
      {
        id: 'run_two',
        status: 'success',
        input_path: 'runs/run_two/input.py',
        stdout_path: 'runs/run_two/stdout.log',
      },
    ],
    claims: [],
    artifacts: ['paper.md', 'paper.pdf'],
  })
  const module = await loadModule(
    'src/components/ResearchEvidence.vue',
    { vue, '@/lang/studio': { useStudioCopy: () => ({ isZh: vue.ref(true) }) } },
    {},
    true,
  )
  const host = hostRenderer(),
    opened = [],
    asked = []
  const app = host.renderer.createApp({
    setup: () => () =>
      vue.h(module.default, {
        snapshot: snapshot.value,
        onOpenFile: (path) => opened.push(path),
        onAsk: (id) => asked.push(id),
      }),
  })
  app.component('t-icon', { render: () => null })
  app.mount(host.root)
  await flush()
  const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
  const button = (label) => host.find((node) => node.kind === 'button' && text(node) === label)
  button('Q2').props.onClick()
  await flush()
  assert.equal(host.find((node) => node.props?.class === 'result-name')?.text, 'Count')
  const row = host.find((node) => node.props?.class === 'result-row')
  row.props.onClick()
  await flush()
  button('执行代码').props.onClick()
  button('执行输出').props.onClick()
  button('追问这部分').props.onClick()
  assert.deepEqual(opened, ['runs/run_two/input.py', 'runs/run_two/stdout.log'])
  assert.deepEqual(asked, ['Q2'])
  button('查看校验问题').props.onClick()
  assert.equal(opened.at(-1), '.system/validation.json')
  console.log(
    'PASS: mounted evidence card filters actual results and opens their exact execution sources',
  )
  snapshot.value = {
    ...snapshot.value,
    tasks: [
      {
        task_id: 'Q4',
        kind: 'verify',
        revision: 1,
        execution_status: 'running',
        acceptance: 'pending',
        validity: 'current',
      },
    ],
    requirements: [
      {
        id: 'RQ1',
        task_ids: ['Q1'],
        status: 'pending',
        statement: '满足容量约束',
        source_quote: '容量不超过10',
      },
    ],
    assumptions: [
      {
        id: 'A1',
        revision: 1,
        status: 'refuted',
        statement: '均值具有代表性',
        source: 'solve',
        task_ids: ['Q1', 'Q2'],
      },
    ],
    issues: [
      {
        id: 'issue-one',
        task_id: 'Q1',
        task_revision: 1,
        assumption_id: 'A1',
        status: 'repairing',
        source: 'local_review',
        message: '独立核验发现反例',
        evidence_refs: ['R7'],
      },
    ],
    issue_evidence: [{ id: 'R7', name: 'residual', value: '4', unit: '', run_id: 'run_one' }],
  }
  await flush()
  assert.match(text(host.root), /容量不超过10/)
  assert.match(text(host.root), /已被反证/)
  assert.match(text(host.root), /计划修复中/)
  assert.match(text(host.root), /求解中提出/)
  assert.match(text(host.root), /求解审查/)
  assert.match(text(host.root), /R7 · residual：4/)
  assert.equal(
    button('修改这部分'),
    undefined,
    'verification tasks are controlled by the issue protocol',
  )
  button('查看核验输出').props.onClick()
  assert.equal(
    opened.at(-1),
    'runs/run_one/stdout.log',
    'historical counterexamples retain their own output source',
  )
  console.log('PASS: contracts, refutations and historical counterexample sources render together')
  snapshot.value = {
    ...snapshot.value,
    delivery: {
      ready: false,
      blockers: [
        { task_id: 'Q1', message: '约束复算左值12，不满足容量8', action: 'recompute' },
        { task_id: 'Q2', message: '正文引用的图不存在', action: 'rewrite' },
      ],
    },
  }
  await flush()
  assert.match(text(host.root), /交付条件尚未满足/)
  assert.match(text(host.root), /约束复算左值12/)
  assert.match(text(host.root), /重算受影响任务并重新验收/)
  assert.match(text(host.root), /已验收计算可保留/)
  snapshot.value = { ...snapshot.value, delivery: { ready: true, blockers: [] }, artifacts: [] }
  await flush()
  assert.match(text(host.root), /研究验收通过，等待生成交付物/)
  assert.doesNotMatch(text(host.root), /约束复算左值12/)
  snapshot.value = { ...snapshot.value, artifacts: ['paper.md'] }
  await flush()
  assert.match(text(host.root), /已通过交付验收/)
  snapshot.value = {
    ...snapshot.value,
    tasks: [
      {
        task_id: 'Q1',
        revision: 2,
        kind: 'compute',
        execution_status: 'succeeded',
        acceptance: 'accepted',
        validity: 'current',
        validation: {
          outcome: 'accepted',
          checks: [
            {
              check_id: 'linear',
              outcome: 'passed',
              message: '约束和目标值复算通过；未验证最优性',
            },
          ],
        },
      },
    ],
  }
  await flush()
  assert.match(text(host.root), /查看本次验收/)
  assert.match(text(host.root), /未验证最优性/)
  console.log(
    'PASS: delivery status and next steps follow authoritative backend checks, with no stale blockers',
  )
  snapshot.value = { ...snapshot.value, results: [], runs: [], validation_count: 0 }
  await flush()
  assert.ok(host.find((node) => node.props?.class === 'evidence-empty'))
  assert.equal(button('执行代码'), undefined, 'removed evidence cannot retain stale source links')
  app.unmount()
  console.log('PASS: evidence refresh removes stale execution links')

  const messages = []
  const api = await loadModule(
    'src/api/chat.ts',
    { '@/utils/apiClient': { apiClient: {} } },
    { WebSocket: { OPEN: 1 } },
  )
  const socket = new api.WebSocketChatHandler('one', 'test')
  socket.ws = { readyState: 1, send: (raw) => messages.push(JSON.parse(raw)) }
  socket.sendMessage('解释 Q2', undefined, 'ask', 'Q2')
  socket.sendMessage('继续', undefined, 'resume')
  assert.deepEqual(messages, [
    { problem: '解释 Q2', action: 'ask', subproblem_id: 'Q2' },
    { problem: '继续', action: 'resume' },
  ])
  console.log('PASS: websocket transport preserves typed action and question scope')
  socket.sendMessage('修正 Q2', undefined, 'revise_research', 'Q2', {
    command_id: 'revision-one',
    research_id: 'research-one',
    base_revision: 2,
    scope: ['Q2'],
  })
  assert.equal(messages.at(-1).revision.base_revision, 2)
  assert.equal(messages.at(-1).revision.scope[0], 'Q2')
  const phases = await loadModule('src/utils/paperPipeline.ts', {})
  const updates = [
    { type: 'paper_phase', content: { stage: 'solve', status: 'blocked' } },
    { type: 'paper_phase', content: { stage: 'write', status: 'in_progress' } },
  ]
  assert.equal(phases.derivePhases(updates).find((p) => p.key === 'solve').status, 'blocked')
  assert.equal(phases.derivePhases(updates).find((p) => p.key === 'design').status, 'pending')
  const failedAnalysis = [
    { type: 'paper_phase', content: { stage: 'analyze', status: 'in_progress' } },
  ]
  assert.equal(
    phases.derivePhases(failedAnalysis, { failed: true }).find((p) => p.key === 'analyze').status,
    'blocked',
  )
  assert.equal(
    phases.derivePhases(failedAnalysis).find((p) => p.key === 'analyze').status,
    'in_progress',
  )
  assert.equal(
    phases.deriveSubproblems([
      { type: 'paper_solved', content: 'Q2 已通过验收，登记 5 项结果' },
      { type: 'paper_stage', content: 'Q2：重新求解' },
    ])[0].status,
    'in_progress',
  )
  const stagedPremise = [
    { type: 'paper_stage', content: 'Q1 已提交，关键假设已登记，按冻结契约重新执行' },
  ]
  assert.equal(phases.deriveSubproblems(stagedPremise)[0].status, 'in_progress')
  assert.equal(
    phases.deriveSubproblems([
      ...stagedPremise,
      { type: 'paper_solved', content: 'Q1 已通过验收，登记 7 项结果' },
    ])[0].status,
    'completed',
  )
  const oldCompletion = { type: 'paper_solved', content: 'Q1 求解完成' }
  const currentTask = {
    task_id: 'Q1',
    objective: '实际研究',
    execution_status: 'pending',
    acceptance: 'pending',
    validity: 'stale',
  }
  const researchState = (task, issues = []) => ({
    type: 'research_state',
    content: { tasks: [task], issues },
  })
  assert.equal(
    phases.deriveSubproblems([oldCompletion, researchState(currentTask)])[0].status,
    'pending',
  )
  const acceptedTask = {
    ...currentTask,
    execution_status: 'succeeded',
    acceptance: 'accepted',
    validity: 'current',
  }
  const openReview = [{ task_id: 'Q1', status: 'suspected', blocking: true }]
  assert.equal(
    phases.deriveSubproblems([oldCompletion, researchState(acceptedTask, openReview)])[0].status,
    'failed',
  )
  assert.equal(
    phases.deriveSubproblems([
      researchState(acceptedTask, openReview),
      researchState(acceptedTask),
    ])[0].status,
    'completed',
  )
  console.log('PASS: revisions carry scope/version and retries preserve explicit phase status')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
