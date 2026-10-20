<template>
  <section
    class="research-runtime"
    :class="{ compact }"
    aria-label="当前研究状态"
    aria-live="polite"
  >
    <header>
      <span class="signal" :class="{ live: running }"></span><strong>{{ headline }}</strong
      ><button v-if="compact" @click="emit('progress')">
        {{ compact ? '展开进度' : '执行记录' }}
      </button>
    </header>
    <p v-if="currentQuestion" class="current-question">{{ currentQuestion }}</p>
    <p v-if="latestDecision" class="decision">{{ latestDecision.reason }}</p>
    <div v-for="action in active" :key="action.id" class="active-operation">
      <span>{{ action.label }}</span
      ><time>{{ duration(action) }}</time>
      <small v-if="action.task_id">{{ taskTitle(action.task_id) }}</small>
    </div>
    <p v-if="waiting" class="waiting">需要你补充必要信息，请在对话中回复。</p>
    <p v-else-if="paused" class="waiting">
      {{
        snapshot?.execution_reason?.message ||
        '执行位置与已有成果已保存。继续后从未完成的操作恢复。'
      }}
    </p>
    <p v-else-if="running && !active.length" class="waiting">正在同步执行状态…</p>
    <details v-if="!compact && history.length" class="history">
      <summary>最近执行 · {{ history.length }} 条</summary>
      <ol>
        <li v-for="row in history" :key="row.id + row.label">
          <span
            >{{ row.label }}<small v-if="row.task_id"> · {{ taskTitle(row.task_id) }}</small></span
          ><span>{{ statusLabel(row.status) }} · {{ duration(row) }}</span
          ><small v-if="row.input_tokens || row.output_tokens"
            >输入 {{ row.input_tokens || 0 }} / 输出 {{ row.output_tokens || 0 }} tokens</small
          ><button
            v-if="row.run_id && runFile(row.run_id)"
            @click="emit('file', runFile(row.run_id)!)"
          >
            执行输出
          </button>
        </li>
      </ol>
    </details>
    <p v-if="!running && !paused && !waiting && !history.length" class="waiting">
      {{
        snapshot?.has_research
          ? '此项研究尚无可展示的实时运行记录。'
          : '提交研究目标后，实际执行过程会显示在这里。'
      }}
    </p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import type { ResearchSnapshot, ResearchActivity } from '@/types/research'
const props = defineProps<{
  snapshot: ResearchSnapshot | null
  workStatus?: string
  compact?: boolean
}>()
const emit = defineEmits<{ progress: []; file: [path: string] }>()
const now = ref(Date.now() / 1000)
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now() / 1000
  }, 1000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
const state = computed(() => props.workStatus || props.snapshot?.work_status)
const running = computed(() => ['in_progress', 'running', 'processing'].includes(state.value || ''))
const paused = computed(() => ['paused', 'failed', 'cancelled'].includes(state.value || ''))
const waiting = computed(() => state.value === 'waiting')
const actions = computed(() => props.snapshot?.runtime?.actions || [])
const models = computed(() => props.snapshot?.runtime?.models || [])
const active = computed(() =>
  running.value
    ? [
        ...actions.value.filter((a) => a.status === 'running'),
        ...models.value.filter(
          (m) => !m.finished_at && ['sent', 'http_response'].includes(m.status),
        ),
      ]
    : [],
)
const history = computed(() =>
  [...actions.value, ...models.value].sort((a, b) => b.started_at - a.started_at).slice(0, 30),
)
const latestDecision = computed(() => props.snapshot?.runtime?.decisions.at(-1))
const currentQuestion = computed(() => {
  const task = active.value.find((a) => a.task_id)?.task_id
  return props.snapshot?.questions?.find((q) => task && q.task_ids.includes(task))?.title || ''
})
const headline = computed(() =>
  waiting.value
    ? '等待你的信息'
    : paused.value
      ? '研究已暂停'
      : running.value
        ? active.value[0]?.label || '研究正在进行'
        : state.value === 'completed'
          ? '本次研究已完成'
          : '当前研究',
)
const duration = (row: ResearchActivity) => {
  if (!row.finished_at && !running.value) return '未记录结束时间'
  const seconds = Math.max(0, Math.floor((row.finished_at || now.value) - row.started_at))
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}
const taskTitle = (id: string) =>
  props.snapshot?.outcomes?.find((t) => t.task_id === id)?.title ||
  props.snapshot?.questions?.find((q) => q.task_ids.includes(id))?.title ||
  '相关研究'
const statusLabel = (status: string) =>
  ({
    running: running.value ? '进行中' : '已中断',
    completed: '完成',
    failed: '失败',
    interrupted: '已中断',
    sent: running.value ? '等待模型' : '已中断',
    http_response: running.value ? '模型返回中' : '已中断',
  })[status] || '记录已保存'
const runFile = (id: string) => props.snapshot?.runs.find((r) => r.id === id)?.stdout_path
</script>
<style scoped>
.research-runtime {
  border: 1px solid var(--pa-line);
  border-radius: 12px;
  padding: 18px;
  background: var(--pa-surface);
  color: var(--pa-ink);
  font-size: 13px;
}
.research-runtime header {
  display: flex;
  gap: 9px;
  align-items: center;
}
.research-runtime header button {
  margin-left: auto;
}
.signal {
  width: 7px;
  height: 7px;
  background: var(--pa-muted);
  border-radius: 50%;
  flex-shrink: 0;
}
.signal.live {
  background: var(--pa-accent);
  box-shadow: 0 0 0 4px var(--pa-surface-active);
}
.current-question {
  font-size: 15px;
  font-weight: 600;
}
.decision,
.waiting {
  line-height: 1.7;
  color: var(--pa-muted);
  margin: 10px 0 0;
}
.active-operation {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 0 0;
}
.active-operation time {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  color: var(--pa-muted);
}
small {
  color: var(--pa-muted);
  font-size: 12px;
}
.active-operation small {
  width: 100%;
}
.history {
  margin-top: 16px;
  border-top: 1px solid var(--pa-line);
  padding-top: 12px;
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
}
ol {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
}
li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 0;
  border-bottom: 1px solid var(--pa-line);
}
li > span:nth-child(2) {
  margin-left: auto;
  color: var(--pa-muted);
}
button {
  border: 0;
  background: transparent;
  font: inherit;
  font-size: 12px;
  color: var(--pa-accent);
  cursor: pointer;
}
.compact {
  margin: 18px 0;
}
.compact .decision {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
