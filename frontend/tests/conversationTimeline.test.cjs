const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
;(async () => {
  const timeline = await loadModule('src/utils/conversationTimeline.ts', {})
  const exec = (id, status = 'running', extra = {}) => ({
    id,
    type: 'execution',
    ref: { id, kind: 'activity', research_id: 'old-research' },
    execution: { id, label: `Read ${id}`, status, started_at: 1, ...extra },
  })
  const before = { id: 'before', type: 'text', text: 'Before {"type":"confirm"}' }
  const after = { id: 'after', type: 'text', text: 'After' }
  let message = { id: 'reply', content: '', segments: [] }
  const update = (revision, segments, status = 'running') => ({
    reply_id: 'reply',
    chat_task_id: 'chat-task',
    revision,
    status,
    segments,
  })
  message = timeline.applyReplyUpdate(
    message,
    update(1, [
      before,
      exec('outer'),
      exec('nested', 'running', { parent_call_id: 'outer' }),
      after,
    ]),
  )
  assert.equal(message.segments.map((r) => r.id).join(','), 'before,outer,nested,after')
  message = timeline.applyReplyUpdate(
    message,
    update(2, [exec('outer', 'completed', { finished_at: 4 })]),
  )
  assert.equal(message.segments.length, 4, 'finish updates the existing row in place')
  assert.equal(
    timeline.applyReplyUpdate(message, update(1, [before])),
    message,
    'replayed older deltas are ignored',
  )
  assert.equal(
    timeline.applyReplyUpdate(message, update(2, [exec('outer')])),
    message,
    'replayed duplicate deltas are ignored',
  )
  assert.equal(
    timeline
      .timelineGroups(message)
      .map((g) => g.segments.length)
      .join(','),
    '1,2,1',
  )
  message = timeline.applyReplyUpdate(message, update(3, [exec('later')]))
  assert.equal(
    timeline.timelineGroups(message).length,
    4,
    'operations never group across intervening text',
  )
  const refreshed = JSON.parse(JSON.stringify(message))
  assert.equal(
    timeline.applyReplyUpdate(refreshed, update(3, [exec('later')])),
    refreshed,
    'mid-run refresh binds the same durable revision',
  )
  const grouped = timeline.timelineGroups({ id: 'progress', content: '', segments: [
    { id:'start', type:'event', block:{type:'main_agent_start',content:'started'} },
    exec('call'),
    { id:'phase', type:'event', block:{type:'paper_phase',content:{stage:'write',note:'保存源码'}} },
    exec('child', 'running', {parent_call_id:'call'}),
    after,
  ] })
  assert.equal(grouped.length, 2, 'stage receipts share the existing execution group; prose stays outside')
  assert.equal(grouped[0].segments.length, 3)
  const prose = 'Here is JSON:\n```json\n{"type":"confirm","content":"example"}\n```\n{"a":1}\nend'
  const legacy = timeline.messageSegments({
    id: 'old',
    content: prose,
    json_blocks: [
      { type: 'future_tool', content: 'unknown history' },
      { type: 'research_activity', content: { recorded_at: 1 } },
    ],
  })
  assert.equal(legacy[0].text, prose, 'JSON-looking prose survives byte for byte')
  assert.equal(legacy.length, 2, 'invalidation does not become execution')
  assert.equal(legacy[1].block.type, 'future_tool')
  const paper = timeline.messageSegments({
    id: 'paper-comment',
    content: 'comment answer',
    paper_execution: { state: 'pending' },
  })
  assert.equal(
    paper[0].text,
    'comment answer',
    'paper-comment projection needs no duplicate persisted timeline',
  )

  const component = await loadModule(
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
        default: {
          props: ['content'],
          setup: (p) => () => vue.h('div', { class: 'markdown' }, p.content),
        },
      },
      './ConversationExecution.vue': {
        default: {
          props: ['segments'],
          setup: (p) => () =>
            vue.h('aside', {}, p.segments.map((s) => s.execution.label).join('|')),
        },
      },
    },
    {},
    true,
  )
  const state = vue.reactive({
    messages: [{ ...message, role: 'assistant', datetime: '', avatar: '', isStreaming: false }],
    workStatus: 'waiting',
  })
  state.messages[0].segments.push(
    {
      id: 'sources',
      type: 'event',
      block: {
        type: 'research_context',
        content: { files: ['inputs/data.csv', 'research:feedback'], result_ids: ['R1'] },
      },
    },
    {
      id: 'final',
      type: 'event',
      block: { type: 'main_agent_complete', content: 'DELIVERED RESULT' },
    },
    { id: 'confirm', type: 'event', block: { type: 'paper_confirm', content: 'Need input' } },
  )
  let confirmed = 0,
    progressOpened = 0
  const openedFiles = [],
    openedResults = []
  const host = hostRenderer()
  const app = host.renderer.createApp({
    render: () =>
      vue.h(component.default, {
        ...state,
        onConfirmPlan: () => confirmed++,
        onOpenFile: (path) => openedFiles.push(path),
        onOpenResult: (id) => openedResults.push(id),
        onOpenProgress: () => progressOpened++,
      }),
  })
  app.component('t-icon', { render: () => null })
  app.mount(host.root)
  await flush()
  const rendered = text(host.root)
  assert.ok(rendered.indexOf('Before') < rendered.indexOf('Read outer'))
  assert.ok(rendered.indexOf('Read outer') < rendered.indexOf('After'))
  const final = host.find((n) => n.props?.class === 'markdown' && text(n) === 'DELIVERED RESULT')
  assert.ok(final && final.parent.kind !== 'details', 'final result remains outside folded details')
  const action = (label) => host.find((n) => n.kind === 'button' && text(n).trim() === label)
  assert.ok(action('data.csv'), 'file sources remain visible')
  assert.ok(action('查看实测结果'), 'result references remain visible')
  assert.ok(action('校验问题'), 'semantic research sources remain visible')
  action('data.csv').props.onClick()
  action('查看实测结果').props.onClick()
  action('校验问题').props.onClick()
  assert.deepEqual(openedFiles, ['inputs/data.csv'])
  assert.deepEqual(openedResults, ['R1'])
  assert.equal(
    progressOpened,
    1,
    'semantic research refs open research details, never a fake file path',
  )
  const confirm = () => host.find((n) => n.kind === 'button' && text(n).trim() === '继续研究')
  assert.ok(confirm(), 'the latest paper_confirm in waiting state exposes Continue research')
  confirm().props.onClick()
  assert.equal(confirmed, 1)
  state.messages.push({
    id: 'second-turn',
    role: 'user',
    content: 'Next',
    datetime: '',
    avatar: '',
  })
  await flush()
  assert.equal(!!confirm(), false, 'historical confirmation cannot authorize another run')
  assert.match(text(host.root), /DELIVERED RESULT/, 'older turn stays visible')
  app.unmount()
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
