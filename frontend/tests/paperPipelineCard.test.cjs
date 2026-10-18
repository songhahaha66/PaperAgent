const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')

;(async () => {
  const module = await loadModule(
    'src/components/PaperPipelineCard.vue',
    {
      vue,
      '@/components/MarkdownRenderer.vue': {
        default: {
          props: ['content'],
          setup: (props) => () => vue.h('div', { class: 'markdown' }, props.content),
        },
      },
    },
    {},
    true,
  )
  const state = vue.reactive({
    blocks: [
      { type: 'paper_analysis', content: '保留分析与假设' },
      { type: 'paper_solved', content: 'Q1 已通过验收，登记 5 项结果' },
      { type: 'main_agent_complete', content: '这是已交付的研究结果' },
    ],
    streaming: false,
    superseded: true,
  })
  const host = hostRenderer()
  const app = host.renderer.createApp({ setup: () => () => vue.h(module.default, state) })
  app.component('t-icon', { render: () => null })
  app.component('t-loading', { render: () => null })
  app.component('t-alert', {
    props: ['message'],
    setup:
      (props, { slots }) =>
      () =>
        vue.h('aside', {}, [props.message, slots.message?.()]),
  })
  app.mount(host.root)
  await flush()
  const text = (node) => [node.text || '', ...(node.children || []).map(text)].join('')
  const record = () => host.find((node) => node.props?.class === 'pipeline-record')
  assert.equal(record().props.open, false, 'historical process starts collapsed')
  assert.match(text(record()), /保留分析与假设/)
  const result = host.find((node) => node.props?.class === 'pipeline-section pipeline-result')
  assert.match(text(result), /这是已交付的研究结果/)
  assert.equal(
    result.parent,
    record().parent,
    'the delivered answer stays outside the collapsed process',
  )

  state.superseded = false
  state.streaming = true
  state.blocks = [
    { type: 'paper_analysis', content: '新一轮研究' },
    { type: 'paper_stage', content: 'Q1：正在核验' },
  ]
  await flush()
  assert.equal(record().props.open, true, 'live progress stays expanded')
  assert.match(text(record()), /正在研究.*Q1：正在核验/)
  state.blocks = [...state.blocks, { type: 'paper_confirm', content: '请核对新的建模方案' }]
  state.streaming = false
  await flush()
  assert.equal(record().props.open, true, 'a pending decision is never hidden')
  assert.match(text(record()), /待确认方案.*请核对新的建模方案/)
  state.superseded = true
  await flush()
  assert.equal(record().props.open, false, 'old confirmations collapse after the user replies')

  state.blocks = [
    ...state.blocks,
    { type: 'main_agent_error', content: '核验未通过，需要修改假设' },
  ]
  await flush()
  const error = host.find((node) => node.props?.class === 'pipeline-error')
  assert.match(text(error), /核验未通过，需要修改假设/)
  assert.equal(
    error.parent,
    record().parent,
    'errors remain visible without expanding technical history',
  )
  app.unmount()
  console.log(
    'PASS: historical research folds away while results, errors, live progress and pending decisions remain visible',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
