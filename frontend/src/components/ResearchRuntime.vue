<template>
  <section
    class="research-runtime"
    :class="{ compact }"
    aria-label="当前执行现场"
    aria-live="polite"
  >
    <header>
      <strong>{{ headline }}</strong>
      <time v-if="current">{{ duration(current) }}</time>
      <button v-if="running && !compact" @click="emit('pause')">暂停</button>
      <button v-if="compact" @click="emit('progress')">查看研究</button>
    </header>
    <template v-if="current">
      <p v-if="currentScope" class="scope">{{ currentScope }}</p>
      <p v-if="current.experiment_id">{{ experimentTitle(current.experiment_id) }}</p>
      <p
        v-if="questionId && current.question_id && current.question_id !== questionId"
        class="muted"
      >
        后台正在处理其他问题，当前查看范围保持不变。
      </p>
    </template>
    <p v-else-if="running" class="muted">等待下一条执行记录。</p>
    <p v-if="waiting" class="muted">等待补充必要信息，请在对话中回复。</p>
    <p v-if="paused" class="muted">
      {{ snapshot?.execution_reason?.message || '研究已暂停，记录已保存。' }}
    </p>
    <p v-if="lastUpdated" class="updated">上次记录 {{ timeLabel(lastUpdated) }}</p>
    <details v-if="!compact && history.length" class="history">
      <summary>研究经过</summary>
      <article v-for="entry in history" :key="entry.id">
        <header>
          <strong>{{ entry.label }}</strong
          ><time>{{ timeLabel(entry.recorded_at) }}</time>
        </header>
        <small>{{
          entry.question_id
            ? questionTitle(entry.question_id)
            : entry.action_id
              ? '项目级操作'
              : '归属未记录'
        }}</small>
        <p v-if="entry.reason">{{ entry.reason }}</p>
        <p v-for="judgment in entry.judgments" :key="judgment.question_id + judgment.statement">
          {{ judgment.statement }}
        </p>
        <div class="links">
          <button
            v-for="result in resultsFor(entry.artifact_refs)"
            :key="result.id"
            @click="emit('result', result.id)"
          >
            {{ result.name || '查看依据' }}
          </button>
        </div>
        <details v-if="callsFor(entry).length">
          <summary>本次行动的执行步骤</summary>
          <ol>
            <li
              v-for="call in callsFor(entry)"
              :key="call.id"
              :class="{ nested: call.parent_call_id }"
            >
              <span>{{ call.label }}</span
              ><span>{{ executionLabel(call.status) }}</span
              ><time>{{ duration(call) }}</time>
              <small v-if="call.context_usage" class="muted" title="按文本估算，实际计费用量见会话统计">
                上下文约 {{ (call.context_usage.messages_estimated_tokens + call.context_usage.tools_estimated_tokens).toLocaleString() }} tokens
                <span v-if="call.context_usage.summarized_messages" class="context-note"
                  >已摘要 {{ call.context_usage.summarized_messages }} 条历史</span
                >
              </small>
              <button
                v-if="call.run_id && runFile(call.run_id)"
                @click="emit('file', runFile(call.run_id)!)"
              >
                执行输出
              </button>
            </li>
          </ol>
        </details>
      </article>
    </details>
    <details v-if="!compact && unlinked.length" class="history">
      <summary>未关联的执行记录</summary>
      <ol>
        <li v-for="row in unlinked" :key="row.id">
          <span>{{ row.label }}</span
          ><span>{{ executionLabel(row.status) }}</span
          ><time>{{ duration(row) }}</time>
        </li>
      </ol>
    </details>
    <p v-if="!running && !paused && !waiting && !history.length" class="muted">
      {{ snapshot?.has_research ? '暂无执行经过。' : '等待研究开始。' }}
    </p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import type { ResearchSnapshot, ResearchActivity, ResearchHistory } from '@/types/research'
import {
  activityHeadline,
  researchRunning,
  callRunning,
  executionDuration,
  executionLabel,
} from '@/utils/researchProgress'
const props = defineProps<{
  snapshot: ResearchSnapshot | null
  workStatus?: string
  compact?: boolean
  questionId?: string
}>()
const emit = defineEmits<{ progress: []; pause: []; file: [path: string]; result: [id: string] }>()
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
const running = computed(() => researchRunning(state.value))
const paused = computed(() => ['paused', 'failed', 'cancelled'].includes(state.value || ''))
const waiting = computed(() => state.value === 'waiting')
const calls = computed(() => [
  ...(props.snapshot?.runtime?.actions || []),
  ...(props.snapshot?.runtime?.models || []),
])
const current = computed(() => {
  if (!running.value) return undefined
  const active =
    props.snapshot?.runtime?.current ||
    calls.value.filter(callRunning).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))[0]
  return active
})
const currentScope = computed(
  () =>
    current.value?.subject ||
    (current.value?.question_id ? questionTitle(current.value.question_id) : ''),
)
const headline = computed(() =>
  waiting.value
    ? '等待补充信息'
    : paused.value
      ? state.value === 'failed'
        ? '本次执行失败'
        : state.value === 'cancelled'
          ? '执行已取消'
          : '研究已暂停'
      : running.value
        ? current.value ? activityHeadline(current.value) : '研究执行中'
        : state.value === 'completed'
          ? '本次执行已结束'
          : '当前研究',
)
const lastUpdated = computed(() =>
  Math.max(
    props.snapshot?.runtime?.updated_at || 0,
    props.snapshot?.runtime?.models_updated_at || 0,
  ),
)
const history = computed(() => {
  const rows = props.snapshot?.runtime?.history || []
  const interrupted = new Set<string>()
  return rows
    .filter((row) => {
      if (props.questionId && row.question_id && row.question_id !== props.questionId) return false
      if (['activity_finished', 'action_interrupted'].includes(row.kind) && row.action_id) {
        if (interrupted.has(row.action_id)) return false
        interrupted.add(row.action_id)
      }
      return true
    })
    .slice(0, 30)
})
const unlinked = computed(() => calls.value.filter((row) => !row.action_id).slice(0, 12))
const callsFor = (entry: ResearchHistory) =>
  entry.action_id && history.value.find((row) => row.action_id === entry.action_id)?.id === entry.id
    ? calls.value
        .filter((row) => row.action_id === entry.action_id)
        .sort((a, b) => (a.started_at || 0) - (b.started_at || 0))
    : []
const resultsFor = (refs: string[]) =>
  props.snapshot?.results.filter((row) => refs.includes(row.id)) || []
const questionTitle = (id: string) =>
  props.snapshot?.questions?.find((q) => q.id === id)?.title || '问题名称未记录'
const experimentTitle = (id: string) =>
  props.snapshot?.experiments?.find((e) => e.task_id === id)?.title || '实验名称未记录'
const duration = (row: ResearchActivity) => executionDuration(row, now.value, running.value)
const timeLabel = (value?: number | null) =>
  value ? new Date(value * 1000).toLocaleString() : '时间未记录'
const runFile = (id: string) => props.snapshot?.runs.find((r) => r.id === id)?.stdout_path
</script>
<style scoped>
.research-runtime {
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  padding: 18px;
  background: var(--pa-surface);
  color: var(--pa-ink);
  font-size: 13px;
}
header,
li,
.links {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
header time {
  margin-left: auto;
}
time,
small,
.muted,
.updated {
  color: var(--pa-muted);
}
p {
  line-height: 1.8;
  margin: 10px 0;
  overflow-wrap: anywhere;
}
.scope {
  font-weight: 600;
}
.updated {
  font-size: 13px;
}
.context-note {
  display: block;
}
.history {
  margin-top: 18px;
  border-top: 1px solid var(--pa-line);
  padding-top: 14px;
}
article {
  padding: 18px 0;
  border-bottom: 1px solid var(--pa-line);
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
}
ol {
  list-style: none;
  padding: 0;
}
li {
  padding: 10px 0;
}
li.nested {
  margin-left: 14px;
  padding-left: 12px;
}
button {
  border: 0;
  background: transparent;
  color: var(--pa-accent);
  cursor: pointer;
  font: inherit;
}
.compact {
  margin: 18px 0;
}
</style>
