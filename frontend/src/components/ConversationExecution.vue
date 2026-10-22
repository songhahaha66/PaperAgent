<template>
  <section class="execution-group" :class="{ failed, running }">
    <button
      type="button"
      class="execution-heading"
      :aria-expanded="expanded"
      :aria-controls="`calls-${replyId}-${groupId}`"
      @click="toggle"
    >
      <span class="status-dot" :class="{ pulse: running }" aria-hidden="true"></span>
      <span>{{ headline }}</span>
      <span v-if="current?.subject" class="subject">{{ current.subject }}</span>
      <time v-if="current">{{ duration(current) }}</time>
      <span class="chevron" aria-hidden="true">{{ expanded ? '⌄' : '›' }}</span>
    </button>
    <p v-if="running && progressNote" class="current-note">{{ progressNote }}</p>
    <p v-if="failed && failure?.message" class="failure-reason">{{ failure.message.split('；')[0] }}</p>
    <ol v-if="expanded" :id="`calls-${replyId}-${groupId}`">
      <template v-for="entry in entries" :key="entry.id">
        <li
          v-if="entry.type === 'execution'"
          :class="{ nested: entry.row.parent_call_id, error: isFailed(entry.row) }"
        >
          <div class="step-heading">
            <span>{{ entry.row.label }}</span>
            <small
              >{{ status(entry.row)
              }}<template v-if="duration(entry.row)"> · {{ duration(entry.row) }}</template></small
            >
          </div>
          <p v-if="entry.row.detail">{{ entry.row.detail }}</p>
          <p v-if="entry.row.message">{{ entry.row.message }}</p>
          <div class="step-links">
            <button v-if="entry.row.file" @click="$emit('file', entry.row.file)">
              {{ entry.row.file.split('/').pop() }}
            </button>
            <button
              v-for="(id, index) in entry.row.result_ids || []"
              :key="id"
              @click="$emit('result', id)"
            >
              {{ (entry.row.result_ids?.length || 0) > 1 ? `查看结果 ${index + 1}` : '查看结果' }}
            </button>
            <button v-if="entry.row.script_file" @click="$emit('file', entry.row.script_file)">
              查看代码
            </button>
            <button v-if="entry.row.error_file" @click="$emit('file', entry.row.error_file)">
              错误输出
            </button>
            <button v-if="entry.row.output_file" @click="$emit('file', entry.row.output_file)">
              执行输出
            </button>
          </div>
        </li>
        <li v-else class="execution-note">{{ entry.text }}</li>
      </template>
    </ol>
    <details v-if="expanded && modelRows.length" class="model-detail">
      <summary>模型通信记录 · {{ modelRows.length }} 次</summary>
      <p v-for="row in modelRows" :key="row.id">
        {{ rows.find((parent) => parent.id === row.parent_call_id)?.label || row.label }}
        <small>{{ status(row) }} · {{ duration(row) }}</small>
        <span v-if="row.message">{{ row.message }}</span>
      </p>
    </details>
    <p v-if="detailError" class="failure-reason">
      执行详情加载失败。<button @click="loadDetails">重试</button>
    </p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, onUnmounted, watch } from 'vue'
import { chatAPI } from '@/api/chat'
import { callRunning, executionDuration, executionLabel } from '@/utils/researchProgress'
import type { ConversationSegment, ExecutionDetail } from '@/utils/conversationTimeline'
const props = defineProps<{
  replyId: string
  groupId: string
  segments: ConversationSegment[]
  workId?: string
  replyStatus?: string
}>()
defineEmits<{ file: [path: string]; result: [id: string] }>()
const expanded = ref(false)
const details = ref<Record<string, ExecutionDetail>>({})
const detailError = ref(false)
const now = ref(Date.now() / 1000)
let timer: ReturnType<typeof setInterval> | undefined
let detailGeneration = 0
onUnmounted(() => {
  detailGeneration++
  if (timer) clearInterval(timer)
})
watch(
  () => [props.segments, props.replyStatus],
  () => {
    detailGeneration++
    details.value = {}
    if (props.replyStatus && props.replyStatus !== 'running') void loadDetails()
  },
  { immediate: true },
)
const rows = computed(() =>
  props.segments.flatMap((s) =>
    s.type === 'execution' ? [details.value[s.id] || s.execution] : [],
  ),
)
const modelRows = computed(() => rows.value.filter((r) => r.kind === 'model'))
const isFailed = (row: ExecutionDetail) =>
  ['failed', 'error', 'interrupted', 'cancelled'].includes(row.status)
const running = computed(() => rows.value.some(callRunning))
const current = computed(() => {
  const active = [...rows.value].reverse().filter(callRunning)
  return active.find((r) => r.kind !== 'model') || active[0]
})
const failure = computed(() => {
  if (running.value) return undefined
  const ids = new Set(rows.value.map((r) => r.id))
  const last = rows.value
    .filter(
      (r) =>
        r.kind !== 'model' &&
        r.capability !== 'experience.candidates' &&
        (!r.parent_call_id || !ids.has(r.parent_call_id)),
    )
    .at(-1)
  return last && isFailed(last) ? last : undefined
})
const failed = computed(() => !!failure.value)
const headline = computed(
  () => current.value?.label || (failure.value ? `${failure.value.label}未完成` : '执行记录'),
)
function note(segment: ConversationSegment) {
  if (segment.type !== 'event' || segment.block.type === 'paper_code') return ''
  const content = segment.block.content
  return typeof content === 'string' ? content : (content as { note?: string } | null)?.note || ''
}
const progressNote = computed(() => {
  const index = props.segments.findIndex(
    (s) => s.type === 'execution' && s.execution.id === current.value?.id,
  )
  return (
    props.segments
      .slice(index + 1)
      .map(note)
      .filter(Boolean)
      .at(-1) || ''
  )
})
type Entry =
  | { id: string; type: 'execution'; row: ExecutionDetail }
  | { id: string; type: 'note'; text: string }
const entries = computed(() =>
  props.segments.flatMap<Entry>((s) => {
    if (s.type === 'execution' && s.execution.kind === 'model') return []
    if (s.type === 'execution')
      return [{ id: s.id, type: 'execution' as const, row: details.value[s.id] || s.execution }]
    const text = note(s)
    return text ? [{ id: s.id, type: 'note' as const, text }] : []
  }),
)
function status(row: ExecutionDetail) {
  if (row.kind === 'model') return executionLabel(row.status)
  return callRunning(row) ? '进行中' : executionLabel(row.status)
}
function duration(row: ExecutionDetail) {
  if (!row.started_at || (!row.finished_at && !callRunning(row))) return ''
  if (row.finished_at && row.finished_at - row.started_at < 1) return ''
  return executionDuration(row, now.value, running.value)
}
watch(
  running,
  (active) => {
    if (timer) clearInterval(timer)
    timer = active
      ? setInterval(() => {
          now.value = Date.now() / 1000
        }, 1000)
      : undefined
  },
  { immediate: true },
)
async function loadDetails() {
  if (!props.workId) return
  const generation = ++detailGeneration
  const ids = props.segments.filter((s) => s.type === 'execution').map((s) => s.id)
  try {
    const executions: ExecutionDetail[] = []
    for (let offset = 0; offset < ids.length; offset += 100) {
      const result = await chatAPI.resolveExecutions(
        props.workId,
        props.replyId,
        ids.slice(offset, offset + 100),
      )
      if (generation !== detailGeneration) return
      executions.push(...result.executions)
    }
    details.value = Object.fromEntries(executions.map((row) => [row.segment_id!, row]))
    detailError.value = false
  } catch {
    if (generation === detailGeneration) detailError.value = true
  }
}
function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) void loadDetails()
}
</script>
<style scoped>
.execution-group {
  margin: 10px 0 18px;
  font-size: 13px;
  color: var(--pa-muted);
}
.execution-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  color: inherit;
  text-align: left;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.pulse {
  animation: pulse 1.4s ease-in-out infinite;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
@media (prefers-reduced-motion: reduce) {
  .pulse {
    animation: none;
  }
}
.subject {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chevron,
small,
time {
  color: var(--pa-muted);
  font-size: 12px;
}
.current-note {
  margin: 4px 0 0 14px;
}
ol {
  list-style: none;
  padding: 0 0 0 14px;
  margin: 10px 0;
  border-left: 1px solid var(--pa-line);
}
li {
  padding: 6px 0;
  overflow-wrap: anywhere;
}
li.nested {
  margin-left: 12px;
}
.step-heading {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  color: var(--pa-ink);
}
li p {
  margin: 4px 0;
}
.step-links {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.model-detail {
  font-size: 12px;
}
summary {
  cursor: pointer;
}
.execution-note {
  white-space: pre-wrap;
  line-height: 1.7;
}
button {
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  padding: 3px 0;
}
.step-links button {
  color: var(--pa-accent);
}
button:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
.failed > .execution-heading,
.failure-reason,
li.error > .step-heading {
  color: var(--pa-danger);
}
.failure-reason {
  margin: 6px 0 0 14px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.failure-reason button {
  color: inherit;
  margin-left: 8px;
}
</style>
