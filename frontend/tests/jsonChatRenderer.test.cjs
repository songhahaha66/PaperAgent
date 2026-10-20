const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
;(async () => {
  const state = vue.reactive({
    messages: [
      {
        id: 'm1',
        role: 'assistant',
        content: '',
        datetime: '',
        avatar: '',
        isStreaming: true,
        json_blocks: [
          { type: 'call_exec_py', content: 'print(1)' },
          { type: 'future_tool', content: { internal: 'raw-content' } },
          { type: 'word_tool_error', content: '无法写入文件' },
        ],
      },
    ],
    workStatus: 'in_progress',
  })
  const module = await loadModule(
    'src/components/JsonChatRenderer.vue',
    {
      vue,
      '@tdesign-vue-next/chat': {
        ChatItem: {
          setup:
            (_, { slots }) =>
            () =>
              vue.h('article', {}, slots.content?.()),
        },
      },
      '@/components/MarkdownRenderer.vue': {
        default: { props: ['content'], setup: (props) => () => vue.h('div', {}, props.content) },
      },
    },
    {},
    true,
  )
  const host = hostRenderer(),
    events = []
  const app = host.renderer.createApp({
    render: () =>
      vue.h(module.default, {
        ...state,
        onOpenProgress: () => events.push('progress'),
        onConfirmPlan: () => events.push('confirm'),
      }),
  })
  app.component('t-icon', { render: () => null })
  app.mount(host.root)
  await flush()
  const header = (id) =>
    host.find((n) => n.kind === 'button' && n.props?.['aria-controls'] === `event-m1-${id}`)
  assert.match(text(header(0)), /执行分析代码/)
  assert.doesNotMatch(text(header(0)), /Type:|call_exec_py|print/)
  assert.equal(header(0).props['aria-expanded'], false)
  assert.match(text(header(1)), /其他执行记录/)
  assert.doesNotMatch(text(header(1)), /future_tool|raw-content/)
  assert.equal(header(1).props['aria-expanded'], false)
  assert.equal(header(2).props['aria-expanded'], true, 'failures start expanded')
  assert.match(text(header(2)), /需要处理/)
  header(0).props.onClick()
  await flush()
  assert.equal(
    header(0).props['aria-expanded'],
    true,
    'a real button provides native keyboard activation',
  )
  state.messages[0].json_blocks[0].content += '\nprint(2)'
  state.messages[0].json_blocks.push({ type: 'exec_py', content: '2' })
  await flush()
  assert.equal(header(0).props['aria-expanded'], true, 'stream updates preserve a manual expansion')
  header(0).props.onClick()
  await flush()
  state.messages = [
    { ...state.messages[0], json_blocks: state.messages[0].json_blocks.map((b) => ({ ...b })) },
  ]
  await flush()
  assert.equal(
    header(0).props['aria-expanded'],
    false,
    'replacing streaming payloads preserves manual collapse',
  )
  const action = (label) => host.find((n) => n.kind === 'button' && text(n).trim() === label)
  action('查看运行状态').props.onClick()
  assert.deepEqual(events, ['progress'])
  state.messages[0].isStreaming = false
  state.messages[0].json_blocks.push({ type: 'confirm', content: '请确认计划' })
  await flush()
  assert.equal(
    !!action('继续研究'),
    false,
    'an event alone cannot authorize a stale confirmation',
  )
  state.workStatus = 'waiting'
  await flush()
  action('继续研究').props.onClick()
  assert.equal(events.at(-1), 'confirm')
  state.messages.push({ id: 'm2', role: 'user', content: '已确认', datetime: '', avatar: '' })
  await flush()
  assert.equal(!!action('继续研究'), false, 'historical decisions cannot be submitted again')
  state.messages[0].json_blocks = [
    { type: 'paper_analysis', content: '分析' },
    { type: 'future_tool', content: '不要丢掉未知记录' },
  ]
  await flush()
  assert.match(text(host.root), /其他执行记录/, 'mixed pipeline messages retain unknown records')
  app.unmount()
  console.log(
    'PASS: typed event titles, accessible controls, severity actions and stream expansion state stay consistent',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
