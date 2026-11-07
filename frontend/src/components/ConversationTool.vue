<template>
  <section class="tool-item" :class="{ failed, running }">
    <button
      type="button"
      class="tool-heading"
      :aria-expanded="expanded"
      :aria-controls="`item-${item.id}`"
      @click="toggle"
    >
      <svg class="disclosure" :class="{ open: expanded }" viewBox="0 0 16 16" aria-hidden="true">
        <path d="m6 4 4 4-4 4" />
      </svg>
      <span class="status-dot" aria-hidden="true"></span
      ><span class="tool-label">{{ row.label }}</span>
      <span class="tool-state">{{ stateLabel }}</span
      ><time v-if="duration">{{ duration }}</time>
    </button>
    <p v-if="failed && !expanded && row.message" class="failure-reason">{{ row.message }}</p>
    <div v-if="expanded" :id="`item-${item.id}`" class="tool-details">
      <p v-if="row.detail">{{ row.detail }}</p>
      <p v-if="row.message" class="failure-reason">{{ row.message }}</p>
      <ul v-if="row.warnings?.length" class="tool-warnings" aria-label="编译警告">
        <li v-for="(warning, index) in row.warnings" :key="index">{{ warning }}</li>
      </ul>
      <pre v-if="row.output_tail" class="tool-output">{{ row.output_tail }}</pre>
      <p v-else-if="row.run_id && !running && !row.output_file && !row.error_file">
        未产生标准输出。
      </p>
      <div class="tool-links">
        <button v-if="row.file" @click="$emit('file', row.file)">
          {{ row.file.split('/').pop() }}
        </button>
        <button v-if="row.script_file" @click="$emit('file', row.script_file)">查看执行代码</button>
        <button v-for="path in row.files || []" :key="path" @click="$emit('file', path)">
          {{ path.split('/').pop() }}
        </button>
        <button v-for="id in row.result_ids || []" :key="id" @click="$emit('result', id)">
          查看结果 {{ id }}
        </button>
        <button v-if="row.error_file" @click="$emit('file', row.error_file)">诊断日志</button>
        <button v-if="row.output_file" @click="$emit('file', row.output_file)">完整日志</button>
      </div>
      <div v-if="children.length" class="child-thread" aria-label="子 Agent 会话">
        <template v-for="child in groupAdjacentOperations(children)" :key="child.id">
          <ConversationOperations
            v-if="child.type === 'operationGroup'"
            :operations="child.operations"
            :items="items"
            :reply-id="replyId"
            :work-id="workId"
            @file="$emit('file', $event)"
            @result="$emit('result', $event)"
          />
          <MarkdownRenderer
            v-else-if="child.type === 'agentMessage'"
            :content="child.text"
            :work-id="workId"
          />
        </template>
      </div>
      <p v-if="detailError" class="failure-reason">
        详情加载失败。<button @click="loadDetails">重试</button>
      </p>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, onUnmounted, watch } from 'vue'
import { chatAPI } from '@/api/chat'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ConversationOperations from './ConversationOperations.vue'
import {
  groupAdjacentOperations,
  isToolItem,
  type ToolItem,
  type ConversationItem,
} from '@/utils/conversationTimeline'
const props = defineProps<{
  item: ToolItem
  items: ConversationItem[]
  replyId: string
  workId?: string
}>()
const emit = defineEmits<{
  file: [path: string]
  result: [id: string]
  'expansion-change': [expanded: boolean]
}>()
const expanded = ref(false),
  detail = ref<ToolItem>(),
  detailError = ref(false),
  now = ref(Date.now() / 1000)
let generation = 0,
  timer: ReturnType<typeof setInterval> | undefined
const row = computed(() => detail.value || props.item)
const running = computed(() => row.value.status === 'running')
const failed = computed(() => ['failed', 'interrupted'].includes(row.value.status))
const stateLabel = computed(
  () =>
    ({ running: '进行中', completed: '完成', failed: '失败', interrupted: '已中断' })[
      row.value.status
    ],
)
const duration = computed(() => {
  const end = row.value.finished_at ?? (running.value ? now.value : undefined)
  if (!end) return ''
  const seconds = Math.max(0, Math.round(end - row.value.started_at))
  return seconds < 1
    ? ''
    : seconds < 60
      ? `${seconds} 秒`
      : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
})
const children = computed(() =>
  props.items
    .filter(
      (item) =>
        item.thread_id === row.value.child_thread_id &&
        item.turn_id === row.value.child_turn_id &&
        item.type !== 'userMessage',
    )
    .sort((a, b) => a.started_at - b.started_at || a.sequence - b.sequence),
)
watch(
  () => props.item,
  () => {
    generation++
    detail.value = undefined
  },
)
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
  generation++
  if (timer) clearInterval(timer)
})
async function loadDetails() {
  if (!props.workId) return
  const version = ++generation
  try {
    const result = await chatAPI.resolveItems(props.workId, props.replyId, [props.item.id])
    if (version !== generation) return
    const saved = result.items.find((item) => item.id === props.item.id)
    if (saved && isToolItem(saved)) detail.value = saved
    detailError.value = false
  } catch {
    if (version === generation) detailError.value = true
  }
}
function toggle() {
  expanded.value = !expanded.value
  emit('expansion-change', expanded.value)
  if (expanded.value) void loadDetails()
}
</script>
<style scoped>
.tool-item {
  margin: 7px 0;
  color: var(--pa-ink);
  font-size: 13px;
}
.tool-heading {
  display: grid;
  grid-template-columns: 14px 6px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  padding: 5px 7px;
  margin-left: -7px;
  border-radius: var(--pa-radius-inline);
  text-align: left;
}
.tool-heading:hover {
  background: var(--pa-surface-muted);
}
.tool-label {
  font-size: 15px;
  font-weight: 500;
  overflow-wrap: anywhere;
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
.disclosure.open {
  transform: rotate(90deg);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pa-accent);
}
.running .status-dot {
  animation: pulse 1.4s infinite;
}
.failed .status-dot {
  background: var(--pa-danger);
}
.tool-state,
time {
  color: var(--pa-muted);
  font-size: 13px;
  white-space: nowrap;
}
.tool-details {
  margin-left: 28px;
  padding: 2px 0 7px;
  overflow-wrap: anywhere;
}
.tool-details p {
  margin: 5px 0;
}
.tool-output {
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 8px 0;
  padding: 8px 10px;
  background: var(--pa-surface-muted);
  border-radius: var(--pa-radius-inline);
  line-height: 1.5;
  font-size: 13px;
}
.tool-warnings {
  padding-left: 18px;
  margin: 6px 0;
}
.tool-links {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.tool-links button {
  color: var(--pa-accent);
}
.child-thread {
  border-left: 1px solid var(--pa-line);
  padding-left: 16px;
  margin-top: 12px;
}
.failure-reason {
  color: var(--pa-danger);
  margin: 4px 0 4px 28px;
  white-space: pre-wrap;
}
button {
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
button:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
@media (prefers-reduced-motion: reduce) {
  .running .status-dot {
    animation: none;
  }
  .disclosure {
    transition: none;
  }
}
</style>
