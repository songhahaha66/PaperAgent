<template>
  <section class="operation-group" :class="{ multiple, running }">
    <button
      v-if="multiple"
      type="button"
      class="operation-heading"
      :aria-expanded="expanded"
      :aria-controls="`operations-${firstId}`"
      @click="expanded = !expanded"
    >
      <svg class="disclosure" :class="{ open: expanded }" viewBox="0 0 16 16" aria-hidden="true">
        <path d="m6 4 4 4-4 4" />
      </svg>
      <span class="status-dot" aria-hidden="true"></span>
      <span class="heading-content">
        <span class="operation-title">{{ operationGroupTitle(operations) }}</span>
        <span class="operation-meta"
          >{{ operations.length }} 项操作 · {{ statusLabel
          }}<template v-if="duration"> · {{ duration }}</template></span
        >
      </span>
    </button>
    <p v-if="multiple && running && !expanded" class="current-operation">
      正在{{ currentLabel
      }}<template v-if="active.length > 1">，另有 {{ active.length - 1 }} 项进行中</template>
    </p>
    <!-- Keep rows mounted when folded, preserving each detail's expansion state. -->
    <div
      v-show="!multiple || expanded"
      :id="`operations-${firstId}`"
      class="operation-rows"
      :class="{ grouped: multiple }"
    >
      <template v-for="operation in operations" :key="operation.id">
        <ConversationTool
          v-if="isToolItem(operation)"
          :item="operation"
          :items="items"
          :reply-id="replyId"
          :work-id="workId"
          @file="$emit('file', $event)"
          @result="$emit('result', $event)"
          @expansion-change="keepSingleExpansion"
        />
        <details
          v-else
          class="plan-item"
          @toggle="keepSingleExpansion(($event.target as HTMLDetailsElement).open)"
        >
          <summary class="operation-heading">
            <svg class="disclosure" viewBox="0 0 16 16" aria-hidden="true">
              <path d="m6 4 4 4-4 4" />
            </svg>
            <span
              class="status-dot"
              :class="{
                issue: operation.status === 'failed' || operation.status === 'interrupted',
              }"
              aria-hidden="true"
            ></span>
            <span class="heading-content"
              ><span class="operation-title">更新研究计划</span
              ><span class="operation-meta">{{ itemStatus(operation.status) }}</span></span
            >
          </summary>
          <div class="plan-details">
            <p v-if="operation.explanation">{{ operation.explanation }}</p>
            <ol>
              <li
                v-for="(step, index) in operation.steps"
                :key="index"
                :class="`plan-${step.status}`"
              >
                {{ planStatus(step.status) }} · {{ step.step
                }}<small v-if="step.note"> — {{ step.note }}</small>
              </li>
            </ol>
          </div>
        </details>
      </template>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import ConversationTool from './ConversationTool.vue'
import {
  isToolItem,
  operationGroupTitle,
  type ConversationItem,
  type ItemStatus,
  type OperationItem,
} from '@/utils/conversationTimeline'
const props = defineProps<{
  operations: OperationItem[]
  items: ConversationItem[]
  replyId: string
  workId?: string
}>()
defineEmits<{ file: [path: string]; result: [id: string] }>()
const expanded = ref(false),
  now = ref(Date.now() / 1000)
const firstId = computed(() => props.operations[0]?.id)
const multiple = computed(() => props.operations.length > 1)
const active = computed(() => props.operations.filter((item) => item.status === 'running'))
const running = computed(() => active.value.length > 0)
const currentLabel = computed(() => {
  const current = active.value[0]
  return current && isToolItem(current) ? current.label : '更新研究计划'
})
// Earlier errors may be resolved later; adjacency does not establish an outcome.
const statusLabel = computed(() => (running.value ? '进行中' : '已结束'))
const duration = computed(() => {
  if (
    !props.operations.length ||
    (!running.value && props.operations.some((item) => item.finished_at == null))
  )
    return ''
  const start = Math.min(...props.operations.map((item) => item.started_at))
  const end = running.value
    ? now.value
    : Math.max(...props.operations.map((item) => item.finished_at!))
  const seconds = Math.max(0, Math.round(end - start))
  return seconds < 1
    ? ''
    : seconds < 60
      ? `${seconds} 秒`
      : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
})
const itemStatus = (status: ItemStatus) =>
  ({ running: '进行中', completed: '完成', failed: '失败', interrupted: '已中断' })[status]
const planStatus = (status: string) =>
  (
    ({
      pending: '待开始',
      in_progress: '进行中',
      completed: '已完成',
      blocked: '待解决',
    }) as Record<string, string>
  )[status]
// A single open tool may gain adjacent siblings while it is streaming.
function keepSingleExpansion(open: boolean) {
  if (!multiple.value) expanded.value = open
}
let timer: ReturnType<typeof setInterval> | undefined
watch(
  running,
  (value) => {
    if (timer) clearInterval(timer)
    timer = value
      ? setInterval(() => {
          now.value = Date.now() / 1000
        }, 1000)
      : undefined
  },
  { immediate: true },
)
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>
<style scoped>
.operation-group {
  color: var(--pa-ink);
  font-size: 13px;
}
.operation-group.multiple,
.plan-item {
  margin: 7px 0;
}
.operation-heading {
  display: grid;
  grid-template-columns: 14px 6px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  padding: 5px 7px;
  margin-left: -7px;
  border-radius: var(--pa-radius-inline);
  text-align: left;
  cursor: pointer;
  list-style: none;
}
.operation-heading::-webkit-details-marker {
  display: none;
}
.operation-heading:hover {
  background: var(--pa-surface-muted);
}
.heading-content {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 3px 8px;
}
.operation-title {
  font-size: 15px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.operation-meta {
  color: var(--pa-muted);
  font-size: 13px;
}
.disclosure {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  color: var(--pa-muted);
  transition: transform 150ms;
}
.disclosure.open,
details[open] > summary > .disclosure {
  transform: rotate(90deg);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pa-accent);
}
.operation-group > .operation-heading > .status-dot {
  background: var(--pa-muted);
}
.running > .operation-heading > .status-dot {
  background: var(--pa-accent);
  animation: pulse 1.4s infinite;
}
.status-dot.issue {
  background: var(--pa-danger);
}
.current-operation {
  margin: 2px 0 6px 28px;
  color: var(--pa-muted);
  overflow-wrap: anywhere;
}
.operation-rows.grouped {
  border-left: 1px solid var(--pa-line);
  padding-left: 16px;
  margin: 4px 0 8px 7px;
}
.plan-details {
  margin-left: 28px;
}
.plan-details ol {
  padding-left: 20px;
}
.plan-details li {
  margin: 6px 0;
}
.plan-completed {
  color: var(--pa-muted);
}
.plan-in_progress {
  color: var(--pa-accent);
}
.plan-blocked {
  color: var(--pa-danger);
}
button {
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
button:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
@media (prefers-reduced-motion: reduce) {
  .running > .operation-heading > .status-dot {
    animation: none;
  }
  .disclosure {
    transition: none;
  }
}
</style>
