const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = (node) =>
  node.kind === 'comment' ? '' : [node.text || '', ...(node.children || []).map(text)].join('')
const test = require('node:test')
const row = (status, extra = {}) => ({
    id: 'segment',
    type: 'execution',
    ref: { id: 'call', kind: 'activity', research_id: 'old' },
    execution: {
      id: 'call',
      label: '读取已有文件 · data.csv',
      status,
      started_at: 1,
      parent_call_id: 'parent',
      file: 'inputs/data.csv',
      detail: '读取第 0 个字符起的 1200 个字符。',
      ...extra,
    },
  })

async function mount(t) {
  let pending
  const module = await loadModule(
    'src/components/ConversationExecution.vue',
    {
      vue,
      '@/api/chat': {
        chatAPI: {
          resolveExecutions: () =>
            new Promise((resolve) => {
              pending = resolve
            }),
        },
      },
    },
    { setInterval: () => 1, clearInterval() {} },
    true,
  )
  const state = vue.reactive({
    replyId: 'reply',
    groupId: 'segment',
    workId: 'work',
    segments: [row('running')],
  })
  const opened = [],
    results = []
  const host = hostRenderer()
  const app = host.renderer.createApp({
    render: () =>
      vue.h(module.default, {
        ...state,
        onFile: (p) => opened.push(p),
        onResult: (id) => results.push(id),
      }),
  })
  app.mount(host.root)
  await flush()
  const heading = () =>
    host.find((n) => n.kind === 'button' && n.props?.['aria-controls'] === 'calls-reply-segment')
  const button = (label) => host.find((n) => n.kind === 'button' && text(n).trim() === label)
  t.after(() => app.unmount())
  return { state, host, heading, button, opened, results, response: () => pending }
}

test('streaming output preserves manual expansion', async (t) => {
  const { state, host, heading, button, opened, results, response } = await mount(t)
  assert.equal(heading().props['aria-expanded'], false, 'running group starts compact')
  heading().props.onClick()
  await flush()
  assert.equal(heading().props['aria-expanded'], true)
  assert.match(text(host.root), /读取第 0 个字符/, 'expanded detail describes the actual operation')
  state.segments = [row('running', { output_tail: 'first print\nsecond print' })]
  await flush()
  assert.match(
    text(host.root),
    /first print\nsecond print/,
    'expanded execution updates its received stdout',
  )
  assert.equal(
    host.find((n) => n.kind === 'button' && n.props?.class === 'output-toggle'),
    undefined,
    'one expansion directly reveals the output without a second fold',
  )
  state.segments = [row('running', { output_tail: 'first print\nsecond print\nthird print' })]
  await flush()
  assert.equal(
    heading().props['aria-expanded'],
    true,
    'manual expansion survives streaming updates',
  )
  assert.match(text(host.root), /third print/)
  heading().props.onClick()
  await flush()
  assert.doesNotMatch(text(host.root), /third print/, 'stdout can be folded again')
})

test('preview details stay folded until the operation is expanded', async (t) => {
  const { state, host, heading } = await mount(t)
  state.segments = [row('completed', {
    label: '编译并预览论文 PDF', capability: 'writing.preview',
    detail: '第 6–9 页；共 17 页；1 条编译警告', warnings: ['Overfull box'],
    output_tail: 'raw output',
  })]
  await flush()
  assert.doesNotMatch(text(host.root), /第 6–9 页|Overfull box|raw output|writing.preview/)
  heading().props.onClick()
  await flush()
  assert.match(text(host.root), /Overfull box/)
  assert.match(text(host.root), /第 6–9 页；共 17 页/)
  assert.doesNotMatch(text(host.root), /工具：|writing.preview/)
  assert.match(text(host.root), /raw output/)
  assert.equal((text(host.root).match(/第 6–9 页/g) || []).length, 1)
})

test('live finish rejects stale fetches and keeps file/result links', async (t) => {
  const { state, host, heading, button, opened, results, response } = await mount(t)
  heading().props.onClick()
  await flush()
  const staleResponse = response()
  state.segments = [
    row('completed', { finished_at: 4, result_ids: ['R1'], output_file: 'runs/run-1/stdout.log' }),
  ]
  await flush()
  staleResponse({
    executions: [
      { id: 'call', segment_id: 'segment', label: 'stale running row', status: 'running' },
    ],
  })
  await flush()
  assert.doesNotMatch(
    text(host.root),
    /stale running row/,
    'late resolver response cannot overwrite a newer live finish',
  )
  assert.equal(heading().props['aria-expanded'], true, 'manual expansion survives live finish')
  assert.ok(button('data.csv'), 'the selected workspace file remains available after finish')
  assert.ok(button('完整日志'), 'a finished operation exposes its full log link')
  assert.ok(
    button('查看结果'),
    'a finished operation keeps its result link without showing internal IDs',
  )
  button('data.csv').props.onClick()
  button('完整日志').props.onClick()
  button('查看结果').props.onClick()
  assert.deepEqual(opened, ['inputs/data.csv', 'runs/run-1/stdout.log'])
  assert.deepEqual(results, ['R1'])
})

test('manually collapsed errors keep their diagnostic details', async (t) => {
  const { state, host, heading, button, opened, results, response } = await mount(t)
  heading().props.onClick()
  await flush()
  heading().props.onClick()
  await flush()
  state.segments = [row('failed', { finished_at: 4, message: 'safe failure notice' })]
  await flush()
  assert.equal(
    heading().props['aria-expanded'],
    false,
    'manual collapse persists through error updates',
  )
  assert.match(text(host.root), /失败/, 'failure stays visible even when manually folded')
  heading().props.onClick()
  await flush()
  assert.match(
    text(host.root),
    /safe failure notice/,
    'the concrete sanitized failure remains available in details',
  )
  heading().props.onClick()
})

test('later success never relabels an earlier failure', async (t) => {
  const { state, host, heading, button, opened, results, response } = await mount(t)
  state.segments = [
    row('failed', { finished_at: 3, message: 'original error' }),
    { ...row('completed', { id: 'retry', finished_at: 8 }), id: 'retry' },
  ]
  await flush()
  assert.match(text(heading()), /完成/, 'compact group follows its latest attempt')
  assert.equal(heading().props['aria-expanded'], false, 'finished history stays compact')
  heading().props.onClick()
  await flush()
  assert.match(text(host.root), /original error/)
  assert.match(text(host.root), /失败/, 'the earlier attempt retains its own status')
  assert.doesNotMatch(text(host.root), /后续已完成/)
  heading().props.onClick()
})

test('single execution has one heading, duration and output entry', async (t) => {
  const { state, host, heading, button, opened } = await mount(t)
  state.segments = [
    row('completed', {
      label: '检查附件与模板结构',
      finished_at: 34,
      output_tail: '10 files checked',
      output_file: 'runs/check/stdout.log',
      script_file: 'runs/check/input.py',
      files: ['outputs/result.xlsx'],
    }),
  ]
  await flush()
  assert.match(text(heading()), /检查附件与模板结构完成33 秒/)
  heading().props.onClick()
  await flush()
  assert.equal((text(host.root).match(/检查附件与模板结构/g) || []).length, 1)
  assert.equal((text(host.root).match(/33 秒/g) || []).length, 1)
  assert.match(text(host.root), /10 files checked/)
  assert.doesNotMatch(text(host.root), /本次代码|展开输出|错误输出|1 次操作/)
  assert.ok(button('查看执行代码') && button('完整日志'))
  button('result.xlsx').props.onClick()
  assert.deepEqual(opened, ['outputs/result.xlsx'])
  assert.equal(button('诊断日志'), undefined, 'empty diagnostics have no entry')
})

test('each grouped operation folds its own logs and retains code links', async (t) => {
  const { state, host, heading, button, opened } = await mount(t)
  state.segments = [
    row('completed', { label: '生成24小时汇总', finished_at: 7, output_tail: 'hour 00:00, buy 7615',
      script_file: 'code/summary.py', output_file: 'runs/summary/stdout.log' }),
    { ...row('completed', { id: 'check', label: '检查约束', finished_at: 9,
      output_tail: 'constraints checked' }), id: 'check' },
  ]
  await flush()
  heading().props.onClick()
  await flush()
  const step = (id) => host.find((n) => n.kind === 'button' && n.props?.['aria-controls'] === `call-reply-${id}`)
  assert.equal(step('segment').props['aria-expanded'], false)
  assert.doesNotMatch(text(host.root), /hour 00:00|constraints checked/)
  step('segment').props.onClick()
  await flush()
  assert.match(text(host.root), /hour 00:00/)
  assert.doesNotMatch(text(host.root), /constraints checked/)
  button('查看执行代码').props.onClick()
  button('完整日志').props.onClick()
  assert.deepEqual(opened, ['code/summary.py', 'runs/summary/stdout.log'])
  step('segment').props.onClick()
  await flush()
  assert.doesNotMatch(text(host.root), /hour 00:00/)
  assert.match(text(host.root), /生成24小时汇总完成6 秒/)
})

test('individual expansion survives streaming, new steps and folding the group', async (t) => {
  const { state, host, heading } = await mount(t)
  const next = { ...row('running', { id: 'next', label: '继续计算', output_tail: 'next output' }), id: 'next' }
  state.segments = [row('completed', { finished_at: 5, output_tail: 'first output' }), next]
  await flush()
  heading().props.onClick()
  await flush()
  const step = (id) => host.find((n) => n.kind === 'button' && n.props?.['aria-controls'] === `call-reply-${id}`)
  step('next').props.onClick()
  await flush()
  state.segments = [state.segments[0], { ...next, execution: { ...next.execution, output_tail: 'new output' } },
    { ...row('running', { id: 'last', label: '保存表格', output_tail: 'last output' }), id: 'last' }]
  await flush()
  assert.equal(step('next').props['aria-expanded'], true)
  assert.equal(step('last').props['aria-expanded'], false)
  assert.match(text(host.root), /new output/)
  assert.doesNotMatch(text(host.root), /first output|last output/)
  heading().props.onClick()
  await flush()
  heading().props.onClick()
  await flush()
  assert.equal(step('next').props['aria-expanded'], true)
  assert.match(text(host.root), /new output/)
})

test('an opened single operation stays open when a second operation arrives', async (t) => {
  const { state, host, heading } = await mount(t)
  state.segments = [row('running', { output_tail: 'keep this output visible' })]
  await flush()
  heading().props.onClick()
  await flush()
  state.segments = [state.segments[0],
    { ...row('running', { id: 'new', label: '新操作', output_tail: 'new output stays folded' }), id: 'new' }]
  await flush()
  const first = host.find((n) => n.kind === 'button' && n.props?.['aria-controls'] === 'call-reply-segment')
  assert.equal(first.props['aria-expanded'], true)
  assert.match(text(host.root), /keep this output visible/)
  assert.doesNotMatch(text(host.root), /new output stays folded/)
})

test('delivery phase stays active until files are delivered', async (t) => {
  const { state, host, heading } = await mount(t)
  state.segments = [row('running', { phase: 'delivery', label: '保存结果文件' })]
  await flush()
  assert.match(text(heading()), /保存结果文件同步文件/)
  state.segments = [
    row('interrupted', {
      label: '保存结果文件',
      finished_at: 5,
      message: '计算已结束，文件交付未完成',
    }),
  ]
  await flush()
  assert.match(text(host.root), /文件交付未完成/)
  assert.doesNotMatch(text(heading()), /完成/)
})

test('a paused reply cannot keep an old execution spinning', async (t) => {
  const { state, host, heading } = await mount(t)
  state.replyStatus = 'paused'
  await flush()
  assert.match(text(heading()), /已中断/)
  assert.doesNotMatch(host.find(node => node.kind === 'section').props.class, /running/)
  assert.equal(state.segments[0].execution.status, 'running', 'the display does not rewrite saved history')
})

test('retry progress follows the latest step and retains old errors', async (t) => {
  const { state, host, heading, button, opened, results, response } = await mount(t)
  const step = (id, status, label, extra = {}) => ({
    ...row(status, { id, label, ...extra }),
    id,
  })
  const timeout = step('first', 'failed', '计算基本统计量', {
    finished_at: 192,
    message: '执行超时，内核已重启。',
  })
  const retry = step('retry', 'completed', '重新计算基本统计量', { finished_at: 202 })
  state.label = '执行记录'
  state.segments = [timeout, retry]
  await flush()
  const group = () => host.find((n) => n.kind === 'section')
  assert.match(
    text(heading()),
    /重新计算基本统计量完成2 次操作/,
    'group summarizes the latest successful attempt',
  )
  assert.doesNotMatch(group().props.class, /failed/)
  assert.doesNotMatch(
    text(host.root),
    /失败|执行超时/,
    'an earlier timeout does not pin the compact group to failure',
  )
  state.segments = [...state.segments, step('save', 'running', '保存计算结果')]
  await flush()
  assert.match(
    text(heading()),
    /保存计算结果进行中3 次操作/,
    'the heading follows the next live step',
  )
  assert.match(group().props.class, /running/)
  state.segments = [timeout, retry, step('save', 'completed', '保存计算结果', { finished_at: 204 })]
  await flush()
  assert.match(text(heading()), /保存计算结果完成3 次操作/)
  assert.doesNotMatch(group().props.class, /failed|running/)
  assert.equal(heading().props['aria-expanded'], false, 'progress updates preserve manual collapse')
  heading().props.onClick()
  await flush()
  assert.match(
    text(host.root),
    /执行超时，内核已重启。/,
    'earlier failures remain in expanded history',
  )
  assert.match(
    text(host.root),
    /计算基本统计量失败/,
    'history preserves each attempt’s own status',
  )
  heading().props.onClick()
  state.segments = [
    ...state.segments,
    step('export', 'failed', '导出结果', {
      finished_at: 206,
      message: '导出失败，请重试。',
    }),
  ]
  await flush()
  assert.match(
    text(heading()),
    /导出结果失败4 次操作/,
    'a failure of the latest step still appears',
  )
  assert.match(group().props.class, /failed/)
  assert.match(text(host.root), /导出失败，请重试。/)
  assert.doesNotMatch(
    text(host.root),
    /执行超时/,
    'only the latest failure is promoted out of history',
  )
})
