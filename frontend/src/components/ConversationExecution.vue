<template>
  <section class="execution-group" :class="{ failed, running }">
    <button
      type="button"
      class="execution-heading"
      :aria-expanded="expanded"
      :aria-controls="`calls-${replyId}-${groupId}`"
      @click="toggle"
    >
      <svg class="disclosure" :class="{ open: expanded }" viewBox="0 0 16 16" aria-hidden="true">
        <path d="m6 4 4 4-4 4" />
      </svg>
      <span class="status-dot" :class="{ pulse: running }" aria-hidden="true"></span>
      <span>{{ headline }}</span>
      <span v-if="grouped" class="call-state">{{ groupState }}</span>
      <span v-if="grouped" class="call-state">{{ rows.length }} 次操作</span>
      <span v-if="current?.subject && !grouped" class="subject">{{ current.subject }}</span>
      <span v-if="!grouped && rows.length === 1" class="call-state">{{ status(rows[0]!) }}</span>
      <time v-if="headingRow">{{ duration(headingRow) }}</time>
    </button>
    <p v-if="running && progressNote" class="current-note">{{ progressNote }}</p>
    <p v-if="failed && !expanded && failure?.message" class="failure-reason">
      {{ failure.message.split('；')[0] }}
    </p>
    <ol v-if="expanded" :id="`calls-${replyId}-${groupId}`">
      <template v-for="entry in entries" :key="entry.id">
        <li
          v-if="entry.type === 'execution'"
          :class="{ nested: entry.row.parent_call_id, error: isFailed(entry.row) }"
        >
          <button
            v-if="grouped"
            type="button"
            class="step-heading"
            :aria-expanded="!!expandedCalls[entry.id]"
            :aria-controls="`call-${replyId}-${entry.id}`"
            @click="expandedCalls[entry.id] = !expandedCalls[entry.id]"
          >
            <svg
              class="disclosure"
              :class="{ open: expandedCalls[entry.id] }"
              viewBox="0 0 16 16"
              aria-hidden="true"
            >
              <path d="m6 4 4 4-4 4" />
            </svg>
            <span>{{ entry.row.label }}</span>
            <small>{{ status(entry.row) }}</small>
            <small v-if="duration(entry.row)">{{ duration(entry.row) }}</small>
          </button>
          <p
            v-if="grouped && !expandedCalls[entry.id] && isFailed(entry.row) && entry.row.message"
            class="step-failure"
          >
            {{ entry.row.message.split('\n')[0] }}
          </p>
          <div
            v-if="!grouped || expandedCalls[entry.id]"
            :id="`call-${replyId}-${entry.id}`"
            class="step-content"
          >
            <p v-if="entry.row.detail">{{ entry.row.detail }}</p>
            <p v-if="entry.row.message">{{ entry.row.message }}</p>
            <ul v-if="entry.row.warnings?.length" class="execution-warnings" aria-label="编译警告">
              <li v-for="(warning, index) in entry.row.warnings" :key="index">{{ warning }}</li>
            </ul>
            <pre v-if="entry.row.output_tail" class="execution-output">{{
              entry.row.output_tail
            }}</pre>
            <p
              v-else-if="
                entry.row.run_id &&
                !callRunning(entry.row) &&
                !entry.row.output_file &&
                !entry.row.error_file
              "
              class="empty-output"
            >
              未产生标准输出。
            </p>
            <div class="step-links">
              <button v-if="entry.row.file" :title="entry.row.file" @click="$emit('file', entry.row.file)">
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
                查看执行代码
              </button>
              <button
                v-for="path in entry.row.files || []"
                :key="path"
                :title="path"
                @click="$emit('file', path)"
              >
                {{ path.split('/').pop() }}
              </button>
              <button v-if="entry.row.error_file" @click="$emit('file', entry.row.error_file)">
                诊断日志
              </button>
              <button v-if="entry.row.output_file" @click="$emit('file', entry.row.output_file)">
                完整日志
              </button>
            </div>
          </div>
        </li>
        <li v-else class="execution-note">{{ entry.text }}</li>
      </template>
    </ol>
    <p v-if="detailError" class="failure-reason">
      执行详情加载失败。<button @click="loadDetails">重试</button>
    </p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, onUnmounted, watch } from 'vue'
import { chatAPI } from '@/api/chat'
import { callRunning, executionDuration, activityStatus } from '@/utils/researchProgress'
import type { ConversationSegment, ExecutionDetail } from '@/utils/conversationTimeline'
const props = defineProps<{
  replyId: string
  groupId: string
  label?: string
  segments: ConversationSegment[]
  workId?: string
  replyStatus?: string
}>()
defineEmits<{ file: [path: string]; result: [id: string] }>()
const expanded = ref(false)
const expandedCalls = ref<Record<string, boolean>>({})
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
    if (expanded.value && props.replyStatus && props.replyStatus !== 'running') void loadDetails()
  },
  { immediate: true },
)
const rows = computed(() =>
  props.segments
    .flatMap((s) => (s.type === 'execution' ? [{ ...s.execution, ...details.value[s.id] }] : []))
    .map((row) =>
      props.replyStatus && props.replyStatus !== 'running' && callRunning(row)
        ? { ...row, status: 'interrupted' }
        : row,
    ),
)
const isFailed = (row: ExecutionDetail) =>
  ['failed', 'error', 'interrupted', 'cancelled'].includes(row.status)
const grouped = computed(() => rows.value.length > 1)
// A compact group shows its latest step, not an aggregate verdict on earlier attempts.
const latest = computed(() => rows.value.at(-1))
const current = computed(() => [...rows.value].reverse().find(callRunning))
const headingRow = computed(() => current.value || latest.value)
const running = computed(() => !!current.value)
const groupState = computed(() => (headingRow.value ? status(headingRow.value) : '等待下一步'))
const failure = computed(() =>
  !running.value && latest.value && isFailed(latest.value) ? latest.value : undefined,
)
const failed = computed(() => !!failure.value)
const headline = computed(() => headingRow.value?.label || props.label || '执行记录')
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
    if (s.type === 'execution')
      return [
        {
          id: s.id,
          type: 'execution' as const,
          row: rows.value.find((row) => row.id === s.execution.id) || s.execution,
        },
      ]
    const text = note(s)
    return text ? [{ id: s.id, type: 'note' as const, text }] : []
  }),
)
function status(row: ExecutionDetail) {
  return activityStatus(row)
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
  if (!grouped.value) {
    const segment = props.segments.find((s) => s.type === 'execution')
    if (segment) expandedCalls.value[segment.id] = expanded.value
  }
  if (expanded.value) void loadDetails()
}
</script>
<style scoped>
.execution-output {
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding: 8px 10px;
  margin: 8px 0;
  background: var(--pa-surface-muted);
  border-radius: var(--pa-radius-inline);
  font-size: 13px;
  line-height: 1.5;
}
.execution-group {
  margin: 6px 0;
  font-size: 13px;
  color: var(--pa-ink);
}
.execution-group > .execution-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  padding: 5px 7px;
  margin-left: -7px;
  border-radius: var(--pa-radius-inline);
  color: var(--pa-ink);
  font-size: 15px;
  font-weight: 500;
  text-align: left;
  transition: background 150ms ease;
}
.execution-group > .execution-heading:hover {
  background: var(--pa-surface-muted);
}
.disclosure {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
  align-self: center;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  color: var(--pa-muted);
  transition: transform 150ms ease;
}
.disclosure.open {
  transform: rotate(90deg);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--pa-radius-circle);
  background: var(--pa-accent);
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
  .disclosure {
    transition: none;
  }
}
.subject {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.call-state,
small,
time {
  color: var(--pa-muted);
  font-size: 13px;
}
.current-note {
  margin: 4px 0 0 14px;
}
.execution-warnings {
  padding-left: 18px;
  margin: 6px 0;
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
  width: 100%;
  text-align: left;
  border-radius: var(--pa-radius-inline);
}
.step-heading:hover {
  background: var(--pa-surface-muted);
}
.step-content {
  padding-left: 24px;
}
.step-failure {
  color: var(--pa-danger);
  padding-left: 24px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
li p {
  margin: 4px 0;
}
.step-links {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
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
button:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
.failed .status-dot {
  background: var(--pa-danger);
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
