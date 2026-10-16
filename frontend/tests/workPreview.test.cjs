// Exercise Work.vue's real setup and watchers with controlled file responses.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const frontend = path.resolve(__dirname, '..')
const projectRequire = createRequire(path.join(frontend, 'package.json'))
const vue = projectRequire('vue')
const { parse, compileScript } = projectRequire('@vue/compiler-sfc')
const ts = projectRequire('typescript')
const descriptor = parse(
  fs.readFileSync(path.join(frontend, 'src/views/Work.vue'), 'utf8'),
).descriptor
const script = compileScript(descriptor, { id: 'work-preview-test' }).content
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText
async function fixture() {
  const reads = []
  const route = vue.reactive({ params: { work_id: 'first' } })
  const isMobile = vue.ref(true)
  const context = vm.createContext({
    console: { log() {}, error() {} },
    window: { dispatchEvent() {} },
    document: {
      querySelector() {
        return null
      },
    },
    localStorage: {
      getItem() {
        return null
      },
    },
    CustomEvent: class {},
    URL,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  })
  const scope = vue.effectScope()
  const imports = {
    vue: { ...vue, onMounted() {}, onUnmounted() {} },
    'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
    'tdesign-vue-next': { DialogPlugin: {}, MessagePlugin: { error() {} } },
    '@tdesign-vue-next/chat': { ChatItem: {}, ChatSender: {} },
    '@/stores/auth': { useAuthStore: () => ({ token: 'test' }) },
    '@/stores/workHistory': { useWorkHistoryStore: () => ({ upsert() {} }) },
    '@/composables/useBreakpoint': { useBreakpoint: () => ({ isMobile }) },
    '@/lang/studio': { useStudioCopy: () => ({ isZh: vue.ref(true), ui: vue.ref({}) }) },
    '@/utils/dates': { formatWorkDate() {} },
    '@/api/chat': { chatAPI: {}, WebSocketChatHandler: class {} },
    '@/api/workspace': {
      workspaceAPI: { getWork: () => new Promise(() => {}) },
      attachmentAPI: {},
      workspaceFileAPI: {
        readFile(token, workId, filePath) {
          return new Promise((resolve, reject) => reads.push({ workId, filePath, resolve, reject }))
        },
      },
    },
  }
  const module = new vm.SourceTextModule(transpile(script), {
    context,
    initializeImportMeta(meta) {
      meta.env = {}
    },
  })
  await module.link(async (id) => {
    if (id === '@/utils/paperPipeline') {
      const pipeline = new vm.SourceTextModule(
        transpile(fs.readFileSync(path.join(frontend, 'src/utils/paperPipeline.ts'), 'utf8')),
        { context },
      )
      await pipeline.link(() => {
        throw new Error('Unexpected pipeline import')
      })
      return pipeline
    }
    const values = imports[id] || (id.endsWith('.vue') ? { default: {} } : null)
    assert.ok(values, `unexpected import ${id}`)
    return new vm.SyntheticModule(
      Object.keys(values),
      function () {
        for (const [key, value] of Object.entries(values)) this.setExport(key, value)
      },
      { context },
    )
  })
  await module.evaluate()
  const state = scope.run(() => module.namespace.default.setup({}, { expose() {} }))
  return { state, reads, route, isMobile, stop: () => scope.stop() }
}
const paper = { type: 'file', path: 'paper.pdf', name: 'paper.pdf', size: 100 }
const text = (content) => ({ type: 'text', content })
const flush = async () => {
  await vue.nextTick()
  await new Promise(setImmediate)
}
;(async () => {
  let f = await fixture()
  f.state.currentWork.value = { status: 'completed', output_mode: 'latex' }
  f.state.workspaceFiles.value = [paper]
  await flush()
  assert.equal(f.reads.length, 0, 'available results do not interrupt the conversation')
  f.state.activeMobilePanel.value = 'preview'
  await flush()
  assert.equal(f.reads.length, 1, 'opening compact preview loads the existing paper')
  assert.equal(f.state.mainPaperLoading.value, true)
  f.reads[0].resolve(text('# Finished paper'))
  await flush()
  assert.equal(f.state.mainPaperContent.value, '# Finished paper')
  assert.equal(f.state.showMainPaper.value, true)
  assert.equal(f.state.mainPaperLoading.value, false)
  f.state.activeMobilePanel.value = 'chat'
  await flush()
  f.state.activeMobilePanel.value = 'preview'
  await flush()
  assert.equal(f.reads.length, 1, 'returning to preview reuses the opened document')
  f.stop()
  console.log('PASS: compact preview opens existing results without stealing focus or refetching')

  f = await fixture()
  const first = f.state.handleWorkspaceFileSelect('first.txt')
  const second = f.state.handleWorkspaceFileSelect('second.txt')
  f.reads[1].resolve(text('Second document'))
  await second
  f.reads[0].resolve(text('Old document'))
  await first
  assert.equal(f.state.selectedFile.value, 'second.txt')
  assert.equal(f.state.currentFileContent.value, 'Second document')
  f.stop()
  console.log('PASS: a late file response cannot replace the newly selected file')

  f = await fixture()
  const main = f.state.handleMainPaperClick()
  const chosen = f.state.handleWorkspaceFileSelect('chosen.txt')
  f.reads[1].resolve(text('Chosen content'))
  await chosen
  f.reads[0].resolve(text('Late paper'))
  await main
  assert.equal(f.state.selectedFile.value, 'chosen.txt')
  assert.equal(f.state.showMainPaper.value, false)
  assert.equal(f.state.currentFileContent.value, 'Chosen content')
  f.stop()
  console.log('PASS: automatic paper loading respects a subsequent manual file selection')

  f = await fixture()
  const old = f.state.handleMainPaperClick()
  f.route.params.work_id = 'another-work'
  await flush()
  f.reads[0].resolve(text('Previous work paper'))
  await old
  assert.equal(f.state.selectedFile.value, null)
  assert.equal(f.state.mainPaperContent.value, '')
  assert.equal(f.state.mainPaperLoading.value, false)
  f.stop()
  console.log('PASS: switching work invalidates in-flight preview reads')

  f = await fixture()
  f.state.currentWork.value = { status: 'completed', output_mode: 'latex' }
  f.state.workspaceFiles.value = [paper]
  const fallback = f.state.handleMainPaperClick()
  f.reads[0].reject(new Error('No Markdown source'))
  await flush()
  assert.equal(f.reads[1].filePath, 'paper.pdf')
  f.reads[1].resolve({ type: 'binary', filename: 'paper.pdf', download_url: '/paper.pdf' })
  await fallback
  assert.equal(f.state.selectedFile.value, 'paper.pdf')
  const broken = f.state.handleWorkspaceFileSelect('broken.txt')
  f.reads[2].reject(new Error('Read failed'))
  await broken
  assert.equal(
    f.state.previewError.value,
    true,
    'read failure offers retry instead of an endless spinner',
  )
  f.stop()
  console.log('PASS: formatted-document fallback and retryable failures work')

  f = await fixture()
  f.isMobile.value = false
  await flush()
  f.state.resourceTab.value = 'files'
  f.isMobile.value = true
  await flush()
  assert.equal(f.state.activeMobilePanel.value, 'files')
  assert.equal(f.state.resourceTab.value, 'paper')
  f.stop()
  console.log('PASS: resizing a file view preserves access to files on compact layouts')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
