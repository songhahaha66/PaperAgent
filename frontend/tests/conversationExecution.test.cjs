const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
;(async () => {
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
  assert.equal(heading().props['aria-expanded'], false, 'running group starts compact')
  heading().props.onClick()
  await flush()
  assert.equal(heading().props['aria-expanded'], true)
  assert.match(text(host.root), /读取第 0 个字符/, 'expanded detail describes the actual operation')
  const staleResponse = pending
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
  const button = (label) => host.find((n) => n.kind === 'button' && text(n).trim() === label)
  assert.ok(button('data.csv'), 'the selected workspace file remains available after finish')
  assert.ok(button('执行输出'), 'a finished operation exposes its output link')
  assert.ok(button('查看结果'), 'a finished operation keeps its result link without showing internal IDs')
  button('data.csv').props.onClick()
  button('执行输出').props.onClick()
  button('查看结果').props.onClick()
  assert.deepEqual(opened, ['inputs/data.csv', 'runs/run-1/stdout.log'])
  assert.deepEqual(results, ['R1'])
  heading().props.onClick()
  await flush()
  state.segments = [row('failed', { finished_at: 4, message: 'safe failure notice' })]
  await flush()
  assert.equal(
    heading().props['aria-expanded'],
    false,
    'manual collapse persists through error updates',
  )
  assert.match(text(host.root), /未完成/, 'failure stays visible even when manually folded')
  heading().props.onClick()
  await flush()
  assert.match(
    text(host.root),
    /safe failure notice/,
    'the concrete sanitized failure remains available in details',
  )
  heading().props.onClick()
  state.segments = [
    row('completed', { id: 'parent', kind: 'activity', parent_call_id: null, finished_at: 8 }),
    { ...row('failed', { id: 'child', parent_call_id: 'parent', finished_at: 3 }), id: 'child' },
  ]
  await flush()
  assert.doesNotMatch(text(heading()), /未完成/, 'a recovered child failure does not mark a completed parent failed')
  assert.equal(heading().props['aria-expanded'], false, 'finished history stays compact')
  app.unmount()
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
