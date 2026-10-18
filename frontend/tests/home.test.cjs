const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
async function fixture() {
  const history = vue.reactive({
    works: [],
    loading: true,
    hasLoaded: false,
    loadError: false,
    ensureLoaded: async () => {},
    upsert() {},
  })
  const routes = [],
    panels = [],
    mutations = []
  const module = await loadModule(
    'src/views/Home.vue',
    {
      vue,
      'vue-router': { useRouter: () => ({ push: (path) => routes.push(path) }) },
      'tdesign-vue-next': { MessagePlugin: { warning() {} } },
      '@/api/template': { templateAPI: {} },
      '@/api/workspace': {
        workspaceAPI: { createWork: () => mutations.push('create') },
        attachmentAPI: {},
      },
      '@/api/modelConfig': {
        modelConfigAPI: {
          getAllModelConfigs: async () =>
            ['brain', 'code', 'writing'].map((type) => ({ type, is_active: true })),
        },
      },
      '@/stores/auth': { useAuthStore: () => ({ token: 'test' }) },
      '@/stores/workHistory': { useWorkHistoryStore: () => history },
      '@/stores/workPanels': {
        useWorkPanelsStore: () => ({ open: (...args) => panels.push(args) }),
      },
      '@/composables/useBreakpoint': { useBreakpoint: () => ({ isMobile: vue.ref(false) }) },
      '@/lang/studio': {
        useStudioCopy: () => ({
          isZh: vue.ref(true),
          ui: vue.ref({ recent: '最近的研究', untitled: '未命名研究' }),
        }),
      },
    },
    {},
    true,
  )
  const host = hostRenderer(),
    app = host.renderer.createApp(module.default)
  app.component('t-button', {
    setup:
      (_, { slots }) =>
      () =>
        vue.h('button', {}, slots.default?.()),
  })
  app.component('t-tooltip', {
    props: ['content'],
    setup:
      (_, { slots }) =>
      () =>
        slots.default?.(),
  })
  app.component('t-textarea', {
    props: ['modelValue'],
    setup: (props, { expose }) => {
      expose({ focus() {} })
      return () => vue.h('textarea', { value: props.modelValue })
    },
  })
  for (const name of ['t-icon', 't-select', 't-option', 't-alert'])
    app.component(name, { render: () => null })
  app.component('router-link', {
    setup:
      (_, { slots }) =>
      () =>
        vue.h('a', {}, slots.default?.()),
  })
  app.mount(host.root)
  await flush()
  return { host, app, history, routes, panels, mutations }
}
;(async () => {
  const f = await fixture()
  assert.match(text(f.host.root), /正在读取最近研究/)
  assert.equal(
    f.host.find((n) => n.props?.class === 'suggestions'),
    undefined,
  )
  f.history.loading = false
  f.history.hasLoaded = true
  f.history.loadError = true
  await flush()
  assert.match(text(f.host.root), /暂时无法读取最近研究/)
  assert.equal(
    f.host.find((n) => n.props?.class === 'suggestions'),
    undefined,
    'an error is not a first visit',
  )
  f.history.loadError = false
  await flush()
  const examples = f.host.find((n) => n.props?.class === 'suggestions')
  assert.equal(examples.children.filter((n) => n.kind === 'button').length, 2)
  assert.match(text(examples), /20 天食堂销量 → 需求预测、图表与分析论文/)
  examples.children.find((n) => n.kind === 'button').props.onClick()
  await flush()
  assert.match(f.host.find((n) => n.kind === 'textarea').props.value, /420、435/)
  assert.equal(f.mutations.length, 0, 'choosing an example only fills the input')
  f.history.works = [
    { work_id: 'old', title: '旧研究', status: 'completed', updated_at: '2026-10-01T00:00:00Z' },
    { work_id: 'missing', title: '未知状态', status: 'unexpected' },
    {
      work_id: 'active',
      title: '进行中研究',
      status: 'in_progress',
      updated_at: '2026-10-18T08:00:00Z',
    },
    {
      work_id: 'waiting',
      title: '待确认方案',
      status: 'waiting',
      updated_at: '2026-10-17T08:00:00Z',
    },
    { work_id: 'paused', title: '中断研究', status: 'paused', updated_at: '2026-10-16T08:00:00Z' },
    {
      work_id: 'done',
      title: '已完成研究',
      status: 'completed',
      updated_at: '2026-10-15T08:00:00Z',
    },
  ]
  await flush()
  assert.equal(
    f.host.find((n) => n.props?.class === 'suggestions'),
    undefined,
  )
  let rows = f.host.find((n) => n.kind === 'ul').children.filter((n) => n.kind === 'li')
  assert.equal(rows.length, 5)
  assert.match(text(rows[0]), /进行中研究进行中更新于/)
  assert.match(text(rows[1]), /待确认方案需要确认/)
  assert.match(text(rows[2]), /中断研究中断/)
  const findButton = (node, label) => {
    if (node.kind === 'button' && text(node) === label) return node
    return (node.children || []).map((n) => findButton(n, label)).find(Boolean)
  }
  findButton(rows[0], '继续研究').props.onClick()
  findButton(rows[3], '查看论文').props.onClick()
  assert.deepEqual(f.routes, ['/work/active', '/work/done'])
  assert.deepEqual(
    f.panels.map((p) => p.slice(0, 2)),
    [
      ['active', 'chat'],
      ['done', 'paper'],
    ],
  )
  assert.equal(f.mutations.length, 0, 'opening recent work never starts or resumes execution')
  f.history.works = [
    { work_id: 'unknown', title: '无时间记录', status: 'unexpected', updated_at: 'invalid' },
  ]
  await flush()
  assert.equal(
    f.host.find((n) => n.kind === 'time'),
    undefined,
  )
  assert.doesNotMatch(text(f.host.root), /unexpected|Invalid Date|更新于/)
  f.app.unmount()
  console.log(
    'PASS: home separates first visit, loading, errors and recent work; real metadata drives order and actions',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
