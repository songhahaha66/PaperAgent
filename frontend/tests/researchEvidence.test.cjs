const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
;(async () => {
  const snapshot = vue.ref({
    schema_version: 1,
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
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
