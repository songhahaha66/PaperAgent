<template>
  <section class="research-progress" aria-label="研究工作台">
    <nav v-if="questions.length > 1" class="questions" aria-label="选择研究问题">
      <button
        v-for="(question, index) in questions"
        :key="question.id"
        :aria-pressed="selected?.id === question.id"
        @click="selectQuestion(question.id)"
      >
        <span>{{ index + 1 }}</span
        >{{ question.title }}
      </button>
    </nav>

    <header class="overview">
      <div class="status-line" role="status">
        <span class="execution-state" :class="{ running, interrupted }">{{ executionState }}</span>
        <span>{{ hasReport ? '报告已生成' : '报告尚未生成' }}</span>
      </div>
      <h2>{{ selected?.title || '研究' }}</h2>
      <div class="actions">
        <button v-if="hasReport" class="primary" @click="emit('object', { type: 'paper' })">
          打开报告
        </button>
        <button v-if="selected" @click="emit('ask', selected.id)">继续讨论</button>
        <button v-if="running" @click="emit('pause')">暂停执行</button>
        <button v-else-if="actionLabel" :disabled="actionBusy" @click="emit('action')">
          {{ actionLabel }}
        </button>
      </div>
    </header>

    <div v-if="running || interrupted || status === 'waiting'" class="execution-note" role="status">
      <strong v-if="running">{{ activeAction ? activityHeadline(activeAction) : '正在准备下一步' }}</strong>
      <p v-if="running && activeAction?.subject">{{ activeAction.subject }}</p>
      <p v-else-if="status === 'waiting'">等待补充必要信息，请在对话中回复。</p>
      <template v-else-if="interrupted">
        <p>{{ snapshot?.execution_reason?.message || '本次执行未继续，已有记录已保留。' }}</p>
        <p v-if="hasReport">已生成的报告仍可打开，执行详情见下方记录。</p>
        <p v-else>已保存的代码和输出可在下方执行记录中查看。</p>
      </template>
    </div>

    <article v-if="selected" :key="selected.id" class="question-detail">
      <section class="conclusion">
        <h3>{{ needsUpdate ? '当前结论待更新' : '当前结论' }}</h3>
        <template v-if="needsUpdate">
          <p class="muted">问题、依据或解释已有变化，已保存的结论需要重新评估。</p>
          <details v-if="selected.judgment">
            <summary>查看更新前的结论</summary>
            <MarkdownRenderer
              :content="evidenceText(selected.judgment)"
              :work-id="snapshot?.work_id"
            />
          </details>
        </template>
        <template v-else-if="selected.judgment">
          <div :class="{ 'short-conclusion': longJudgment && !expanded }">
            <MarkdownRenderer
              :content="evidenceText(selected.judgment)"
              :work-id="snapshot?.work_id"
            />
          </div>
          <button
            v-if="longJudgment"
            class="text-action"
            :aria-expanded="expanded"
            @click="expanded = !expanded"
          >
            {{ expanded ? '收起完整结论' : '展开完整结论' }}
          </button>
        </template>
        <p v-else class="muted">尚未形成结论，已有计算和输出可在执行记录中查看。</p>
        <p
          v-if="selected.coverage === 'partial' || selected.coverage === 'unanswered'"
          class="coverage-note"
        >
          {{
            selected.coverage === 'partial' ? '当前仅回答了部分问题。' : '当前问题尚未得到回答。'
          }}
        </p>
      </section>

      <section v-if="selected.limitations.length" class="limitations">
        <h3>适用范围与局限</h3>
        <ul>
          <li v-for="limit in selected.limitations.slice(0, 2)" :key="limit">{{ limit }}</li>
        </ul>
        <details v-if="selected.limitations.length > 2">
          <summary>其余 {{ selected.limitations.length - 2 }} 项局限</summary>
          <ul>
            <li v-for="limit in selected.limitations.slice(2)" :key="limit">{{ limit }}</li>
          </ul>
        </details>
      </section>

      <section v-if="selected.issues.length" class="open-issues">
        <h3>尚未解决的问题</h3>
        <article v-for="issue in selected.issues" :key="issue.id">
          <p>{{ issue.message }}</p>
          <p v-if="issue.next_step" class="muted">{{ issue.next_step }}</p>
          <button class="text-action" @click="emit('issue', issue.id)">查看问题详情</button>
        </article>
      </section>

      <section v-if="figures.length && !needsUpdate" class="figures">
        <h3>相关图表</h3>
        <figure v-for="figure in figures" :key="figure.id">
          <MarkdownRenderer
            :content="figureMarkdown(figure.figure_path!)"
            :work-id="snapshot?.work_id"
          />
          <figcaption>
            <button
              class="text-action"
              @click="emit('object', { type: 'file', path: figure.figure_path! })"
            >
              {{ figure.name || '打开图表' }}
            </button>
          </figcaption>
        </figure>
      </section>
    </article>
    <p v-else class="empty">
      {{ snapshot ? '尚未形成研究结论。开始后，进展和结果会显示在这里。' : '正在读取研究记录…' }}
    </p>

    <details class="records">
      <summary>执行记录与文件<span>方法、代码、输出及历史结论</span></summary>
      <div class="record-content">
        <template v-if="selected">
          <div v-if="selected.answer_gaps?.length" class="record-note">
            <h3>当前判断待补全</h3>
            <p v-for="gap in selected.answer_gaps" :key="gap">{{ gap }}</p>
          </div>
          <p v-if="selected.stop_reason">
            <strong>本次停止说明：</strong>{{ selected.stop_reason }}
          </p>
          <p v-if="selected.next_step"><strong>记录的下一步：</strong>{{ selected.next_step }}</p>
          <article v-for="task in selectedExperiments" :key="task.task_id" class="experiment">
            <h3>{{ task.title || '实验记录' }}</h3>
            <p class="muted">
              {{
                task.availability === 'historical'
                  ? '历史记录'
                  : task.execution_status === 'succeeded'
                    ? '执行已结束'
                    : executionLabel(task.execution_status)
              }}
            </p>
            <button
              class="text-action"
              @click="
                emit('object', { type: 'result', resultType: 'experiment', id: task.task_id })
              "
            >
              查看方法与实验记录
            </button>
          </article>
          <details v-if="previousJudgments.length">
            <summary>之前的判断</summary>
            <article v-for="(judgment, index) in previousJudgments" :key="index">
              <small>{{
                judgment.recorded_at
                  ? new Date(judgment.recorded_at * 1000).toLocaleString()
                  : '时间未记录'
              }}</small>
              <MarkdownRenderer
                :content="evidenceText(judgment.statement)"
                :work-id="snapshot?.work_id"
              />
              <p v-for="limit in judgment.limitations" :key="limit" class="muted">{{ limit }}</p>
            </article>
          </details>
        </template>
        <details v-if="snapshot?.runs.length">
          <summary>项目代码与运行输出</summary>
          <div v-for="(run, index) in snapshot.runs" :key="run.id" class="run-row">
            <span>运行 {{ index + 1 }} · {{ executionLabel(run.status) }}</span>
            <button
              v-if="run.input_path"
              class="text-action"
              @click="emit('object', { type: 'file', path: run.input_path })"
            >
              查看代码
            </button>
            <button
              v-if="run.stdout_path"
              class="text-action"
              @click="emit('object', { type: 'file', path: run.stdout_path })"
            >
              查看输出
            </button>
          </div>
        </details>
        <ResearchRuntime
          :snapshot="snapshot"
          :work-status="workStatus"
          :question-id="selected?.id"
          @pause="emit('pause')"
          @file="(path) => emit('object', { type: 'file', path })"
          @result="(id) => emit('object', { type: 'result', resultType: 'registered', id })"
        />
        <details
          v-if="snapshot?.artifacts.length || Object.keys(snapshot?.delivery_stages || {}).length"
        >
          <summary>报告文件与生成记录</summary>
          <p v-for="path in snapshot?.artifacts" :key="path">
            <button class="text-action" @click="emit('object', { type: 'file', path })">
              {{ path }}
            </button>
          </p>
          <p v-for="(stage, key) in snapshot?.delivery_stages" :key="key">
            {{ stageName(String(key)) }}：{{
              executionLabel(stage.status === 'in_progress' ? 'running' : stage.status)
            }}
          </p>
        </details>
      </div>
    </details>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ResearchRuntime from './ResearchRuntime.vue'
import type { ResearchSnapshot } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
import {
  currentActivity,
  activityHeadline,
  deliveryLabels,
  executionLabel,
  researchRunning,
} from '@/utils/researchProgress'
import { evidenceMarkdown } from '@/utils/researchEvidence'
const props = defineProps<{
  snapshot: ResearchSnapshot | null
  workStatus?: string
  actionLabel?: string
  actionBusy?: boolean
}>()
const emit = defineEmits<{
  object: [object: WorkObject]
  issue: [id: string]
  ask: [id: string]
  pause: []
  action: []
}>()
const selectedId = ref('')
const expanded = ref(false)
const selectQuestion = (id: string) => {
  selectedId.value = id
  expanded.value = false
}
const questions = computed(() => props.snapshot?.questions?.filter((q) => !q.retired) || [])
const selected = computed(
  () => questions.value.find((q) => q.id === selectedId.value) || questions.value[0],
)
const needsUpdate = computed(
  () => selected.value?.answer_status === 'needs_update' || !!selected.value?.answer_gaps?.length,
)
const longJudgment = computed(() => (selected.value?.judgment.length || 0) > 220)
const previousJudgments = computed(() => {
  const question = selected.value
  const history = question?.judgments || []
  return question?.judgment && history.at(-1)?.statement === question.judgment
    ? history.slice(0, -1)
    : history
})
const selectedExperiments = computed(
  () =>
    props.snapshot?.experiments?.filter((e) => selected.value?.experiment_ids.includes(e.id)) || [],
)
const status = computed(() => props.workStatus || props.snapshot?.work_status || '')
const running = computed(() => researchRunning(status.value))
const interrupted = computed(() => ['paused', 'failed', 'cancelled'].includes(status.value))
const hasReport = computed(() => !!props.snapshot?.artifacts.length)
const activeAction = computed(
  () =>
    props.snapshot?.runtime &&
    (currentActivity(props.snapshot.runtime) || props.snapshot.runtime.current),
)
const executionState = computed(() => {
  if (running.value) return '正在执行'
  return (
    {
      completed: '本次执行已结束',
      failed: '本次执行失败',
      paused: '执行已暂停',
      cancelled: '执行已取消',
      waiting: '等待补充信息',
    }[status.value] || '尚未开始执行'
  )
})
const figures = computed(
  () =>
    props.snapshot?.results.filter(
      (r) =>
        r.kind === 'figure' &&
        r.figure_path &&
        selected.value?.experiment_ids.includes(r.subproblem_id),
    ) || [],
)
const figureMarkdown = (path: string) =>
  `![研究图表](<${encodeURI(path).replace(/[<>]/g, encodeURIComponent)}>)`
const stageName = (key: string) =>
  deliveryLabels[key as keyof typeof deliveryLabels]?.[0] || '论文处理'
const evidenceText = (text: string) => evidenceMarkdown(text, props.snapshot)
</script>
<style scoped>
.research-progress {
  color: var(--pa-ink);
  font-size: 13px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}
.overview {
  margin-bottom: 24px;
}
h2 {
  font-size: 20px;
  line-height: 1.45;
  letter-spacing: -0.02em;
  margin: 12px 0 16px;
}
h3 {
  font-size: 13px;
  margin: 0 0 10px;
  font-weight: 600;
}
p {
  margin: 8px 0;
}
.status-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: var(--pa-muted);
  font-size: 12px;
}
.execution-state::before {
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: var(--pa-radius-circle);
  background: var(--pa-muted);
  margin-right: 7px;
  vertical-align: middle;
}
.execution-state.running::before {
  background: var(--pa-accent);
}
.execution-state.interrupted::before {
  background: var(--pa-warning);
}
.actions,
.run-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
button {
  font: inherit;
  cursor: pointer;
  color: var(--pa-ink);
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface);
  padding: 6px 12px;
}
button:hover {
  background: var(--pa-surface-muted);
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
button:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 3px;
}
button.primary {
  background: var(--pa-accent);
  border-color: var(--pa-accent);
  color: var(--pa-on-accent);
}
button.text-action {
  background: transparent;
  border: 0;
  padding: 4px 0;
  color: var(--pa-accent);
  text-align: left;
}
.muted,
small {
  color: var(--pa-muted);
}
.conclusion {
  font-size: 14px;
}
.conclusion :deep(p:first-child) {
  margin-top: 0;
}
.conclusion :deep(p) {
  line-height: 1.8;
}
.short-conclusion {
  max-height: 9em;
  overflow: hidden;
}
.limitations {
  margin-top: 20px;
  padding: 14px 16px;
  background: var(--pa-surface-muted);
  border-radius: var(--pa-radius-control);
}
.limitations ul {
  margin: 0;
  padding-left: 17px;
  color: var(--pa-muted);
}
.limitations li + li {
  margin-top: 6px;
}
.limitations h3 {
  margin-bottom: 6px;
}
.open-issues,
.figures {
  margin-top: 24px;
}
.open-issues article {
  border-left: 2px solid var(--pa-warning);
  padding-left: 12px;
}
.coverage-note {
  color: var(--pa-muted);
}
.execution-note {
  border-left: 2px solid var(--pa-accent);
  padding: 8px 14px;
  margin: 0 0 20px;
  background: var(--pa-surface-muted);
}
.execution-note p {
  margin: 4px 0;
}
.questions {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 12px;
  margin-bottom: 12px;
}
.questions button {
  display: flex;
  align-items: baseline;
  gap: 8px;
  text-align: left;
  flex: 0 0 auto;
  max-width: 250px;
}
.questions button[aria-pressed='true'] {
  border-color: var(--pa-accent);
  color: var(--pa-accent);
  background: var(--pa-surface-active);
}
.questions span {
  color: var(--pa-muted);
}
.records {
  border-top: 1px solid var(--pa-line);
  margin-top: 24px;
  padding-top: 16px;
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
  padding: 4px 0;
}
.records > summary {
  color: var(--pa-ink);
}
.records > summary span {
  display: block;
  color: var(--pa-muted);
  font-size: 12px;
  margin-left: 16px;
}
details details {
  margin: 12px 0;
}
.record-content {
  padding-top: 12px;
}
.experiment {
  padding: 16px 0;
  border-bottom: 1px solid var(--pa-line);
}
.experiment h3 {
  margin: 0;
}
.record-content :deep(.research-runtime) {
  margin-top: 16px;
}
.run-row {
  padding: 6px 0;
  gap: 12px;
}
figure {
  margin: 12px 0;
}
figcaption {
  text-align: center;
}
.empty {
  color: var(--pa-muted);
  padding: 16px 0;
}
</style>
