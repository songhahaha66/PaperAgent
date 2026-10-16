// Exercise the real Pinia store with controlled API responses, without a browser or extra test dependencies.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const path = require('node:path')
const frontend = path.resolve(__dirname, '..')
const projectRequire = createRequire(path.join(frontend, 'package.json'))
const vue = projectRequire('vue'),
  pinia = projectRequire('pinia'),
  ts = projectRequire('typescript')
const source = fs.readFileSync(path.join(frontend, 'src/stores/workHistory.ts'), 'utf8')
const js = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText
const work = (id, title = 'Original') => ({
  id: 1,
  work_id: id,
  title,
  status: 'created',
  created_at: '2026-10-16T00:00:00',
  updated_at: '2026-10-16T00:00:00',
  progress: 0,
  created_by: 7,
})
async function fixture() {
  let pending = [],
    calls = 0,
    now = 100000
  const auth = vue.reactive({ token: 'test', currentUser: { id: 7 } })
  const context = vm.createContext({
    console: { error() {} },
    Date: class extends Date {
      static now() {
        return now
      }
    },
  })
  const modules = {
    vue,
    pinia,
    '@/stores/auth': { useAuthStore: () => auth },
    '@/api/workspace': {
      workspaceAPI: {
        getWorks() {
          calls++
          return new Promise((resolve, reject) => pending.push({ resolve, reject }))
        },
      },
    },
  }
  const module = new vm.SourceTextModule(js, { context })
  await module.link(async (id) => {
    const values = modules[id]
    assert.ok(values, `unexpected import ${id}`)
    return new vm.SyntheticModule(
      Object.keys(values),
      function () {
        for (const [k, v] of Object.entries(values)) this.setExport(k, v)
      },
      { context },
    )
  })
  await module.evaluate()
  const store = module.namespace.useWorkHistoryStore(pinia.createPinia())
  return {
    store,
    auth,
    pending,
    get calls() {
      return calls
    },
    advance() {
      now += 31000
    },
  }
}
;(async () => {
  let f = await fixture()
  const first = f.store.ensureLoaded(),
    second = f.store.ensureLoaded()
  assert.equal(f.calls, 1, 'simultaneous sidebar and home loads share one request')
  f.pending.shift().resolve({ works: [work('one')] })
  await Promise.all([first, second])
  await f.store.ensureLoaded()
  assert.equal(f.calls, 1, 'route remount uses fresh cache')
  assert.equal(f.store.works.length, 1)
  assert.equal(f.store.hasLoaded, true)
  console.log('PASS: requests coalesce and route remounts keep cached rows')
  f.advance()
  const refresh = f.store.ensureLoaded()
  assert.equal(f.store.loading, true)
  assert.equal(f.store.works[0].title, 'Original')
  assert.equal(f.store.hasLoaded, true)
  f.pending.shift().reject(new Error('offline'))
  await refresh
  assert.equal(f.store.works.length, 1, 'failed background refresh preserves rows')
  console.log('PASS: background refresh and network failure never clear visible rows')
  const stale = f.store.ensureLoaded(true)
  f.store.patch('one', { title: 'Live title', status: 'running' })
  f.store.upsert(work('two', 'New work'))
  f.store.remove('three')
  f.pending.shift().resolve({ works: [work('one', 'Old server title'), work('three')] })
  await stale
  assert.equal(f.store.works.find((w) => w.work_id === 'one').title, 'Live title')
  assert.equal(f.store.works.find((w) => w.work_id === 'one').status, 'running')
  assert.ok(f.store.works.some((w) => w.work_id === 'two'))
  assert.ok(!f.store.works.some((w) => w.work_id === 'three'))
  console.log('PASS: stale responses preserve live changes, additions and deletions')
  const old = f.store.ensureLoaded(true)
  const request = f.pending.shift()
  f.auth.token = null
  assert.equal(f.store.works.length, 0)
  assert.equal(f.store.hasLoaded, false)
  f.auth.currentUser = { id: 9 }
  f.auth.token = 'second-user'
  const fresh = f.store.ensureLoaded()
  const freshRequest = f.pending.shift()
  request.resolve({ works: [work('private-old-user')] })
  await old
  assert.equal(f.store.loading, true)
  assert.equal(f.store.works.length, 0)
  freshRequest.resolve({ works: [work('second-user-work')] })
  await fresh
  assert.equal(f.store.works[0].work_id, 'second-user-work')
  console.log('PASS: logout clears the cache and late responses cannot cross accounts')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
