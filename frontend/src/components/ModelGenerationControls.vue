<template>
  <div class="connection-field generation-field">
    <span>{{ isZh ? '思考强度' : 'Reasoning effort' }}</span>
    <div class="generation-control">
      <div class="effort-control" :style="{ '--effort-steps': efforts.length }">
        <input
          type="range"
          min="0"
          :max="efforts.length - 1"
          step="1"
          :value="effortIndex"
          :disabled="disabled || loading || efforts.length === 1"
          :aria-label="isZh ? '思考强度' : 'Reasoning effort'"
          :aria-valuetext="effortLabel(efforts[effortIndex] || '')"
          @input="setEffort"
        />
        <div class="effort-labels" aria-hidden="true">
          <span
            v-for="(effort, index) in efforts"
            :key="effort"
            :class="{ selected: index === effortIndex }"
            >{{ effortLabel(effort) }}</span
          >
        </div>
      </div>
      <small v-if="efforts.length === 1">{{
        isZh ? '该模型不支持调整思考强度' : 'This model does not support reasoning control'
      }}</small>
    </div>
  </div>
  <div class="connection-field generation-field">
    <span>{{ isZh ? '上下文' : 'Context window' }}</span>
    <div class="generation-control">
      <div
        class="context-choices"
        role="radiogroup"
        :aria-label="isZh ? '上下文' : 'Context window'"
      >
        <label v-for="option in contexts" :key="option.value" class="context-option">
          <input
            type="radio"
            :name="contextId"
            :value="option.value"
            :checked="modelValue?.context_window === option.value"
            :disabled="disabled || loading || (!!maximum && option.value > maximum)"
            @change="setContext"
          />
          <span>{{ option.label }}</span>
        </label>
      </div>
      <small v-if="maximum"
        >{{ isZh ? '模型标称上限' : 'Model capacity' }} {{ formatContext(maximum) }}</small
      >
      <small v-else-if="!loading">{{
        isZh
          ? '上限未识别，请确认接口支持所选容量。'
          : 'Capacity unknown; confirm your endpoint supports this size.'
      }}</small>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { modelConfigAPI, type GenerationSettings, type ModelCapabilities } from '@/api/modelConfig'
import { isDeepSeekEndpoint } from '@/utils/modelProvider'
const props = defineProps<{
  modelId: string
  baseUrl: string
  modelValue?: GenerationSettings
  thinkingEnabled?: boolean
  disabled?: boolean
  isZh: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: GenerationSettings]
  'update:thinkingEnabled': [value: boolean]
}>()
const contextId = useId()
const capabilities = ref<ModelCapabilities | null>(null)
const loading = ref(true)
const maximum = computed(() => capabilities.value?.max_context_tokens)
const formatContext = (value: number) =>
  value >= 1000000 ? `${value / 1000000}M` : `${value / 1000}K`
const contexts = computed(() => {
  const values = new Set([256000, 512000, 1000000])
  if (maximum.value && maximum.value < 1000000) values.add(maximum.value)
  if (props.modelValue?.context_window) values.add(props.modelValue.context_window)
  return [...values].sort((a, b) => a - b).map((value) => ({ value, label: formatContext(value) }))
})
const efforts = computed(() => {
  const available = capabilities.value?.reasoning_efforts ?? ['low', 'medium', 'high']
  const values =
    available.length === 0
      ? ['off']
      : [...(isDeepSeekEndpoint(props.baseUrl) ? ['off'] : []), '', ...available]
  const current = props.modelValue?.reasoning_effort
  if (current && !values.includes(current)) values.push(current)
  return values
})
const effortIndex = computed(() =>
  isDeepSeekEndpoint(props.baseUrl) && !props.thinkingEnabled
    ? 0
    : Math.max(0, efforts.value.indexOf(props.modelValue?.reasoning_effort || '')),
)
function effortLabel(value: string) {
  const labels: Record<string, string[]> = {
    off: ['关', 'Off'],
    '': ['自动', 'Auto'],
    minimal: ['最低', 'Minimal'],
    low: ['低', 'Low'],
    medium: ['中', 'Medium'],
    high: ['高', 'High'],
    xhigh: ['很高', 'Extra high'],
    max: ['最高', 'Max'],
  }
  return labels[value]?.[props.isZh ? 0 : 1] || value
}
function setEffort(event: Event) {
  const value = efforts.value[Number((event.target as HTMLInputElement).value)] || null
  if (isDeepSeekEndpoint(props.baseUrl)) emit('update:thinkingEnabled', value !== 'off')
  emit('update:modelValue', {
    ...props.modelValue,
    reasoning_effort: value === 'off' ? null : value,
  })
}
function setContext(event: Event) {
  const value = Number((event.target as HTMLSelectElement).value)
  if (maximum.value && value > maximum.value) return
  emit('update:modelValue', { ...props.modelValue, context_window: value })
}
watch(
  () => [props.modelId, props.baseUrl],
  ([modelId], _, onCleanup) => {
    let active = true
    loading.value = true
    capabilities.value = null
    const timer = setTimeout(async () => {
      if (!modelId?.trim()) {
        loading.value = false
        return
      }
      try {
        const result = await modelConfigAPI.capabilities(modelId.trim())
        if (!active) return
        capabilities.value = result
      } catch {
        /* Keep unknown capacity explicit; no model request is made. */
      } finally {
        if (active) loading.value = false
      }
    }, 250)
    onCleanup(() => {
      active = false
      clearTimeout(timer)
    })
  },
  { immediate: true },
)
watch(
  () => [
    loading.value,
    maximum.value,
    props.modelValue?.context_window,
    props.modelValue?.reasoning_effort,
  ],
  () => {
    if (loading.value || !props.modelId.trim()) return
    const current = props.modelValue?.context_window
    const selected = current ? Math.min(current, maximum.value || current) : maximum.value || 256000
    const noReasoning = capabilities.value?.reasoning_efforts.length === 0
    if (selected !== current || (noReasoning && props.modelValue?.reasoning_effort))
      emit('update:modelValue', {
        ...props.modelValue,
        context_window: selected,
        ...(noReasoning ? { reasoning_effort: null } : {}),
      })
  },
  { immediate: true },
)
</script>
<style scoped>
.generation-field.connection-field {
  align-items: start;
}
.generation-field > span {
  padding-top: 11px;
  line-height: 20px;
}
.generation-control {
  min-width: 0;
}
.context-choices,
.effort-control {
  position: relative;
  display: flex;
  height: 44px;
  border: 1px solid var(--pa-line-strong);
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface-muted);
}
.context-choices {
  padding: 4px;
  gap: 4px;
}
.context-option {
  position: relative;
  flex: 1;
  min-width: 0;
}
.context-option input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  margin: 0;
  cursor: pointer;
}
.context-option span {
  display: grid;
  place-items: center;
  height: 100%;
  border-radius: 4px;
  color: var(--pa-muted);
  font-size: 13px;
  font-weight: 500;
  border: 1px solid transparent;
}
.context-option input:checked + span {
  background: var(--pa-surface);
  color: var(--pa-accent);
  border-color: var(--pa-line-strong);
  box-shadow: 0 1px 2px rgb(0 0 0 / 4%);
}
.context-option input:disabled + span {
  opacity: 0.35;
}
.context-option input:focus-visible + span {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
.context-option input:disabled {
  cursor: default;
}
.effort-control input[type='range'] {
  appearance: none;
  width: 100%;
  min-width: 0;
  height: 100%;
  margin: 0;
  padding: 4px;
  background: transparent;
  cursor: ew-resize;
  border-radius: inherit;
}
.effort-control input::-webkit-slider-runnable-track {
  height: 34px;
  background: transparent;
}
.effort-control input::-webkit-slider-thumb {
  appearance: none;
  width: calc(100% / var(--effort-steps));
  height: 34px;
  border-radius: 4px;
  border: 1px solid var(--pa-line-strong);
  background: var(--pa-surface);
  box-shadow: 0 1px 2px rgb(0 0 0 / 4%);
}
.effort-control input::-moz-range-track {
  height: 34px;
  background: transparent;
}
.effort-control input::-moz-range-thumb {
  width: calc(100% / var(--effort-steps));
  height: 34px;
  border-radius: 4px;
  border: 1px solid var(--pa-line-strong);
  background: var(--pa-surface);
}
input:disabled {
  cursor: default;
  opacity: 0.5;
}
.effort-labels {
  position: absolute;
  inset: 4px;
  display: grid;
  grid-template-columns: repeat(var(--effort-steps), 1fr);
  align-items: center;
  text-align: center;
  pointer-events: none;
  color: var(--pa-muted);
  font-size: 13px;
}
.effort-labels .selected {
  color: var(--pa-accent);
  font-weight: 500;
}
small {
  display: block;
  margin-top: 6px;
  color: var(--pa-muted);
  font-size: 13px;
}
input:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 3px;
}
</style>
