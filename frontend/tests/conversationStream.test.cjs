const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const deadline = setTimeout(() => {
  console.error('FAIL: conversation stream assertions did not complete within 10 seconds')
  process.exit(1)
}, 10000)
;(async () => {
  const frame=(seq,text,reply_id='reply',chat_task_id='chat-task',status='running')=>({
    type:'agent_event',task_id:chat_task_id,update:{reply_id,chat_task_id,thread_id:'lead',turn_id:reply_id,
      event:{seq,method:status==='completed'?'item/completed':'item/agentMessage/delta',thread_id:'lead',turn_id:reply_id,
        item:{id:reply_id==='reply'?'text':'remote-text',thread_id:'lead',turn_id:reply_id,sequence:1,type:'agentMessage',phase:'final_answer',status,started_at:1,text}}}})
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
    sendMessage(...args) {
      this.sent = args
    }
    disconnect() {}
  }
  const saved = {
    id: 'reply',
    role: 'assistant',
    content: 'Before',
    timestamp: '2026-10-22T00:00:00Z',
    items: [{ id:'text',type:'agentMessage',text:'Before',thread_id:'lead',turn_id:'reply',sequence:1,status:'running',started_at:1 }],
    metadata: { reply_id: 'reply', chat_task_id: 'chat-task', thread_id:'lead',turn_id:'reply',event_seq: 2, status: 'running' },
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
      '@/api/workspace': {
        workspaceAPI: { getWork: async () => ({ status: 'completed', architecture_version: 2 }) },
      },
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
      crypto: require('node:crypto').webcrypto,
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
  let socket = sockets[0]
  socket.message({ type: 'reconnect', reply_id: 'reply', task_id: 'chat-task' })
  socket.message(frame(2,'Before','reply','chat-task','running'))
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
  socket.message(frame(3,'Before plus live','reply','chat-task','running'))
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
  await state.checkAndResumeRunningTask()
  socket = sockets.at(-1)
  socket.message({ type: 'reconnect', reply_id: 'reply', task_id: 'chat-task' })
  history = [
    older,
    {
      ...saved,
      content: 'Terminal answer',
      items: [{ id: 'text', type: 'agentMessage', text: 'Terminal answer' }],
      metadata: { ...saved.metadata, event_seq: 4, status: 'completed' },
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

  await state.checkAndResumeRunningTask()
  assert.equal(sockets.length, 2, 'idle view keeps its existing workspace subscription')
  socket.message({ type: 'start', task_id: 'remote-task', reply_id: 'remote-reply', action: 'run' })
  assert.equal(state.isStreaming.value, true, 'another browser starts a task in an idle view')
  assert.equal(
    state.chatMessages.value.find((m) => m.id === 'reply').content,
    'Terminal answer',
    'a new remote turn never renames the previous durable reply',
  )
  socket.message(frame(2,'Remote answer','remote-reply','remote-task','running'))
  socket.message({ type: 'error', scope: 'command', message: 'busy' })
  assert.equal(state.isStreaming.value, true, 'a command rejection cannot terminate a shared run')
  const refreshBeforeFinish = state.loadChatHistory()
  socket.message(frame(3,'Final remote answer','remote-reply','remote-task','completed'))
  await refreshBeforeFinish
  assert.equal(
    state.chatMessages.value.find((m) => m.id === 'remote-reply').content,
    'Final remote answer',
    'an older history response cannot drop a just-completed reply',
  )
  state.chatMessages.value = state.chatMessages.value.filter((m) => m.id !== 'remote-reply')
  socket.message({ type: 'complete', task_id: 'remote-task', reply_id: 'remote-reply' })
  assert.equal(state.isStreaming.value, false, 'completion works without a chat placeholder')
  await flush()
  await state.sendMessage('local question', 'ask')
  const requestId = socket.sent[6]
  socket.message({
    type: 'start',
    task_id: 'local-task',
    reply_id: 'local-reply',
    action: 'ask',
    request_id: requestId,
  })
  assert.equal(state.chatMessages.value.filter((m) => m.id === 'local-reply').length, 1)
  assert.equal(sockets.length, 2, 'sending a new turn reuses the long-lived subscription')
  socket.message({ type: 'complete', task_id: 'local-task', reply_id: 'local-reply' })
  await flush()
  await state.sendMessage('finished offline', 'ask')
  socket.message({
    type: 'auth_success',
    task: { status: 'completed', request_id: socket.sent[6] },
  })
  assert.equal(
    state.isStreaming.value,
    false,
    'reconnect recovers a turn that finished before its start acknowledgement arrived',
  )
  assert.equal(
    state.chatMessages.value.some((m) => m.content === 'finished offline'),
    false,
    'unacknowledged optimistic rows give way to durable history after reconnect',
  )
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
