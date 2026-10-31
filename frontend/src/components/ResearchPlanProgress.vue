<template>
  <details
    v-if="plan?.steps.length"
    ref="details"
    class="research-plan-progress"
    @keydown.esc="close"
  >
    <summary>
      <span class="plan-line" aria-live="polite">
        <strong>{{ isZh ? '研究进度' : 'Research progress' }}</strong>
        <span class="plan-count">{{
          isZh
            ? `已完成 ${completed} / ${plan.steps.length} 项`
            : `${completed} / ${plan.steps.length} completed`
        }}</span>
        <svg class="plan-chevron" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path d="m4 6 4 4 4-4" />
        </svg>
      </span>
      <span class="plan-rail" aria-hidden="true">
        <span v-for="step in plan.steps" :key="step.title" :class="step.status" />
      </span>
      <span class="plan-current">
        <template v-if="current"
          ><span>{{ statusLabel(current.status) }}</span
          >{{ current.title }}</template
        >
        <template v-else>{{ isZh ? '全部完成' : 'All completed' }}</template>
      </span>
    </summary>
    <section class="plan-panel" :aria-label="isZh ? '研究计划' : 'Research plan'">
      <ol>
        <li v-for="step in plan.steps" :key="step.title" :class="step.status">
          <span class="plan-marker" aria-hidden="true">{{
            step.status === 'completed' ? '✓' : ''
          }}</span>
          <div class="plan-step">
            <span>{{ step.title }}</span>
            <p v-if="step.note">{{ step.note }}</p>
          </div>
          <span class="plan-status">{{ statusLabel(step.status) }}</span>
        </li>
      </ol>
      <div class="plan-updated">{{ isZh ? '更新于' : 'Updated at' }} {{ updatedTime }}</div>
    </section>
  </details>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { ResearchPlan } from '@/types/research'

const props = defineProps<{ plan?: ResearchPlan | null; isZh: boolean; workStatus?: string }>()
const details = ref<HTMLDetailsElement>()
const completed = computed(
  () => props.plan?.steps.filter((step) => step.status === 'completed').length || 0,
)
const current = computed(
  () =>
    props.plan?.steps.find((step) => step.status === 'in_progress') ||
    props.plan?.steps.find((step) => step.status === 'blocked') ||
    props.plan?.steps.find((step) => step.status === 'pending'),
)
const updatedTime = computed(() =>
  props.plan
    ? new Date(props.plan.updated_at * 1000).toLocaleTimeString(props.isZh ? 'zh-CN' : 'en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '',
)
function statusLabel(status: ResearchPlan['steps'][number]['status']) {
  if (
    status === 'in_progress' &&
    ['paused', 'cancelled', 'failed'].includes(props.workStatus || '')
  )
    return props.isZh ? '已暂停' : 'Paused'
  const labels = {
    pending: ['待开始', 'Pending'],
    in_progress: ['进行中', 'In progress'],
    completed: ['已完成', 'Completed'],
    blocked: ['待继续', 'Waiting'],
  }
  return labels[status][props.isZh ? 0 : 1]
}
function close() {
  if (details.value) details.value.open = false
}
function onOutside(event: PointerEvent) {
  if (event.target instanceof Node && !details.value?.contains(event.target)) close()
}
onMounted(() => document.addEventListener('pointerdown', onOutside))
onUnmounted(() => document.removeEventListener('pointerdown', onOutside))
</script>

<style scoped>
.research-plan-progress {
  position: relative;
  flex: 1 1 290px;
  min-width: 190px;
  max-width: 400px;
}
summary {
  display: block;
  cursor: pointer;
  border-radius: var(--pa-radius-control);
  padding: 8px 10px;
  list-style: none;
}
summary::-webkit-details-marker {
  display: none;
}
summary:hover,
details[open] summary {
  background: var(--pa-surface-hover);
}
summary:focus-visible {
  outline: 2px solid var(--td-brand-color);
  outline-offset: 2px;
}
.plan-line {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  line-height: 1.5;
  white-space: nowrap;
}
.plan-line strong {
  font-size: 13px;
  font-weight: 600;
}
.plan-count {
  color: var(--pa-muted);
  font-variant-numeric: tabular-nums;
}
.plan-chevron {
  margin-left: auto;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  flex-shrink: 0;
}
details[open] .plan-chevron {
  transform: rotate(180deg);
}
.plan-rail {
  display: flex;
  gap: 4px;
  margin: 7px 0 6px;
}
.plan-rail > span {
  flex: 1;
  height: 4px;
  min-width: 0;
  border-radius: 4px;
  background: var(--pa-line);
}
.plan-rail > .completed {
  background: var(--td-brand-color);
}
.plan-rail > .in_progress {
  background: var(--td-brand-color-light);
  box-shadow: inset 0 0 0 1px var(--td-brand-color);
}
.plan-rail > .blocked {
  background: var(--pa-warning);
}
.plan-current {
  display: block;
  font-size: 12px;
  color: var(--pa-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.plan-current > span {
  color: var(--td-brand-color);
  margin-right: 7px;
}
.plan-panel {
  position: absolute;
  right: 0;
  top: calc(100% + 10px);
  z-index: 50;
  box-sizing: border-box;
  width: min(430px, calc(100vw - 32px));
  border: 1px solid var(--pa-line);
  border-radius: 14px;
  background: var(--pa-surface);
  box-shadow: 0 12px 40px #152d2220;
  padding: 6px 18px 12px;
}
ol {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: min(55vh, 400px);
  overflow-y: auto;
}
li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 15px 0;
  border-bottom: 1px solid var(--pa-line);
  font-size: 13px;
  line-height: 1.6;
}
.plan-marker {
  box-sizing: border-box;
  flex-shrink: 0;
  width: 15px;
  height: 15px;
  border: 2px solid var(--pa-line-strong);
  border-radius: 50%;
  margin-top: 3px;
}
.completed .plan-marker {
  color: var(--td-brand-color);
  border: 0;
  font-size: 17px;
  line-height: 15px;
}
.in_progress .plan-marker {
  border-color: var(--td-brand-color);
  background: var(--td-brand-color-light);
}
.blocked .plan-marker {
  border-color: var(--pa-warning);
}
.plan-step {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.in_progress .plan-step > span {
  font-weight: 600;
}
.plan-step p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--pa-muted);
}
.plan-status {
  flex-shrink: 0;
  color: var(--pa-muted);
  font-size: 12px;
}
.plan-updated {
  padding-top: 10px;
  text-align: right;
  font-size: 11px;
  color: var(--pa-muted);
}
</style>
