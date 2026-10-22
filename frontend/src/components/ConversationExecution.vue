<template>
  <section class="execution-group" :class="{ failed }">
    <button
      type="button"
      class="execution-heading"
      :aria-expanded="expanded"
      :aria-controls="`calls-${replyId}-${groupId}`"
      @click="toggle"
    >
      <span>{{ expanded ? '⌄' : '›' }}</span>
      <span>{{
        running
          ? current?.label || '执行中'
          : failed
            ? '操作未完成'
            : `已执行 ${rows.length} 项操作`
      }}</span>
      <small>{{ running ? '执行中' : failed ? '需要处理' : '查看记录' }}</small>
    </button>
    <ol v-if="expanded" :id="`calls-${replyId}-${groupId}`">
      <li
        v-for="row in rows"
        :key="row.id"
        :class="{ nested: row.parent_call_id, error: row.status === 'failed' }"
      >
        <span>{{ row.label }}</span
        ><small
          >{{ executionLabel(row.status) }} · {{ executionDuration(row, now, running) }}</small
        >
        <p v-if="row.detail">{{ row.detail }}</p>
        <p v-if="row.message">{{ row.message }}</p>
        <button v-if="row.file" @click="$emit('file', row.file)">
          {{ row.file.split('/').pop() }}
        </button>
        <button v-for="id in row.result_ids || []" :key="id" @click="$emit('result', id)">
          查看实测结果 {{ id }}
        </button>
        <button v-if="row.script_file" @click="$emit('file', row.script_file)">
          查看代码 {{ row.script_file.split('/').pop() }}
        </button>
        <button v-if="row.error_file" @click="$emit('file', row.error_file)">错误输出</button>
        <button v-if="row.output_file" @click="$emit('file', row.output_file)">执行输出</button>
      </li>
    </ol>
    <p v-if="failed && !expanded" class="error">执行未完成，已有结果已保存。</p>
    <p v-if="detailError" class="error">详情暂不可用。<button @click="loadDetails">重试</button></p>
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
}>()
defineEmits<{ file: [path: string]; result: [id: string] }>()
const manual = ref<boolean>(),
  details = ref<Record<string, ExecutionDetail>>({}),
  detailError = ref(false)
const now = ref(Date.now() / 1000)
let timer: ReturnType<typeof setInterval> | undefined
let detailGeneration = 0
onUnmounted(() => {
  detailGeneration++
  if (timer) clearInterval(timer)
})
watch(
  () => props.segments,
  () => {
    detailGeneration++
    details.value = {}
  },
)
const rows = computed(() =>
  props.segments.flatMap((s) =>
    s.type === 'execution' ? [details.value[s.id] || s.execution] : [],
  ),
)
const running = computed(() => rows.value.some(callRunning))
const current = computed(() => [...rows.value].reverse().find(callRunning))
const failed = computed(() =>
  rows.value.some((r) => ['failed', 'error', 'interrupted', 'cancelled'].includes(r.status)),
)
const expanded = computed(() => manual.value ?? failed.value)
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
watch(
  expanded,
  (open) => {
    if (open) void loadDetails()
  },
  { immediate: true },
)
async function loadDetails() {
  if (!props.workId) return
  const generation = ++detailGeneration
  try {
    const result = await chatAPI.resolveExecutions(
      props.workId,
      props.replyId,
      props.segments.map((s) => s.id),
    )
    if (generation !== detailGeneration) return
    details.value = Object.fromEntries(result.executions.map((row) => [row.segment_id!, row]))
    detailError.value = false
  } catch {
    if (generation === detailGeneration) detailError.value = true
  }
}
function toggle() {
  manual.value = !expanded.value
}
</script>
<style scoped>
.execution-group {
  border-left: 2px solid var(--pa-line);
  margin: 12px 0;
  padding-left: 12px;
  font-size: 13px;
}
.execution-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  text-align: left;
}
small {
  color: var(--pa-muted);
  margin-left: 8px;
}
ol {
  list-style: none;
  padding: 0;
  margin: 4px 0;
}
li {
  padding: 6px 0;
  overflow-wrap: anywhere;
}
li.nested {
  margin-left: 16px;
  border-left: 1px solid var(--pa-line);
  padding-left: 8px;
}
li p {
  margin: 4px 0;
}
button {
  border: 0;
  background: transparent;
  color: var(--pa-accent);
  cursor: pointer;
  font: inherit;
  padding: 3px 0;
}
li button {
  margin-right: 12px;
}
button:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
.failed {
  border-color: var(--pa-danger);
}
.error {
  color: var(--pa-danger);
}
</style>
