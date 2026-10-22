const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

;(async () => {
  const cleanup = [],
    revoked = [],
    notices = []
  const scope = vue.effectScope()
  const figuresVisible = vue.ref(false)
  const image = deferred()
  let fileReads = 0,
    imageReads = 0
  const files = [
    { path: 'paper.pdf', name: 'paper.pdf', type: 'file', size: 100 },
    { path: 'paper.md', name: 'paper.md', type: 'file', size: 80 },
    { path: 'figures/chart.png', name: 'chart.png', type: 'file', size: 50 },
    {
      path: 'attachments/photo.png',
      name: 'photo.png',
      type: 'file',
      category: 'attachments',
      size: 50,
    },
  ]
  const imports = {
    vue: { ...vue, onUnmounted: (callback) => cleanup.push(callback) },
    'tdesign-vue-next': {
      MessagePlugin: Object.fromEntries(
        ['error', 'success', 'info'].map((key) => [key, (message) => notices.push(message)]),
      ),
    },
    '@/stores/auth': { useAuthStore: () => ({ token: 'test' }) },
    '@/lang/studio': {
      useStudioCopy: () => ({
        ui: vue.ref({ downloadPdf: 'PDF', downloadWord: 'Word', downloadMd: 'Markdown' }),
        isZh: vue.ref(true),
      }),
    },
    '@/api/workspace': {
      workspaceFileAPI: {
        listFiles: async () => {
          fileReads++
          return files
        },
        getImageUrl: (_token, _work, path) => path,
      },
    },
  }
  const { useArtifactPreview } = await loadModule(
    'src/composables/useArtifactPreview.ts',
    imports,
    {
      fetch: async () => {
        imageReads++
        return image.promise
      },
      URL: { createObjectURL: () => 'blob:test', revokeObjectURL: (url) => revoked.push(url) },
    },
  )
  const work = vue.ref({ output_mode: 'latex' })
  const preview = scope.run(() =>
    useArtifactPreview({
      workId: vue.ref('one'),
      currentWork: work,
      researchSnapshot: vue.ref(null),
      figuresVisible,
    }),
  )
  await preview.loadWorkspaceFiles()
  assert.equal(preview.primaryArtifact.value.path, 'paper.pdf')
  assert.equal(preview.downloadLabel.value, 'PDF')
  work.value.output_mode = 'word'
  assert.equal(
    preview.primaryArtifact.value.path,
    'paper.md',
    'missing formatted output falls back to Markdown',
  )
  assert.equal(preview.figureFiles.value.length, 1, 'attachments are not research figures')
  await preview.loadWorkspaceFiles()
  assert.equal(fileReads, 2)
  await preview.loadWorkspaceFiles()
  assert.equal(fileReads, 3, 'refresh coalescing belongs to the workspace scheduler')
  await flush()
  assert.equal(imageReads, 0, 'hidden figure panes do not fetch image blobs')
  figuresVisible.value = true
  await flush()
  assert.equal(imageReads, 1)
  figuresVisible.value = false
  figuresVisible.value = true
  await flush()
  assert.equal(imageReads, 1, 'pending image loads are shared across tab switches')
  cleanup.forEach((callback) => callback())
  scope.stop()
  image.resolve({ ok: true, blob: async () => ({}) })
  await flush()
  assert.deepEqual(revoked, ['blob:test'], 'late image URLs are immediately released after leaving')
  assert.equal(Object.keys(preview.imageUrls.value).length, 0)
  await preview.loadWorkspaceFiles()
  assert.equal(fileReads, 3, 'disposed work never starts another file request')
  console.log(
    'PASS: artifact selection, lazy figures, forced refresh and late blob cleanup remain isolated',
  )

  const fileManager = await loadModule('src/components/FileManager.vue', { vue }, {}, true)
  const host = hostRenderer(),
    selected = []
  const app = host.renderer.createApp({
    render: () =>
      vue.h(fileManager.default, {
        loading: false,
        fillHeight: true,
        fileTreeData: [
          ...['paper.tex', 'paper.pdf', 'paper.docx', 'paper.md'].map((path) => ({
            path,
            name: path,
            type: 'file',
            category: 'papers',
            modified: 0,
          })),
          {
            path: 'attachment/paper.tex',
            name: 'paper.tex',
            type: 'file',
            category: 'attachments',
            modified: 0,
          },
        ],
        onFileSelect: (path) => selected.push(path),
      }),
  })
  app.component('t-tree', {
    props: ['data'],
    emits: ['click'],
    setup:
      (props, { emit }) =>
      () =>
        vue.h(
          'div',
          props.data.flatMap((group) =>
            group.children.map((node) =>
              vue.h(
                'button',
                {
                  onClick: () => emit('click', { node }),
                  'data-path': node.value,
                },
                node.label,
              ),
            ),
          ),
        ),
  })
  app.component('t-button', { render: () => null })
  app.component('t-icon', { render: () => null })
  app.component('t-loading', { render: () => null })
  app.mount(host.root)
  await flush()
  for (const [path, label] of [
    ['paper.tex', '论文'],
    ['paper.md', '论文'],
    ['paper.pdf', '论文（PDF）'],
    ['paper.docx', '论文（Word）'],
    ['attachment/paper.tex', 'paper.tex'],
  ]) {
    const node = host.find((n) => n.props?.['data-path'] === path)
    assert.equal(node.text, label)
    node.props.onClick()
    await flush()
    assert.equal(selected.at(-1), path, 'display labels must never replace real API paths')
  }
  app.unmount()
  console.log(
    'PASS: paper labels distinguish exports, preserve attachment names and open exact files',
  )

  const upload = deferred(),
    uploadCleanup = [],
    uploaded = []
  const uploadScope = vue.effectScope()
  const { useWorkAttachments } = await loadModule('src/composables/useWorkAttachments.ts', {
    ...imports,
    vue: { ...vue, onUnmounted: (callback) => uploadCleanup.push(callback) },
    '@/api/workspace': { attachmentAPI: { uploadAttachment: async () => upload.promise } },
  })
  const attachments = uploadScope.run(() =>
    useWorkAttachments({
      workId: vue.ref('one'),
      onUploaded: async (...file) => uploaded.push(file),
    }),
  )
  const input = { value: 'report.csv', files: [{ name: 'report.csv' }] }
  const request = attachments.handleAttachmentChange({ target: input })
  assert.equal(input.value, '', 'choosing the same file again remains possible')
  assert.equal(attachments.uploading.value, true)
  uploadCleanup.forEach((callback) => callback())
  uploadScope.stop()
  const before = notices.length
  upload.resolve({ attachment: { original_filename: 'report.csv', file_size: 2048 } })
  await request
  assert.equal(uploaded.length, 0, 'late uploads cannot append into another work conversation')
  assert.equal(notices.length, before, 'leaving suppresses upload completion notices')
  assert.equal(attachments.uploading.value, false)
  console.log('PASS: attachment inputs reset and late uploads cannot mutate a disposed work')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
