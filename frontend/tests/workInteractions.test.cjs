const assert = require('node:assert/strict')
const { loadModule, vue, pinia, flush } = require('./moduleHarness.cjs')
async function environment() {
  const auth = vue.reactive({ token: 'test', currentUser: { id: 7 } })
  const storeModule = await loadModule('src/stores/workPanels.ts', {
    vue,
    pinia,
    '@/stores/auth': { useAuthStore: () => auth },
  })
  const store = storeModule.useWorkPanelsStore(pinia.createPinia())
  async function work(id, deferred = false) {
    const cleanup = [],
      sockets = [],
      sent = [],
      connected = []
    const scope = vue.effectScope()
    class Socket {
      constructor(workId) {
        this.workId = workId
        sockets.push(this)
      }
      onMessage(callback) {
        this.message = callback
      }
      onReconnect() {}
      onDisconnect() {}
      connect() {
        return deferred ? new Promise((resolve) => connected.push(resolve)) : Promise.resolve()
      }
      sendMessage(message) {
        sent.push({ workId: this.workId, message })
      }
      disconnect() {
        this.closed = true
      }
    }
    const route = vue.reactive({ params: { work_id: id } })
    const module = await loadModule(
      'src/views/Work.vue',
      {
        vue: { ...vue, onMounted() {}, onUnmounted: (callback) => cleanup.push(callback) },
        'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
        'tdesign-vue-next': {
          DialogPlugin: {},
          MessagePlugin: { error() {}, success() {}, info() {} },
        },
        '@/stores/auth': { useAuthStore: () => auth },
        '@/stores/workPanels': { useWorkPanelsStore: () => store },
        '@/stores/workHistory': { useWorkHistoryStore: () => ({ upsert() {} }) },
        '@/lang/studio': { useStudioCopy: () => ({ isZh: vue.ref(true), ui: vue.ref({}) }) },
        '@/composables/useBreakpoint': {
          useBreakpoint: () => ({ isMobile: vue.ref(false), width: vue.ref(1400) }),
        },
        '@/api/chat': {
          WebSocketChatHandler: Socket,
          chatAPI: {
            getWorkChatHistory: async () => ({
              messages: [
                {
                  id: 'saved',
                  role: 'assistant',
                  content: 'Saved result',
                  timestamp: '2026-10-16T06:00:00Z',
                },
              ],
            }),
            getTaskStatus: async () => ({ has_task: false }),
          },
        },
        '@/api/workspace': {
          workspaceAPI: {
            getWork: async () => ({
              id: 1,
              work_id: id,
              title: 'Research',
              status: 'completed',
              output_mode: 'latex',
            }),
          },
          attachmentAPI: {},
          workspaceFileAPI: {
            listFiles: async () => [
              { type: 'file', path: 'paper.pdf', name: 'paper.pdf', size: 100 },
            ],
            readFile: async () => ({ content: '{"items":[]}' }),
          },
        },
      },
      {
        document: { querySelector: () => null },
        window: { dispatchEvent() {} },
        localStorage: { getItem: () => null },
        CustomEvent: class {},
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        URL,
      },
    )
    const state = scope.run(() => module.default.setup({}, { expose() {} }))
    return {
      state,
      sockets,
      sent,
      route,
      connected,
      stop() {
        cleanup.forEach((callback) => callback())
        scope.stop()
      },
    }
  }
  return { store, work }
}
;(async () => {
  const env = await environment()
  const first = await env.work('first')
  await first.state.loadWork()
  assert.equal(
    first.state.panelSession.value.activeId,
    'chat',
    'completed work still opens its conversation by default',
  )
  first.state.inputValue.value = 'Unsent draft'
  first.state.handleWorkspaceFileSelect('code/model.py')
  first.state.panelSession.value.scroll['file:code/model.py'] = 460
  first.stop()
  const second = await env.work('second')
  assert.equal(second.state.inputValue.value, '')
  second.state.inputValue.value = 'Another draft'
  second.stop()
  const restored = await env.work('first')
  await restored.state.loadWork()
  assert.equal(restored.state.inputValue.value, 'Unsent draft')
  assert.equal(restored.state.panelSession.value.activeId, 'file:code/model.py')
  assert.equal(restored.state.panelSession.value.scroll['file:code/model.py'], 460)
  console.log('PASS: keyed work instances restore their own draft, tabs and document position')

  const keyEvent = (extra) => ({
    key: 'Enter',
    shiftKey: false,
    isComposing: false,
    preventDefault() {},
    ...extra,
  })
  restored.state.handleComposerKeydown('', { e: keyEvent({ shiftKey: true }) })
  restored.state.handleComposerKeydown('', { e: keyEvent({ isComposing: true }) })
  await flush()
  assert.equal(restored.sent.length, 0, 'newlines and IME confirmation do not submit')
  restored.state.handleComposerKeydown('', { e: keyEvent() })
  await flush()
  assert.deepEqual(restored.sent, [{ workId: 'first', message: 'Unsent draft' }])
  assert.equal(restored.state.inputValue.value, '')
  assert.equal(restored.state.isStreaming.value, true)
  assert.equal(restored.state.panelSession.value.activeId, 'chat')
  env.store.split('first', 'file:code/model.py', 'right')
  env.store.open('first', 'files', undefined, 'secondary')
  restored.sockets[0].message({ type: 'content', content: 'Still streaming' })
  assert.equal(restored.state.chatMessages.value.at(-1).content, 'Still streaming')
  assert.equal(restored.sockets.length, 1, 'switching and splitting tabs keeps the same stream')
  assert.equal(restored.state.panelSession.value.secondaryActiveId, 'files')
  restored.sockets[0].message({ type: 'complete', work_status: 'completed' })
  assert.equal(restored.state.isStreaming.value, false)
  assert.equal(restored.state.panelSession.value.secondaryActiveId, 'files')
  restored.stop()
  console.log('PASS: one-line submission respects IME and streaming continues across split tabs')

  const leaving = await env.work('leaving', true)
  const send = leaving.state.sendMessage('Do not send after leaving')
  leaving.stop()
  leaving.connected[0]()
  await send
  assert.equal(leaving.sent.length, 0)
  assert.equal(leaving.sockets[0].closed, true)
  console.log('PASS: a late socket connection cannot submit into an unmounted work')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
