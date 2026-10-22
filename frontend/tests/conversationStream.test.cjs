const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const deadline = setTimeout(() => {
  console.error('FAIL: conversation stream assertions did not complete within 10 seconds')
  process.exit(1)
}, 10000)
;(async () => {
  const sockets = [],
    cleanup = []
  let history, pendingHistory
  class Socket {
    constructor() {
      sockets.push(this)
    }
    onMessage(callback) {
      this.message = callback
    }
    onReconnect(callback) {
      this.reconnect = callback
    }
    onDisconnect(callback) {
      this.lost = callback
    }
    connect() {
      return Promise.resolve()
    }
    disconnect() {}
  }
  const saved = {
    id: 'reply',
    role: 'assistant',
    content: 'Before',
    timestamp: '2026-10-22T00:00:00Z',
    segments: [{ id: 'text', type: 'text', text: 'Before' }],
    metadata: { reply_id: 'reply', chat_task_id: 'chat-task', revision: 2, status: 'running' },
  }
  const older = {
    id: 'older',
    role: 'assistant',
    content: 'Previous turn',
    timestamp: '2026-10-21T00:00:00Z',
  }
  const comment = {
    id: 'comment',
    role: 'assistant',
    content: 'Paper reply',
    timestamp: '2026-10-22T00:00:01Z',
    paper_execution: { id: 'paper', state: 'pending' },
  }
  history = [older, saved, comment]
  let taskStatus = {
    has_task: true,
    status: 'running',
    task_id: 'chat-task',
    reply_id: 'reply',
    action: 'ask',
  }
  const module = await loadModule(
    'src/composables/useWorkStream.ts',
    {
      vue: { ...vue, onMounted() {}, onUnmounted: (fn) => cleanup.push(fn) },
      'tdesign-vue-next': { MessagePlugin: { warning() {}, error() {}, info() {} } },
      '@/stores/auth': { useAuthStore: () => ({ token: 'test' }) },
      '@/lang/studio': { useStudioCopy: () => ({ ui: vue.ref({}), isZh: vue.ref(true) }) },
      '@/api/workspace': { workspaceAPI: { getWork: async () => ({ status: 'completed', architecture_version: 2 }) } },
      '@/api/chat': {
        WebSocketChatHandler: Socket,
        chatAPI: {
          getWorkChatHistory: async () =>
            pendingHistory ? await pendingHistory : { messages: history },
          getTaskStatus: async () => taskStatus,
        },
      },
    },
    {
      setTimeout: () => 1,
      clearTimeout() {},
      setInterval: () => 1,
      clearInterval() {},
      document: { querySelector: () => null },
    },
  )
  const scope = vue.effectScope()
  const state = scope.run(() =>
    module.useWorkStream({
      session: {
        workId: vue.ref('work'),
        currentWork: vue.ref({ status: 'in_progress', architecture_version: 2 }),
        researchSnapshot: vue.ref(null),
        applyWorkStatus() {},
        applyResearchSnapshot() {},
        loadResearchSnapshot: async () => {},
        invalidateSnapshot() {},
        isDisposed: () => false,
      },
      layout: {
        inputValue: vue.ref(''),
        atBottom: vue.ref(true),
        panelSession: vue.ref({ followChat: true }),
        restorePanelScroll: async () => {},
        scrollToBottom() {},
        activateChat() {},
      },
      refreshFiles: async () => {},
    }),
  )
  await state.loadChatHistory()
  await state.checkAndResumeRunningTask()
  assert.equal(
    state.chatMessages.value.filter((m) => m.id === 'reply').length,
    1,
    'refresh/reconnect binds persisted reply',
  )
  const socket = sockets[0]
  socket.message({ type: 'reconnect', reply_id: 'reply', task_id: 'chat-task' })
  socket.message({
    type: 'reply_update',
    update: {
      reply_id: 'reply',
      chat_task_id: 'chat-task',
      revision: 2,
      status: 'running',
      segments: saved.segments,
    },
  })
  assert.equal(
    state.chatMessages.value.find((m) => m.id === 'reply').content,
    'Before',
    'replay does not duplicate text',
  )
  let resolveHistory
  pendingHistory = new Promise((resolve) => {
    resolveHistory = resolve
  })
  const load = state.loadChatHistory()
  socket.message({
    type: 'reply_update',
    update: {
      reply_id: 'reply',
      chat_task_id: 'chat-task',
      revision: 3,
      status: 'running',
      segments: [{ id: 'text', type: 'text', text: 'Before plus live' }],
    },
  })
  resolveHistory({ messages: history })
  await load
  pendingHistory = null
  assert.equal(
    state.chatMessages.value.find((m) => m.id === 'reply').content,
    'Before plus live',
    'old history response cannot overwrite live revision',
  )
  socket.lost()
  await flush()
  assert.equal(state.isStreaming.value, true, 'browser disconnect does not fail execution')
  history = [
    older,
    {
      ...saved,
      content: 'Terminal answer',
      segments: [{ id: 'text', type: 'text', text: 'Terminal answer' }],
      metadata: { ...saved.metadata, revision: 4, status: 'completed' },
    },
    {
      ...comment,
      content: 'Paper complete',
      paper_execution: { ...comment.paper_execution, state: 'completed' },
    },
  ]
  taskStatus = { has_task: false }
  socket.message({ type: 'complete', reply_id: 'reply', reload_history: true })
  await flush()
  assert.equal(
    state.chatMessages.value.find((m) => m.id === 'reply').content,
    'Terminal answer',
    'terminal authority comes from durable history',
  )
  assert.equal(state.chatMessages.value.find((m) => m.id === 'older').content, 'Previous turn')
  assert.equal(
    state.chatMessages.value.filter((m) => m.id === 'comment').length,
    1,
    'paper comment projection is not duplicated',
  )
  assert.equal(state.chatMessages.value.find((m) => m.id === 'comment').isStreaming, false)
  for (const dispose of cleanup) dispose()
  scope.stop()
  console.log(
    'PASS: conversation stream refresh, replay, disconnect, terminal history and paper-comment assertions completed',
  )
})()
  .catch((error) => {
    console.error(error)
    process.exitCode = 1
  })
  .finally(() => clearTimeout(deadline))
