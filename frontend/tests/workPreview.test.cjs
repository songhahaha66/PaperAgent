const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
async function fixture(overrides = {}) {
  const reads = [],
    cleanup = [],
    events = []
  const props = vue.reactive({
    workId: 'first',
    path: 'paper.md',
    paper: true,
    available: true,
    active: true,
    scrollTop: 0,
    ...overrides,
  })
  const scope = vue.effectScope()
  const module = await loadModule('src/components/WorkDocument.vue', {
    vue: { ...vue, onUnmounted: (callback) => cleanup.push(callback) },
    '@/stores/auth': { useAuthStore: () => ({ token: 'test' }) },
    '@/lang/studio': { useStudioCopy: () => ({ isZh: vue.ref(true), ui: vue.ref({}) }) },
    '@/api/workspace': {
      workspaceFileAPI: {
        readFile(token, workId, path) {
          return new Promise((resolve, reject) => reads.push({ workId, path, resolve, reject }))
        },
      },
    },
  })
  const state = scope.run(() =>
    module.default.setup(props, { expose() {}, emit: (...args) => events.push(args) }),
  )
  return {
    props,
    state,
    reads,
    events,
    stop() {
      cleanup.forEach((callback) => callback())
      scope.stop()
    },
  }
}
const text = (content) => ({ type: 'text', content, filename: 'paper.md', size: content.length })
;(async () => {
  let f = await fixture()
  f.reads[0].resolve(text('# Finished paper'))
  await flush()
  assert.equal(f.state.data.value.content, '# Finished paper')
  f.props.active = false
  await flush()
  f.props.active = true
  await flush()
  assert.equal(f.reads.length, 1, 'switching tabs retains the loaded content')
  f.stop()
  console.log('PASS: document tabs load once and retain their content when hidden')

  f = await fixture({ available: false })
  assert.equal(f.reads.length, 0, 'an unfinished paper does not request a nonexistent file')
  f.props.available = true
  await flush()
  assert.equal(f.reads.length, 1)
  f.reads[0].resolve(text('New paper'))
  await flush()
  assert.equal(f.state.data.value.content, 'New paper')
  f.stop()
  console.log('PASS: a paper tab opens its result when generation finishes')

  f = await fixture({ path: 'paper.tex', paper: true })
  assert.equal(f.reads[0].path, 'paper.tex')
  f.reads[0].resolve({
    type: 'text',
    content: '\\section{模型}正文',
    filename: 'paper.tex',
    size: 30,
  })
  await flush()
  assert.equal(f.state.codeLanguage.value, 'latex')
  f.props.path = 'paper.pdf'
  await flush()
  assert.equal(f.reads.at(-1).path, 'paper.pdf')
  f.reads
    .at(-1)
    .resolve({ type: 'binary', filename: 'paper.pdf', size: 200, download_url: '/paper.pdf' })
  await flush()
  assert.equal(f.state.loadedPath.value, 'paper.pdf')
  f.stop()
  console.log('PASS: native source uses LaTeX highlighting and switches to the compiled PDF')

  f = await fixture({ fallbackPath: 'paper.pdf' })
  f.reads[0].reject(new Error('No Markdown source'))
  await flush()
  assert.equal(f.reads[1].path, 'paper.pdf')
  f.reads[1].resolve({
    type: 'binary',
    filename: 'paper.pdf',
    size: 100,
    download_url: '/paper.pdf',
  })
  await flush()
  assert.equal(f.state.loadedPath.value, 'paper.pdf')
  f.stop()
  f = await fixture({ fallbackPath: 'paper.pdf' })
  f.reads[0].resolve(text(''))
  await flush()
  assert.equal(
    f.reads[1].path,
    'paper.pdf',
    'an empty source also falls back to the formatted result',
  )
  f.stop()
  console.log('PASS: missing and empty Markdown sources fall back to the formatted document')

  f = await fixture({ paper: false, path: 'code/one.py' })
  f.props.path = 'code/two.py'
  await flush()
  f.reads[1].resolve(text('Second file'))
  await flush()
  f.reads[0].resolve(text('Late first file'))
  await flush()
  assert.equal(f.state.data.value.content, 'Second file')
  f.stop()
  f = await fixture()
  f.stop()
  f.reads[0].resolve(text('Closed tab'))
  await flush()
  assert.equal(f.state.data.value, null)
  console.log('PASS: stale reads cannot replace a newer document or revive a closed tab')

  f = await fixture({ paper: false })
  f.reads[0].reject(new Error('Network failure'))
  await flush()
  assert.equal(f.state.error.value, true)
  assert.equal(f.state.loading.value, false)
  const retry = f.state.load()
  f.reads[1].resolve(text('Recovered'))
  await retry
  assert.equal(f.state.error.value, false)
  assert.equal(f.state.data.value.content, 'Recovered')
  f.stop()
  console.log('PASS: failed reads expose retry and recover without reopening the tab')

  f = await fixture({ scrollTop: 480, paper: false })
  const scroller = { scrollTop: 0 }
  f.state.scroller.value = scroller
  f.reads[0].resolve(text('Long document'))
  await flush()
  assert.equal(scroller.scrollTop, 480)
  f.props.active = false
  await flush()
  scroller.scrollTop = 0
  f.state.rememberScroll()
  assert.equal(f.events.length, 0, 'hiding a tab must not overwrite its reading position')
  f.props.active = true
  await flush()
  assert.equal(scroller.scrollTop, 480)
  scroller.scrollTop = 720
  f.state.rememberScroll()
  assert.deepEqual(f.events.at(-1), ['scroll-position', 720])
  f.stop()
  console.log('PASS: reading positions restore and hidden-tab scroll events are ignored')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
