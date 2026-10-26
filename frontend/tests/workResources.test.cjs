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
  const images = []
  let fileReads = 0,
    imageReads = 0
  let files = [
    { path: 'paper.pdf', name: 'paper.pdf', type: 'file', size: 100 },
    { path: 'paper.md', name: 'paper.md', type: 'file', size: 80 },
    { path: 'figures/chart.png', name: 'chart.png', type: 'file', size: 50, modified: 1 },
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
          return [...files]
        },
        getImageUrl: (_token, _work, path) => path,
      },
    },
  }
  const { useArtifactPreview } = await loadModule(
    'src/composables/useArtifactPreview.ts',
    imports,
    {
      fetch: async (_url, options) => {
        imageReads++
        const image = deferred()
        images.push({ ...image, options })
        return image.promise
      },
      URL: { createObjectURL: (blob) => `blob:${blob.version}`, revokeObjectURL: (url) => revoked.push(url) },
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
  const finishImage = async (index, version) => {
    images[index].resolve({ ok: true, blob: async () => ({ version }) })
    await flush()
  }
  await finishImage(0, 1)
  assert.equal(preview.imageUrls.value['figures/chart.png'], 'blob:1')

  figuresVisible.value = false
  await flush()
  files = files.map(file => file.path === 'figures/chart.png' ? { ...file, modified: 2 } : file)
  await preview.loadWorkspaceFiles()
  await flush()
  assert.equal(imageReads, 1, 'hidden panes invalidate replaced figures without loading them')
  assert.equal(preview.imageUrls.value['figures/chart.png'], undefined)
  assert.deepEqual(revoked, ['blob:1'])
  figuresVisible.value = true
  await flush()
  assert.equal(imageReads, 2, 'reopening loads the updated version at the same path')

  files = files.map(file => file.path === 'figures/chart.png' ? { ...file, modified: 3 } : file)
  await preview.loadWorkspaceFiles()
  await flush()
  assert.equal(imageReads, 3)
  await finishImage(2, 3)
  await finishImage(1, 2)
  assert.equal(preview.imageUrls.value['figures/chart.png'], 'blob:3', 'late older responses cannot replace the current image')
  assert.deepEqual(revoked, ['blob:1', 'blob:2'])
  assert.ok(images.every(image => image.options.cache === 'no-store'), 'same-path refreshes bypass the browser cache')

  files = files.filter(file => file.path !== 'figures/chart.png')
  await preview.loadWorkspaceFiles()
  await flush()
  assert.equal(Object.keys(preview.imageUrls.value).length, 0)
  assert.deepEqual(revoked, ['blob:1', 'blob:2', 'blob:3'], 'deleted figures release their blobs')
  files.push({ path: 'figures/chart.png', name: 'chart.png', type: 'file', size: 50, modified: 4 })
  await preview.loadWorkspaceFiles()
  await flush()
  const readsBeforeLeaving = fileReads
  cleanup.forEach((callback) => callback())
  scope.stop()
  await finishImage(3, 4)
  assert.deepEqual(revoked, ['blob:1', 'blob:2', 'blob:3', 'blob:4'], 'late image URLs are immediately released after leaving')
  assert.equal(Object.keys(preview.imageUrls.value).length, 0)
  await preview.loadWorkspaceFiles()
  assert.equal(fileReads, readsBeforeLeaving, 'disposed work never starts another file request')
  console.log(
    'PASS: artifact selection and lazy figures refresh replaced files, reject stale responses and release blobs',
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
          ...[
            ['data/input.csv', 'data'],
            ['template/original.docx', 'template'],
            ['future/result.txt', 'future'],
          ].map(([path, category]) => ({ path, name: path.split('/').pop(), type: 'file', category, modified: 0 })),
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
                  'data-category': group.value,
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
  for (const [path, category] of [
    ['data/input.csv', 'data'],
    ['template/original.docx', 'template'],
    ['future/result.txt', 'outputs'],
  ]) {
    const node = host.find((n) => n.props?.['data-path'] === path)
    assert.ok(node, `${path} must remain visible`)
    assert.equal(node.props['data-category'], category)
    node.props.onClick()
    await flush()
    assert.equal(selected.at(-1), path)
  }
  app.unmount()
  console.log(
    'PASS: file categories retain data, templates and unknown categories with exact selectable paths',
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
