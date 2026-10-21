<template>
  <section class="research-progress" aria-label="研究工作台">
    <header class="overview">
      <h3>研究问题</h3>
      <p>{{ questions.length }} 个问题，{{ snapshot?.results.length || 0 }} 项可用结果</p>
    </header>
    <p v-if="!questions.length" class="empty">尚未建立研究问题。</p>
    <nav v-else class="questions" aria-label="选择研究问题">
      <button
        v-for="(question, index) in questions"
        :key="question.id"
        :class="{ selected: selected?.id === question.id }"
        @click="selectedId = question.id"
      >
        <span class="question-index">{{ index + 1 }}</span>
        <span
          >{{ question.title }}<small>{{ questionState(question) }}</small></span
        >
      </button>
    </nav>
    <article v-if="selected" class="question-detail">
      <header>
        <h3>{{ selected.title }}</h3>
        <button class="text-action" @click="emit('ask', selected.id)">追问当前问题</button>
      </header>
      <section class="judgment">
        <h3>当前答案</h3>
        <p v-if="selected.answer_status === 'needs_update'" class="muted">
          依据或解释已改变，当前答案待研究层重新评估。
        </p>
        <MarkdownRenderer
          v-if="selected.judgment && selected.answer_status !== 'needs_update'"
          :content="evidenceText(selected.judgment)"
          :work-id="snapshot?.work_id"
        />
        <p v-else-if="selected.answer_status !== 'needs_update'" class="muted">
          尚未形成判断。已有实验结果不代表该问题已回答。
        </p>
        <details v-if="selected.judgment && selected.answer_status === 'needs_update'">
          <summary>先前答案（等待重新评估）</summary>
          <MarkdownRenderer
            :content="evidenceText(selected.judgment)"
            :work-id="snapshot?.work_id"
          />
        </details>
      </section>
      <div v-if="selected.answer_status !== 'needs_update'" class="basis">
        <button
          v-for="result in basis"
          :key="result.id"
          class="text-action"
          @click="emit('object', { type: 'result', resultType: 'registered', id: result.id })"
        >
          {{ result.name || '查看依据' }}
        </button>
      </div>
      <p v-for="limitation in selected.limitations" :key="limitation" class="muted">
        {{ limitation }}
      </p>
      <section v-if="selected.issues.length || selected.next_step" class="next-step">
        <h3>未解决部分与下一步</h3>
        <p v-if="selected.next_step">{{ selected.next_step }}</p>
        <div v-for="issue in selected.issues" :key="issue.id" class="finding">
          <p>{{ issue.message }}</p>
          <p>{{ issue.next_step }}</p>
          <button @click="emit('issue', issue.id)">查看相关记录</button>
        </div>
      </section>
      <details v-if="previousJudgments.length">
        <summary>之前的判断</summary>
        <article v-for="(judgment, index) in previousJudgments" :key="index">
          <small>{{
            judgment.recorded_at
              ? new Date(judgment.recorded_at * 1000).toLocaleString()
              : '时间未记录'
          }}</small>
          <p>{{ judgment.statement }}</p>
          <p v-for="limit in judgment.limitations" :key="limit">{{ limit }}</p>
        </article>
      </details>
      <section>
        <h3>相关实验</h3>
        <article v-for="task in selectedExperiments" :key="task.task_id" class="experiment">
          <header>
            <strong>{{ task.title || '实验名称未记录' }}</strong
            ><span class="state">{{ taskLabel(taskState(task, status), true) }}</span>
          </header>
          <p v-if="task.availability === 'historical'" class="muted">当前显示保留的历史结果。</p>
          <MarkdownRenderer
            v-if="task.summary"
            class="experiment-summary"
            :content="evidenceText(task.summary)"
            :work-id="snapshot?.work_id"
          />
          <p v-else class="muted">{{ task.reason || '尚无已提交的实验报告。' }}</p>
          <button
            v-if="task.results.length || task.summary || task.artifacts.some((item) => item.path)"
            class="text-action"
            @click="emit('object', { type: 'result', resultType: 'experiment', id: task.task_id })"
          >
            查看实验结果与记录
          </button>
          <details v-if="task.experiment">
            <summary>方法与条件</summary>
            <p>{{ task.experiment.conditions }}</p>
            <p>{{ task.experiment.evaluation }}</p>
          </details>
        </article>
      </section>
    </article>
    <ResearchRuntime
      :snapshot="snapshot"
      :work-status="workStatus"
      :question-id="selected?.id"
      @pause="emit('pause')"
      @file="(path) => emit('object', { type: 'file', path })"
      @result="(id) => emit('object', { type: 'result', resultType: 'registered', id })"
    />
    <section class="delivery">
      <header>
        <h3>论文</h3>
        <button v-if="snapshot?.artifacts.length" @click="emit('object', { type: 'paper' })">
          打开论文
        </button>
      </header>
      <p v-if="snapshot?.artifacts.length">论文产物已保存，可按需打开。</p>
      <p v-else>论文产物尚不可用。</p>
      <p v-for="(stage, key) in snapshot?.delivery_stages" :key="key">
        {{ stageName(String(key)) }}：{{
          executionLabel(stage.status === 'in_progress' ? 'running' : stage.status)
        }}
      </p>
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ResearchRuntime from './ResearchRuntime.vue'
import type { ResearchSnapshot, ResearchQuestion } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
import {
  taskState,
  taskLabel,
  deliveryLabels,
  executionLabel,
  researchRunning,
} from '@/utils/researchProgress'
import { evidenceMarkdown } from '@/utils/researchEvidence'
const props = defineProps<{ snapshot: ResearchSnapshot | null; workStatus?: string }>()
const emit = defineEmits<{
  object: [object: WorkObject]
  issue: [id: string]
  ask: [id: string]
  pause: []
}>()
const selectedId = ref('')
const questions = computed(() => props.snapshot?.questions?.filter((q) => !q.retired) || [])
const selected = computed(
  () => questions.value.find((q) => q.id === selectedId.value) || questions.value[0],
)
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
const basis = computed(
  () => props.snapshot?.results.filter((r) => selected.value?.artifact_refs.includes(r.id)) || [],
)
const questionState = (question: ResearchQuestion) => {
  if (
    researchRunning(status.value) &&
    props.snapshot?.runtime?.actions?.some(
      (row) => row.question_id === question.id && row.status === 'running',
    )
  )
    return '正在执行'
  if (question.answer_status === 'needs_update') return '答案待更新'
  if (question.judgment) return '已有答案'
  return props.snapshot?.results.some((result) =>
    question.experiment_ids.includes(result.subproblem_id),
  )
    ? '已有结果，尚无判断'
    : '尚无判断'
}
const stageName = (key: string) =>
  deliveryLabels[key as keyof typeof deliveryLabels]?.[0] || '论文处理'
const evidenceText = (text: string) => evidenceMarkdown(text, props.snapshot)
</script>
<style scoped>
.research-progress {
  color: var(--pa-ink);
  font-size: 13px;
}
.overview h3 {
  margin-bottom: 6px;
}
.overview p,
.muted {
  color: var(--pa-muted);
}
.questions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 20px 0;
}
.questions button {
  display: flex;
  text-align: left;
  align-items: flex-start;
  gap: 12px;
  padding: 13px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  font-size: 14px;
  color: var(--pa-ink);
  cursor: pointer;
}
.questions button.selected {
  border-color: var(--pa-accent);
  background: var(--pa-surface-active);
}
.question-index {
  display: grid;
  place-items: center;
  background: var(--pa-surface-muted);
  width: 24px;
  height: 24px;
  border-radius: 6px;
  flex-shrink: 0;
  font-size: 12px;
}
.questions button > span:nth-child(2) {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.questions small {
  display: block;
  margin-top: 5px;
  color: var(--pa-muted);
  font-size: 12px;
}
.question-detail {
  border-top: 1px solid var(--pa-line);
  padding-top: 24px;
}
h3 {
  font-size: 14px;
  margin: 0 0 10px;
}
p {
  line-height: 1.8;
  margin: 8px 0;
}
section {
  margin-top: 24px;
}
.judgment {
  background: var(--pa-surface-muted);
  padding: 18px;
  border-radius: 10px;
}
.judgment small {
  color: var(--pa-muted);
}
.next-step {
  border-left: 3px solid var(--pa-accent);
  padding-left: 16px;
}
.experiment {
  border: 1px solid var(--pa-line);
  border-radius: 10px;
  padding: 18px;
  margin-top: 12px;
}
.experiment header,
.delivery header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.experiment header strong {
  max-width: 75%;
  line-height: 1.65;
}
.state {
  color: var(--pa-muted);
  font-size: 12px;
}
.experiment-summary {
  margin: 12px 0;
  line-height: 1.85;
}
.experiment-summary :deep(p) {
  margin: 10px 0;
}
.experiment-summary :deep(a) {
  color: var(--pa-accent);
  text-underline-offset: 3px;
}
.finding {
  background: var(--pa-surface-muted);
  padding: 12px;
  margin-top: 12px;
  border-radius: 6px;
}
.finding > strong {
  font-size: 12px;
  color: var(--pa-accent);
}
details {
  margin-top: 12px;
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
  font-size: 12px;
}
button.text-action,
.finding button,
.delivery button {
  border: 0;
  background: transparent;
  padding: 8px 0;
  color: var(--pa-accent);
  cursor: pointer;
}
.delivery {
  border-top: 1px solid var(--pa-line);
  padding: 24px 0;
}
.empty {
  color: var(--pa-muted);
  padding: 24px 0;
}
</style>
