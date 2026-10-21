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
  let openedProgress=0
  const state = vue.reactive({
    onOpenProgress:()=>{openedProgress++},
    blocks: [
      { type: 'paper_analysis', content: '保留分析与假设' },
      { type: 'paper_solved', content: 'Q1 已通过验收，登记 5 项结果' },
      { type: 'main_agent_complete', content: '这是已交付的研究结果' },
    ],
    streaming: false,
    superseded: true,
    canConfirm: true,
    tasks: [{ task_id: 'Q1', objective: '建立需求预测模型' }],
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
  assert.match(
    text(record()),
    /保留分析与假设/,
    'raw analysis remains available inside technical details',
  )
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
  assert.equal(record().props.open, false, 'ordinary live records stay folded')
  const current=host.find(node=>node.props?.class==='pipeline-current')
  assert.match(text(current), /建立需求预测模型：正在核验/)
  assert.doesNotMatch(text(host.root), /本次操作进行中/,'Current progress comes from the concrete event')
  record().props.onToggle({ target: { open: true } })
  await flush()
  state.blocks = [...state.blocks, { type: 'paper_stage', content: 'Q1：更新计算' }]
  await flush()
  assert.equal(record().props.open, true, 'streaming updates retain manual details expansion')
  record().props.onToggle({ target: { open: false } })
  await flush()
  state.blocks = [...state.blocks, { type: 'paper_confirm', content: '请核对新的建模方案' }]
  state.streaming = false
  await flush()
  assert.equal(record().props.open, false, 'the pending decision is outside folded history')
  assert.match(text(host.root), /请核对新的建模方案/)
  state.superseded = true
  await flush()
  assert.equal(record().props.open, false, 'old confirmations collapse after the user replies')

  state.blocks = [
    ...state.blocks,
    { type: 'main_agent_error', content: '核验未通过，需要修改假设' },
  ]
  await flush()
  const error = host.find((node) => node.props?.class === 'pipeline-error')
  assert.equal(error.kind,'details')
  assert.match(text(error), /核验未通过，需要修改假设/)
  const openProgress=host.find(node=>node.kind==='button'&&text(node).trim()==='查看运行状态')
  assert.ok(openProgress)
  openProgress.props.onClick()
  assert.equal(openedProgress,1,'The current recovery action opens research progress')
  assert.equal(
    error.parent,
    record().parent,
    'error diagnostics stay outside the historical process and can be expanded independently',
  )
  state.blocks = [{
    type: 'paper_phase',
    content: { stage: 'solve', status: 'blocked', note: '下一步研究决策未完成，尚未开始实验计算。' },
  }]
  await flush()
  assert.match(text(record()), /下一步研究决策未完成，尚未开始实验计算/)
  assert.doesNotMatch(text(record()), /操作已记录|阶段状态已更新/)
  app.unmount()
  console.log(
    'PASS: historical research folds away while results, errors, live progress and pending decisions remain visible',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
