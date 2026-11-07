const assert = require('node:assert/strict')
const test = require('node:test')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = n => n.kind === 'comment' ? '' : [n.text || '', ...(n.children || []).map(text)].join('')
const tool = (id, extra = {}) => ({ id, thread_id: 'lead', turn_id: 'turn', sequence: 1, type: 'toolCall', tool_name: 'read_file', call_id: id, label: id, status: 'completed', started_at: 1, finished_at: 4, ...extra })

test('adjacency preserves message, request, Turn and child boundaries without mutating Items', async () => {
  const { groupAdjacentOperations, conversationEntries, operationGroupTitle } = await loadModule('src/utils/conversationTimeline.ts', {})
  const plan = { ...tool('plan'), type: 'plan', steps: [], sequence: 2 }
  const prose = { ...tool('prose'), type: 'agentMessage', text: '下面检查', sequence: 3 }
  const items = [tool('read'), plan, prose, tool('edit', { type: 'fileChange', sequence: 4 }),
    tool('child', { thread_id: 'child-thread', sequence: 1 }), tool('preview', { tool_name: 'writing.preview', sequence: 5 }),
    { ...tool('confirm'), type: 'inputRequest', name: 'paper_confirm', content: '继续？', sequence: 6 },
    tool('export', { tool_name: 'writing.export', sequence: 7 })]
  const original = JSON.stringify(items)
  const entries = conversationEntries({ id: 'reply', content: '', items, metadata: { thread_id: 'lead' } })
  assert.equal(entries.map(e => e.type).join(','), 'operationGroup,agentMessage,operationGroup,inputRequest,operationGroup')
  assert.equal(entries[0].operations.map(i => i.id).join(','), 'read,plan')
  assert.equal(entries[2].operations.map(i => i.id).join(','), 'edit,preview')
  assert.equal(operationGroupTitle(entries[2].operations), '编写与检查论文')
  assert.equal(entries[4].operations.length, 1)
  assert.equal(JSON.stringify(items), original)
  assert.equal(groupAdjacentOperations([tool('a'), tool('b', { turn_id: 'other' }), tool('c', { thread_id: 'child' })]).length, 3)
  assert.equal(conversationEntries({ id: 'reply', content: '', items: [items[0]], metadata: { thread_id: 'lead' } })[0].id, entries[0].id, 'appending operations retains group identity')
})

async function mount(t, operations) {
  const child = { props: ['item'], emits: ['expansion-change'], setup(props, { emit }) {
    const expanded = vue.ref(false)
    return () => vue.h('button', { 'data-tool': props.item.id, 'aria-expanded': expanded.value,
      onClick() { expanded.value = !expanded.value; emit('expansion-change', expanded.value) } }, props.item.label)
  } }
  const component = await loadModule('src/components/ConversationOperations.vue', { vue, './ConversationTool.vue': { default: child } }, { setInterval: () => 1, clearInterval() {} }, true)
  const state = vue.reactive({ operations, items: [], replyId: 'reply' })
  const host = hostRenderer(), files = []
  const app = host.renderer.createApp({ render: () => vue.h(component.default, { ...state, onFile: path => files.push(path) }) })
  app.mount(host.root); await flush(); t.after(() => app.unmount())
  return { state, host, files,
    heading: () => host.find(n => n.kind === 'button' && n.props['aria-controls'] === 'operations-' + state.operations[0].id),
    rows: () => host.find(n => n.props?.id === 'operations-' + state.operations[0].id),
  }
}

test('group summary stays neutral and leaves file links inside tool details', async t => {
  const m = await mount(t, [tool('a', { status: 'failed', finished_at: 5, warnings: ['Overflow'] }),
    tool('b', { started_at: 2, finished_at: 4, files: ['outputs/result.csv'] })])
  assert.equal(m.heading().props['aria-expanded'], false)
  assert.equal(m.rows().style.display, 'none')
  assert.match(text(m.heading()), /2 项操作 · 已结束 · 4 秒/)
  assert.doesNotMatch(text(m.heading()), /失败|警告|修复|成功/)
  assert.equal(m.state.operations[0].status, 'failed', 'historical failure is not rewritten as recovered')
  assert.equal(m.state.operations[0].warnings[0], 'Overflow')
  assert.equal(m.host.find(n => n.kind === 'button' && text(n) === 'result.csv'), undefined)
  assert.equal(m.state.operations[1].files[0], 'outputs/result.csv')
  m.heading().props.onClick(); await flush()
  assert.notEqual(m.rows().style.display, 'none')
  const child = m.host.find(n => n.props?.['data-tool'] === 'a')
  child.props.onClick(); await flush()
  m.heading().props.onClick(); await flush()
  m.heading().props.onClick(); await flush()
  assert.equal(child.props['aria-expanded'], true, 'folding a group preserves its open detail rows')
})

test('a single open operation remains open as streaming appends siblings and completes', async t => {
  const m = await mount(t, [tool('a', { status: 'running', finished_at: undefined })])
  assert.equal(m.heading(), undefined, 'single operation has no extra heading')
  m.host.find(n => n.props?.['data-tool'] === 'a').props.onClick(); await flush()
  m.state.operations.push(tool('b', { status: 'running', label: '读取 CSV', finished_at: undefined })); await flush()
  assert.equal(m.heading().props['aria-expanded'], true)
  m.state.operations = [tool('a'), tool('b')]; await flush()
  assert.equal(m.heading().props['aria-expanded'], true, 'completion must not fold a user-opened group')
  m.heading().props.onClick(); await flush()
  m.state.operations.push(tool('c', { status: 'running', label: '编译 PDF', finished_at: undefined })); await flush()
  assert.equal(m.heading().props['aria-expanded'], false, 'streaming must not reopen a user-folded group')
  assert.match(text(m.host.root), /正在编译 PDF/)
})
