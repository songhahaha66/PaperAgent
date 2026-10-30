const assert = require('node:assert/strict')
const test = require('node:test')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { hostRenderer } = require('./vueHost.cjs')
const text = (n) =>
  n.kind === 'comment' ? '' : [n.text || '', ...(n.children || []).map(text)].join('')
const waitCapacity = async () => {
  await new Promise((r) => setTimeout(r, 280))
  await flush()
}
const deepseek = {
  max_context_tokens: 1000000,
  reasoning_efforts: ['low', 'high', 'max'],
  source: 'provider',
}

async function mount(t, lookup = async () => deepseek, settings = {}) {
  const module = await loadModule(
    'src/components/ModelGenerationControls.vue',
    {
      vue,
      '@/api/modelConfig': { modelConfigAPI: { capabilities: lookup } },
    },
    { URL, setTimeout, clearTimeout },
    true,
  )
  const state = vue.reactive({
    modelId: 'deepseek-flash',
    baseUrl: 'https://api.deepseek.com/v1',
    modelValue: settings,
    thinkingEnabled: true,
    isZh: true,
  })
  const host = hostRenderer()
  const app = host.renderer.createApp({
    render: () =>
      vue.h(module.default, {
        ...state,
        'onUpdate:thinkingEnabled': (value) => {
          state.thinkingEnabled = value
        },
        'onUpdate:modelValue': (value) => {
          state.modelValue = value
        },
      }),
  })
  app.mount(host.root)
  t.after(() => app.unmount())
  await waitCapacity()
  return {
    state,
    host,
    slider: () => host.find((n) => n.kind === 'input' && n.props.type === 'range'),
    context: (value) =>
      host.find((n) => n.kind === 'input' && n.props.type === 'radio' && n.props.value === value),
  }
}

test('slider and context choices preserve hidden generation settings', async (t) => {
  const { state, host, slider, context } = await mount(t, undefined, { max_output_tokens: 24000 })
  assert.equal(state.modelValue.context_window, 1000000)
  assert.match(text(host.root), /256K512K1M/)
  assert.doesNotMatch(text(host.root), /高级|单次生成上限/)
  assert.equal(slider().props.max, 4)
  slider().props.onInput({ target: { value: '2' } })
  await flush()
  assert.equal(state.modelValue.reasoning_effort, 'low')
  context(512000).props.onChange({ target: { value: '512000' } })
  await flush()
  assert.equal(state.modelValue.context_window, 512000)
  assert.equal(state.modelValue.max_output_tokens, 24000)
  slider().props.onInput({ target: { value: '0' } })
  await flush()
  assert.equal(state.thinkingEnabled, false)
  assert.equal(state.modelValue.reasoning_effort, null)
  assert.equal(slider().props.disabled, false)
  slider().props.onInput({ target: { value: '3' } })
  await flush()
  assert.equal(state.thinkingEnabled, true)
  assert.equal(state.modelValue.reasoning_effort, 'high')
  state.modelValue = {}
  await flush()
  assert.equal(
    state.modelValue.context_window,
    1000000,
    'late saved-config hydration receives the same default',
  )
})

test('known smaller model clamps context and rejects an oversized selection', async (t) => {
  const lookup = async (model) =>
    model === 'deepseek-flash'
      ? deepseek
      : { max_context_tokens: 128000, reasoning_efforts: [], source: 'sdk' }
  const { state, host, slider, context } = await mount(t, lookup, {
    context_window: 1000000,
    reasoning_effort: 'high',
  })
  state.modelId = 'gpt-4o'
  state.baseUrl = 'https://api.openai.com/v1'
  await flush()
  await waitCapacity()
  assert.equal(state.modelValue.context_window, 128000)
  assert.equal(state.modelValue.reasoning_effort, null)
  assert.equal(slider().props.disabled, true)
  assert.match(text(host.root), /128K/)
  const big = context(256000)
  assert.equal(big.props.disabled, true)
  context(256000).props.onChange({ target: { value: '256000' } })
  await flush()
  assert.equal(state.modelValue.context_window, 128000)
})

test('unknown capacity is explicit and older configured sizes remain available', async (t) => {
  const { state, host, context } = await mount(
    t,
    async () => ({
      max_context_tokens: null,
      reasoning_efforts: ['low', 'medium', 'high'],
      source: 'unknown',
    }),
    { context_window: 64000 },
  )
  assert.equal(state.modelValue.context_window, 64000)
  assert.match(text(host.root), /64K/)
  assert.match(text(host.root), /上限未识别/)
  context(256000).props.onChange({ target: { value: '256000' } })
  await flush()
  assert.equal(state.modelValue.context_window, 256000)
})

test('late capacity response cannot overwrite a newly selected model', async (t) => {
  const pending = []
  const { state, context } = await mount(t, () => new Promise((resolve) => pending.push(resolve)))
  state.modelId = 'smaller-model'
  await flush()
  await waitCapacity()
  pending[1]({ max_context_tokens: 128000, reasoning_efforts: [], source: 'sdk' })
  await flush()
  pending[0](deepseek)
  await flush()
  assert.equal(state.modelValue.context_window, 128000)
  assert.equal(context(128000).props.disabled, false)
})
