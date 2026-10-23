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
  assert.equal(grouped.length, 2, 'adjacent visible operations group across hidden receipts')
  assert.equal(grouped.some(g => g.segments.some(s => s.id === 'phase')), false)
  const reads = timeline.timelineGroups({ id:'reads',content:'', segments:[
    before, exec('r1','completed',{ capability:'read_file',parent_call_id:'p',experiment_id:'E1' }),
    exec('r2','completed',{ capability:'read_file',parent_call_id:'p',experiment_id:'E1' }),
    { ...after, phase:'commentary',scope:'E1' },
    exec('r3','completed',{ capability:'read_file',parent_call_id:'p',experiment_id:'E1' }),
    exec('r4','completed',{ capability:'read_file',parent_call_id:'p',experiment_id:'E2' }),
    exec('maintenance','completed',{capability:'experience.candidates'}),
    exec('failed-maintenance','failed',{capability:'record_results'}),
  ] })
  assert.equal(reads[1].segments.length,2)
  assert.equal(reads[1].label,'执行记录')
  assert.equal(reads[2].segments[0].text,'After')
  assert.equal(reads[3].segments.length,3,'all adjacent operations group, including different scopes and failures')
  assert.equal(reads[3].segments[2].id,'failed-maintenance','failure remains inside the visible group')
  assert.equal(reads.length,4,'prose remains the grouping boundary')
  const actual = timeline.timelineGroups({ id:'actual', content:'', segments:[
    exec('experiment','running',{capability:'experiments.execute'}),
    before,
    exec('code','completed',{capability:'run_python'}),
    exec('save','completed',{capability:'record_results'}),
    after,
  ] })
  assert.equal(actual.map(g=>g.id).join(','),'before,code,after','outer experiment is not a second running status; real operations remain inline')
  assert.equal(timeline.progressText('子问题E1：实验E2已完成，现在登记这些结果。'),'当前实验：当前实验已完成，现在保存这些计算结果。')
  const recoverySegments = [exec('write-failed','failed',{capability:'writing.write'}),
    after,exec('write-ok','completed',{capability:'writing.write',finished_at:4})]
  const recovered = timeline.timelineGroups({id:'recovery',content:'',segments:recoverySegments})
  assert.equal(recovered[0].segments[0].execution.recovered,true)
  assert.equal(recoverySegments[0].execution.recovered,undefined,'projection never rewrites historical failure')
  assert.equal(recovered[0].segments[0].execution.status,'failed')
  const publication = timeline.timelineGroups({id:'publication',content:'',segments:[before,
    exec('write','completed',{capability:'writing.write'}),
    {id:'receipt',type:'event',block:{type:'paper_stage',content:'Saved'}},
    exec('save-source','completed',{capability:'writing.save_source'}),
    exec('housekeeping','completed',{capability:'experience.candidates'}),
    exec('compile','completed',{capability:'writing.compile',parent_call_id:'write'}),after,
    exec('later-write','running',{capability:'writing.write'}),
  ]})
  assert.equal(publication.length,4,'report steps share one row but never cross prose')
  assert.equal(publication[1].label,'执行记录')
  assert.equal(publication[1].segments.length,3,'stage receipts do not create additional steps')
  assert.equal(publication[3].segments.length,1)
  assert.equal(JSON.stringify(timeline.timelineGroups(JSON.parse(JSON.stringify(message)))),
    JSON.stringify(timeline.timelineGroups(message)), 'reload preserves the same groups')
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
  const oldModel = { ...exec('model:1'), ref:{ id:'model:1',kind:'model',research_id:'old' }, execution:{ id:'model:1',kind:'model',label:'模型请求',status:'completed' } }
  assert.deepEqual(timeline.messageSegments({id:'old-model',content:'',segments:[before,oldModel,exec('actual')]}).map((s)=>s.id), ['before','actual'])
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
            vue.h('aside', {}, p.segments.map((s) => s.type === 'execution' ? s.execution.label : 'receipt').join('|')),
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
