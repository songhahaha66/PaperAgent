const assert = require('node:assert/strict')
const { loadModule, flush } = require('./moduleHarness.cjs')
;(async () => {
  const sockets = [],
    timers = new Map()
  let timerId = 0
  class WebSocket {
    static OPEN = 1
    constructor() {
      this.readyState = 0
      this.sent = []
      sockets.push(this)
    }
    open() {
      this.readyState = 1
      this.onopen?.()
    }
    send(raw) {
      this.sent.push(JSON.parse(raw))
    }
    receive(data) {
      this.onmessage?.({ data: JSON.stringify(data) })
    }
    close(code = 1006) {
      this.readyState = 3
      this.onclose?.({ code })
    }
  }
  const { WebSocketChatHandler } = await loadModule(
    'src/api/chat.ts',
    {
      '@/utils/apiClient': { apiClient: {} },
    },
    {
      WebSocket,
      setTimeout: (fn) => {
        timers.set(++timerId, fn)
        return timerId
      },
      clearTimeout: (id) => timers.delete(id),
      setInterval: () => ++timerId,
      clearInterval() {},
    },
  )
  const events = [],
    reconnects = []
  const handler = new WebSocketChatHandler('work', 'token')
  handler.onMessage((e) => events.push(e))
  handler.onReconnect((e) => reconnects.push(e.type))
  let ready = false
  const pending = handler.connect().then(() => {
    ready = true
  })
  const first = sockets[0]
  const sourceIssue = { id: 'issue-one', version: 'a'.repeat(64) }
  handler.sendMessage('question', undefined, 'auto', 'Q1', undefined, undefined, 'request', sourceIssue)
  first.open()
  await flush()
  assert.equal(ready, false, 'TCP open is not an authenticated subscription')
  assert.equal(first.sent.length, 1, 'only authentication can precede auth_success')
  first.receive({ type: 'auth_success', task: { status: 'none' } })
  await pending
  assert.equal(first.sent[1].request_id, 'request')
  assert.equal(first.sent[1].action, 'auto')
  assert.equal(first.sent[1].subproblem_id, 'Q1')
  assert.deepEqual(first.sent[1].source_issue, sourceIssue)
  assert.equal(first.sent[1].revision, undefined)
  first.receive({ type: 'start', task_id: 'one' })
  first.receive({ type: 'content', task_id: 'one', seq: 1, content: 'A' })
  first.receive({ type: 'content', task_id: 'one', seq: 1, content: 'duplicate' })
  first.close()
  assert.ok(reconnects.includes('connection_reconnecting'))
  assert.equal(timers.size, 1, 'failed connection owns one reconnect timer')
  const retry = [...timers.entries()][0]
  timers.delete(retry[0])
  retry[1]()
  const second = sockets[1]
  second.open()
  assert.equal(second.sent[0].task_id, 'one')
  assert.equal(second.sent[0].last_seq, 1)
  second.receive({ type: 'auth_success', task: { status: 'running', task_id: 'one' } })
  second.receive({ type: 'reconnect', task_id: 'one' })
  second.receive({ type: 'content', task_id: 'one', seq: 1, content: 'duplicate' })
  second.receive({ type: 'content', task_id: 'one', seq: 2, content: 'B' })
  second.receive({ type: 'complete', task_id: 'one' })
  second.receive({ type: 'start', task_id: 'two' })
  second.receive({ type: 'content', task_id: 'two', seq: 1, content: 'C' })
  second.receive({ type: 'content', task_id: 'one', seq: 3, content: 'old task' })
  assert.deepEqual(
    events.filter((e) => e.type === 'content').map((e) => e.content),
    ['A', 'B', 'C'],
  )
  await flush()
  handler.disconnect()
  assert.equal(timers.size, 0)
  first.receive({ type: 'start', task_id: 'stale socket' })
  assert.equal(events.at(-1).content, 'C', 'old socket callbacks cannot mutate the view')
  const abandoned = new WebSocketChatHandler('work', 'token')
  const connecting = abandoned.connect().then(
    () => assert.fail('disposed connection became ready'),
    () => {},
  )
  abandoned.disconnect()
  await connecting
  assert.equal(timers.size, 0, 'disposal clears authentication timeout and rejects connect')
  console.log('PASS: socket authentication, task cursors, reconnect and disposal')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
