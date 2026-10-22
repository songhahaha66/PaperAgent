const assert = require('node:assert/strict')
const { loadModule, vue, pinia, flush } = require('./moduleHarness.cjs')

// A Vue host renderer mounts the real Teleport layout without a browser.
const { hostRenderer } = require('./vueHost.cjs')

;(async () => {
  const auth = vue.reactive({ token: 'test', currentUser: { id: 7 } })
  const storeModule = await loadModule('src/stores/workPanels.ts', {
    vue,
    pinia,
    '@/stores/auth': { useAuthStore: () => auth },
  })
  const store = storeModule.useWorkPanelsStore(pinia.createPinia())
  const state = store.session('one')
  store.open('one', 'paper')
  const bars = {}
  const Bar = {
    props: ['idPrefix'],
    setup(props, { emit }) {
      bars[props.idPrefix] = emit
      return () => null
    },
  }
  const layoutModule = await loadModule(
    'src/components/WorkPaneLayout.vue',
    {
      vue,
      '@/components/WorkTabBar.vue': { default: Bar },
      '@/lang/studio': { useStudioCopy: () => ({ isZh: vue.ref(true) }) },
    },
    {},
    true,
  )
  const host = hostRenderer(),
    mounted = {},
    removed = {}
  const Content = {
    props: ['panel'],
    setup(props) {
      vue.onMounted(() => {
        mounted[props.panel.id] = (mounted[props.panel.id] || 0) + 1
      })
      vue.onUnmounted(() => {
        removed[props.panel.id] = (removed[props.panel.id] || 0) + 1
      })
      return () => vue.h('article', { 'data-panel': props.panel.id }, props.panel.id)
    },
  }
  const narrow = vue.ref(false)
  const Shell = {
    setup() {
      return () =>
        vue.h(
          layoutModule.default,
          {
            session: state,
            panels: state.panels.map((panel) => ({ ...panel, label: panel.id, icon: 'file-1' })),
            options: [],
            stacked: narrow.value,
            onMove: (id, group, before) => store.move('one', id, group, before),
            onSplit: (id, edge) => store.split('one', id, edge),
            onRatio: (ratio) => {
              state.splitRatio = ratio
            },
          },
          { default: ({ panel }) => vue.h(Content, { panel }) },
        )
    },
  }
  const app = host.renderer.createApp(Shell)
  app.component('t-icon', { render: () => null })
  app.mount(host.root)
  await flush()
  const paper = host.find((node) => node.props?.['data-panel'] === 'paper')
  assert.ok(paper)
  assert.equal(mounted.paper, 1)
  for (const edge of ['right', 'top', 'left', 'bottom']) {
    const surface = host.find((node) => node.props?.class?.includes('work-pane-layout'))
    surface.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 800 })
    Object.values(bars)[0]('drag', 'paper')
    await flush()
    const points = { right: [995, 400], left: [5, 400], top: [500, 5], bottom: [500, 795] }
    surface.props.onDragover({
      clientX: points[edge][0],
      clientY: points[edge][1],
      preventDefault() {},
    })
    await flush()
    assert.ok(
      host.find((node) => node.props?.class?.includes(`split-drop-preview ${edge}`)),
      `dragging to ${edge} shows a drop preview`,
    )
    Object.values(bars)[0]('drag-over')
    await flush()
    assert.ok(!host.find((node) => node.props?.class?.includes('split-drop-preview')), 'entering a tab bar clears the split preview')
    surface.props.onDrop({
      clientX: points[edge][0], clientY: points[edge][1],
      dataTransfer: { getData: () => 'paper' }, preventDefault() {},
    })
    await flush()
    assert.strictEqual(
      host.find((node) => node.props?.['data-panel'] === 'paper'),
      paper,
      'moving panes must move existing DOM',
    )
    assert.equal(mounted.paper, 1, 'splitting must not remount or refetch a document')
    assert.equal(removed.paper, undefined)
  }
  assert.ok(
    host.find((node) => node.props?.class?.includes('stacked')),
    'top/bottom drops stack the panes',
  )
  store.move('one', 'paper', 'main')
  await flush()
  assert.strictEqual(
    host.find((node) => node.props?.['data-panel'] === 'paper'),
    paper,
  )
  assert.equal(state.secondaryActiveId, null)
  console.log('PASS: real mounted tab contents survive splits and merges without remounting')
  store.split('one', 'paper', 'right')
  await flush()
  const separator = host.find((node) => node.props?.role === 'separator')
  separator.props.onKeydown({ key: 'ArrowRight', preventDefault() {} })
  await flush()
  assert.equal(state.splitRatio, 0.55)
  separator.setPointerCapture = () => {}
  separator.hasPointerCapture = () => true
  separator.releasePointerCapture = () => {}
  separator.props.onPointerdown({
    button: 0,
    pointerId: 1,
    currentTarget: separator,
    preventDefault() {},
  })
  separator.props.onPointermove({ clientX: 700, clientY: 400 })
  await flush()
  assert.equal(state.splitRatio, 0.7)
  separator.props.onPointerup({ pointerId: 1, currentTarget: separator })
  store.split('one', 'paper', 'top')
  await flush()
  const horizontal = host.find((node) => node.props?.role === 'separator')
  horizontal.setPointerCapture = () => {}
  horizontal.props.onPointerdown({
    button: 0,
    pointerId: 2,
    currentTarget: horizontal,
    preventDefault() {},
  })
  horizontal.props.onPointermove({ clientX: 999, clientY: 240 })
  await flush()
  assert.equal(state.splitRatio, 0.3, 'stacked panes resize along the vertical axis')
  console.log('PASS: pointer and keyboard resizing retain the split ratio on either axis')
  state.splitAxis = 'horizontal'
  narrow.value = true
  await flush()
  const countPanes = (node) =>
    (node.props?.class === 'workspace-pane' ? 1 : 0) +
    (node.children || []).reduce((sum, child) => sum + countPanes(child), 0)
  assert.equal(countPanes(host.root), 1, 'narrow screens use one tab group')
  assert.ok(
    !host.find((node) => node.props?.class?.includes('work-pane-layout stacked')),
    'narrow width never forces vertical splitting',
  )
  assert.strictEqual(
    host.find((node) => node.props?.['data-panel'] === 'paper'),
    paper,
  )
  assert.equal(mounted.paper, 1, 'resizing preserves the current document')
  narrow.value = false
  await flush()
  assert.equal(countPanes(host.root), 2, 'desktop groups return on widening')
  assert.equal(mounted.paper, 1)
  app.unmount()
  assert.equal(removed.paper, 1)
  console.log('PASS: leaving the work disposes each moved document exactly once')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
