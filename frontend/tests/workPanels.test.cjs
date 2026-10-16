const assert = require('node:assert/strict')
const { loadModule, vue, pinia } = require('./moduleHarness.cjs')
;(async () => {
  const auth = vue.reactive({ token: 'test', currentUser: { id: 7 } })
  const module = await loadModule('src/stores/workPanels.ts', {
    vue,
    pinia,
    '@/stores/auth': { useAuthStore: () => auth },
  })
  const store = module.useWorkPanelsStore(pinia.createPinia())
  const first = store.session('first')
  assert.equal(first.activeId, 'chat')
  first.draft = 'Unsent question'
  first.scroll.chat = 340
  first.followChat = false
  store.open('first', 'file', 'code/model.py')
  first.scroll['file:code/model.py'] = 490
  store.open('first', 'figures')
  store.open('first', 'file', 'code/model.py')
  assert.equal(first.panels.length, 3, 'reopening an existing file activates its tab')
  assert.equal(first.activeId, 'file:code/model.py')
  assert.equal(first.scroll[first.activeId], 490)
  const second = store.session('second')
  assert.equal(second.activeId, 'chat')
  assert.equal(second.draft, '')
  second.draft = 'Another question'
  assert.equal(store.session('first').draft, 'Unsent question')
  assert.equal(store.session('first').scroll.chat, 340)
  assert.equal(store.session('first').followChat, false)
  console.log('PASS: tabs, drafts, and reading positions survive work switches without mixing')
  store.close('first', 'figures')
  assert.equal(first.activeId, 'file:code/model.py', 'closing a background tab preserves selection')
  store.close('first', 'file:code/model.py')
  assert.equal(first.activeId, 'chat')
  assert.equal(first.scroll['file:code/model.py'], undefined)
  store.close('first', 'chat')
  assert.equal(first.panels.length, 1, 'conversation stays pinned')
  store.activate('first', 'unknown')
  assert.equal(first.activeId, 'chat')
  console.log('PASS: closing tabs selects a valid neighbor and cannot close the conversation')
  store.open('first', 'file', 'code/a.py')
  store.open('first', 'file', 'code/b.py')
  first.scroll['file:code/a.py'] = 640
  for (const edge of ['left', 'right', 'top', 'bottom']) {
    store.split('first', 'file:code/a.py', edge)
    assert.equal(first.secondaryActiveId, 'file:code/a.py')
    assert.equal(first.splitAxis, edge === 'top' || edge === 'bottom' ? 'vertical' : 'horizontal')
    assert.equal(first.secondaryFirst, edge === 'left' || edge === 'top')
    assert.equal(first.activeId, 'file:code/b.py')
  }
  store.move('first', 'file:code/b.py', 'secondary', 'file:code/a.py')
  assert.equal(first.activeId, 'chat')
  assert.deepEqual(
    Array.from(
      first.panels.filter((panel) => panel.group === 'secondary').map((panel) => panel.id),
    ),
    ['file:code/b.py', 'file:code/a.py'],
  )
  store.move('first', 'file:code/a.py', 'main')
  store.move('first', 'file:code/b.py', 'main')
  assert.equal(first.secondaryActiveId, null, 'dragging all tabs back collapses the empty pane')
  assert.equal(first.scroll['file:code/a.py'], 640, 'moving tabs preserves their reading position')
  assert.equal(first.panels.length, 3, 'splitting and merging never duplicates document tabs')
  console.log('PASS: four-edge splits, cross-pane ordering, and drag-to-merge preserve tabs')
  store.remove('first')
  assert.equal(store.sessions.first, undefined)
  assert.equal(store.session('second').draft, 'Another question')
  auth.token = null
  assert.equal(Object.keys(store.sessions).length, 0)
  auth.currentUser = { id: 9 }
  auth.token = 'new'
  assert.equal(store.session('second').draft, '')
  console.log('PASS: deleting a work and signing out clear only the appropriate UI state')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
