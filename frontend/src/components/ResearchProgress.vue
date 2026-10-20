<template>
  <section class="research-progress" aria-label="研究问题与证据">
    <header class="overview">
      <div>
        <h3>研究问题</h3>
        <p>{{ questions.length }} 个问题 · {{ snapshot?.results.length || 0 }} 项实测结果</p>
      </div>
    </header>
    <p v-if="!questions.length" class="empty">尚未建立研究问题。</p>
    <nav v-else class="questions" aria-label="选择研究问题">
      <button
        v-for="(question, index) in questions"
        :key="question.id"
        :class="{ selected: selected?.id === question.id }"
        @click="selectedId = question.id"
      >
        <span class="question-index">{{ index + 1 }}</span
        ><span
          >{{ question.title }}<small>{{ questionState(question) }}</small></span
        >
      </button>
    </nav>
    <article v-if="selected" class="question-detail">
      <header>
        <h2>{{ selected.title }}</h2>
        <details v-if="selected.question !== selected.title">
          <summary>研究目标</summary>
          <p>{{ selected.question }}</p>
        </details>
      </header>
      <details v-if="selected.sources.length" class="source">
        <summary>对应原题</summary>
        <blockquote v-for="source in selected.sources" :key="source">{{ source }}</blockquote>
      </details>
      <section class="judgment">
        <h3>当前结论</h3>
        <MarkdownRenderer
          v-if="selected.judgment"
          :content="evidenceText(selected.judgment)"
          :work-id="snapshot?.work_id"
        />
        <p v-else>实验结果尚不足以形成判断。</p>
        <small
          v-if="
            selected.judgment &&
            selectedExperiments.some((t) => experimentFor(t.task_id)?.checking !== 'passed')
          "
          >结论仍在核查。</small
        >
      </section>
      <div v-if="selected.artifact_refs.length" class="basis">
        <button
          v-for="ref in selected.artifact_refs"
          :key="ref"
          @click="emit('object', { type: 'result', resultType: 'registered', id: ref })"
        >
          {{ snapshot?.results.find((r) => r.id === ref)?.name || '查看依据' }}
        </button>
      </div>
      <p v-for="limitation in selected.limitations" :key="limitation" class="muted">
        {{ limitation }}
      </p>
      <details v-if="selected.judgments.length > 1">
        <summary>之前的判断</summary>
        <p v-for="(judgment, index) in selected.judgments.slice(0, -1)" :key="index">
          {{ judgment.statement }}
        </p>
      </details>
      <section v-if="selected.next_step || decision" class="next-step">
        <h3>后续安排</h3>
        <p>{{ selected.next_step || decision?.reason }}</p>
        <p v-if="paused">研究已暂停。点击“继续”后，系统从保存的执行位置恢复。</p>
      </section>
      <section>
        <h3>实验记录</h3>
        <article
          v-for="task in selectedExperiments"
          :key="task.task_id"
          class="experiment"
          :data-task="task.task_id"
        >
          <header>
            <strong>{{ methodName(task.task_id) }}</strong
            ><span class="state">{{ taskLabel(taskState(task, status), true) }}</span>
          </header>
          <p v-if="experimentFor(task.task_id)?.experiment" class="conditions">
            {{ experimentFor(task.task_id)?.experiment?.conditions }}<br />评价：{{
              experimentFor(task.task_id)?.experiment?.evaluation
            }}
          </p>
          <div v-if="experimentFor(task.task_id)?.results.length" class="measurements">
            <button
              v-for="result in experimentFor(task.task_id)!.results.slice(0, 4)"
              :key="result.id"
              @click="emit('object', { type: 'result', resultType: 'registered', id: result.id })"
            >
              <small>{{ resultName(result.name) }}</small
              ><strong>{{
                result.kind === 'figure'
                  ? '查看图表'
                  : result.kind === 'series'
                    ? '查看实测序列'
                    : `${result.value} ${result.unit}`
              }}</strong>
            </button>
          </div>
          <p v-else class="muted">
            {{ task.progress.next_step || task.reason || '尚未取得实测结果。' }}
          </p>
          <p class="review-status" v-if="experimentFor(task.task_id)">
            证据检查：{{ outcomeLabel(experimentFor(task.task_id)!.checking, true) }}
          </p>
          <div v-for="issue in issuesFor(task.task_id)" :key="issue.id" class="finding">
            <strong>待核查</strong>
            <p>{{ issue.next_step }}</p>
            <details>
              <summary>问题详情</summary>
              <p>{{ issue.message }}</p>
              <button @click="emit('issue', issue.id)">查看相关记录</button>
            </details>
          </div>
          <button
            v-if="
              experimentFor(task.task_id)?.results.length || experimentFor(task.task_id)?.summary
            "
            class="text-action"
            @click="emit('object', { type: 'result', resultType: 'experiment', id: task.task_id })"
          >
            查看实验 →
          </button>
          <details v-if="task.progress.executions">
            <summary>执行记录</summary>
            <p>
              {{ task.progress.executions }} 次执行尝试<span v-if="task.kind === 'verify'">
                · 独立核查</span
              >。
            </p>
            <p v-if="task.dependencies.length">
              依赖：{{ task.dependencies.map(taskTitle).join('、') }}
            </p>
          </details>
        </article>
      </section>
    </article>
    <section v-if="snapshot?.delivery" class="delivery">
      <header>
        <h3>草稿与交付</h3>
        <button @click="emit('object', { type: 'paper' })">打开草稿 →</button>
      </header>
      <p v-if="!Object.keys(snapshot.delivery_stages || {}).length">草稿尚未生成。</p>
      <p v-for="(stage, key) in snapshot.delivery_stages" :key="key">
        {{ stageName(String(key)) }} · {{ stageStatus(stage.status) }}
      </p>
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import type { ResearchSnapshot, ResearchQuestion } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
import { researchQuestions, taskState, taskLabel, deliveryLabels } from '@/utils/researchProgress'
import { outcomeLabel } from '@/utils/outcomeLabels'
import { evidenceMarkdown, resultName } from '@/utils/researchEvidence'
const props = defineProps<{ snapshot: ResearchSnapshot | null; workStatus?: string }>()
const emit = defineEmits<{ object: [object: WorkObject]; issue: [id: string] }>()
const selectedId = ref('')
const questions = computed(() => researchQuestions(props.snapshot))
const selected = computed(
  () =>
    questions.value.find((q) => q.id === selectedId.value) ||
    questions.value.find((q) =>
      props.snapshot?.experiments?.some(
        (t) => q.experiment_ids.includes(t.task_id) && t.execution_status === 'running',
      ),
    ) ||
    questions.value[0],
)
const selectedExperiments = computed(
  () =>
    props.snapshot?.experiments?.filter(
      (t) =>
        selected.value?.experiment_ids.includes(t.task_id) ||
        (t.kind === 'verify' && t.question_id === selected.value?.id),
    ) || [],
)
const status = computed(() => props.workStatus || props.snapshot?.work_status || '')
const paused = computed(() => ['paused', 'failed', 'cancelled'].includes(status.value))
const decision = computed(() =>
  props.snapshot?.runtime?.decisions.filter((d) => d.question_id === selected.value?.id).at(-1),
)
const experimentFor = (id: string) => props.snapshot?.experiments?.find((o) => o.task_id === id)
const methodName = (id: string) =>
  experimentFor(id)
    ?.experiment?.candidates.map((c) => c.name)
    .join(' / ') ||
  experimentFor(id)?.approach ||
  experimentFor(id)?.method?.name ||
  taskTitle(id)
const taskTitle = (id: string) =>
  props.snapshot?.experiments?.find((t) => t.task_id === id)?.title || '前置研究'
const issuesFor = (id: string) =>
  selected.value?.issues.filter((i) =>
    props.snapshot?.feedback?.some((f) => f.id === i.id && f.task_id === id),
  ) || []
const questionState = (question: ResearchQuestion) => {
  const tasks =
    props.snapshot?.experiments?.filter((t) => question.experiment_ids.includes(t.task_id)) || []
  if (tasks.some((t) => t.execution_status === 'running') && !paused.value) return '正在获取证据'
  if (question.judgment) return '已有判断'
  return tasks.length && tasks.some((t) => t.acceptance === 'accepted' && t.validity === 'current')
    ? '已有结果'
    : '研究中'
}
const stageName = (key: string) =>
  deliveryLabels[key as keyof typeof deliveryLabels]?.[0] || '论文整理'
const stageStatus = (status: string) =>
  ({
    pending: '待执行',
    in_progress: '进行中',
    completed: '完成',
    blocked: '未完成，位置已保存',
    interrupted: '已中断',
  })[status] || status
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
.muted,
.conditions,
.review-status {
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
.question-detail > header small {
  color: var(--pa-accent);
}
h2 {
  font-size: 19px;
  line-height: 1.6;
  margin: 8px 0 12px;
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
.source {
  margin: 10px 0 20px;
}
blockquote {
  margin: 10px 0;
  padding: 0 14px;
  border-left: 2px solid var(--pa-line);
  white-space: pre-wrap;
  line-height: 1.8;
  color: var(--pa-muted);
  max-height: 230px;
  overflow: auto;
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
.measurements {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px;
  margin: 14px 0;
}
.measurements button {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: left;
  border: 0;
  border-radius: 8px;
  background: var(--pa-surface-muted);
  padding: 12px;
  color: var(--pa-ink);
  cursor: pointer;
}
.measurements strong {
  font-size: 16px;
  overflow-wrap: anywhere;
}
.measurements small {
  color: var(--pa-muted);
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
