const assert = require('node:assert/strict')
const { loadModule, flush } = require('./moduleHarness.cjs')
;(async () => {
  const timers = new Map()
  let sequence = 0
  const { createWorkspaceRefresh } = await loadModule('src/composables/workspaceRefresh.ts', {}, {
    setTimeout(fn) { const id = ++sequence; timers.set(id, fn); return id },
    clearTimeout(id) { timers.delete(id) },
  })
  let files = 0, research = 0, release
  const coordinator = createWorkspaceRefresh({
    files: async () => { files++; if (files === 1) await new Promise(resolve => { release = resolve }) },
    research: async () => { research++ },
  }, () => false)
  for (let i = 0; i < 20; i++) coordinator.schedule(['files'])
  assert.equal(timers.size, 1, 'file bursts create one scheduled refresh')
  const pending = coordinator.request(['files'])
  assert.equal(timers.size, 0, 'explicit refresh consumes the pending event')
  await flush()
  assert.equal(files, 1)
  for (let i = 0; i < 20; i++) coordinator.schedule(['files'])
  await coordinator.request(['research'])
  assert.equal(research, 1, 'independent resources can refresh while files are loading')
  assert.equal(files, 1, 'no parallel request for the same resource')
  release(); await pending
  assert.equal(files, 2, 'invalidations during a request get exactly one trailing refresh')
  coordinator.schedule(['files', 'research'])
  coordinator.dispose()
  assert.equal(timers.size, 0)
  await coordinator.request(['files'])
  assert.equal(files, 2, 'disposal stops subsequent loads')
  let attempts = 0
  const retry = createWorkspaceRefresh({ files: async () => { if (++attempts === 1) throw Error('offline') } }, () => false)
  await assert.rejects(retry.request(['files']), /offline/)
  await retry.request(['files'])
  assert.equal(attempts, 2, 'a failed request does not permanently block refresh')
  console.log('PASS: workspace refresh coalesces bursts, retains trailing invalidations and stops after disposal')
})().catch(error => { console.error(error); process.exitCode = 1 })
