<template>
  <section class="research-materials">
    <header>
      <div>
        <h2>资料与结果</h2>
        <p>从输入材料到实测证据，按研究问题查看来源和去向。</p>
      </div>
      <button class="quiet" @click="load">刷新</button>
    </header>
    <nav class="views" aria-label="资料与结果分类">
      <button :class="{ selected: view === 'results' }" @click="view = 'results'">
        实测结果 <small>{{ snapshot?.results.length || 0 }}</small></button
      ><button :class="{ selected: view === 'sources' }" @click="view = 'sources'">
        原始资料与文献
      </button>
    </nav>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="view === 'results'">
      <select v-if="questions.length > 1" v-model="questionId" aria-label="按研究问题查看">
        <option value="">全部研究问题</option>
        <option v-for="q in questions" :key="q.id" :value="q.id">{{ q.title }}</option>
      </select>
      <p v-if="!outcomes.length" class="empty">还没有实测结果。执行中的方法可在研究进度里查看。</p>
      <article v-for="outcome in outcomes" :key="outcome.id" class="evidence-card">
        <header>
          <small>{{ questionFor(outcome.task_id)?.title || outcome.title }}</small
          ><span>{{
            outcome.availability === 'historical'
              ? '历史结果'
              : outcome.results.length
                ? '实测证据'
                : '尚无实测证据'
          }}</span>
        </header>
        <h3>
          {{
            outcome.experiment?.candidates.map((c) => c.name).join(' / ') ||
            outcome.approach ||
            outcome.method?.name ||
            outcome.title
          }}
        </h3>
        <p class="check">
          {{ outcomeLabel(outcome.checking, true)
          }}<span v-if="outcome.issues.length && outcome.next_step">
            · {{ outcome.next_step }}</span
          >
        </p>
        <div class="key-results">
          <button
            v-for="result in outcome.results.filter((r) => !r.figure_path).slice(0, 4)"
            :key="result.id"
            @click="openResult(result.id)"
          >
            <small>{{ resultName(result.name) }}</small
            ><strong>{{
              result.kind === 'series' ? '查看实测序列' : `${result.value} ${result.unit}`
            }}</strong>
          </button>
        </div>
        <button
          v-for="figure in outcome.results.filter((r) => r.figure_path).slice(0, 1)"
          :key="figure.id"
          class="figure"
          @click="openResult(figure.id)"
        >
          <WorkDocument
            :work-id="workId"
            :path="figure.figure_path!"
            :show-toolbar="false"
            :active="true"
          /><span>{{ figure.name }} · 查看来源与实验</span>
        </button>
        <p v-if="outcome.experiment" class="conditions">
          实验范围：{{ outcome.experiment.conditions }}<br />评价标准：{{
            outcome.experiment.evaluation
          }}
        </p>
        <footer>
          <button
            class="quiet"
            @click="emit('object', { type: 'result', resultType: 'task', id: outcome.id })"
          >
            查看完整实验 →</button
          ><small>{{ outcome.results.length }} 项结果 · {{ outcome.sources.length }} 份输入</small>
        </footer>
      </article>
      <details v-if="experiments.length" class="comparison">
        <summary>共同条件下的比较实验 · {{ experiments.length }}</summary>
        <button
          v-for="experiment in experiments"
          :key="experiment.id"
          class="resource"
          @click="emit('object', { type: 'result', resultType: 'experiment', id: experiment.id })"
        >
          <strong>{{ experiment.branches.map((b) => b.name).join(' / ') }}</strong
          ><span>{{ experiment.reason || '查看固定评价条件与实测结果' }}</span>
        </button>
      </details>
      <section v-if="snapshot?.artifacts.length">
        <h3>交付文件</h3>
        <button
          v-for="path in snapshot.artifacts"
          :key="path"
          class="resource"
          @click="emit('object', { type: 'file', path })"
        >
          {{ path.split('/').pop() }}
        </button>
      </section>
    </template>
    <section v-show="view === 'sources'" class="sources">
      <h3>研究输入</h3>
      <p v-if="!inputs.length" class="empty">
        当前未提供可打开的独立材料文件；题内数据与原始要求可在研究问题中查看。
      </p>
      <button
        v-for="path in inputs"
        :key="path"
        class="resource"
        @click="emit('object', { type: 'file', path })"
      >
        <strong>{{ path.split('/').pop() }}</strong
        ><span>{{ inputUse(path) }}</span>
      </button>
      <ResearchLiterature
        :work-id="workId"
        :disabled="disabled"
        :evidence-id="evidenceId"
        :refresh-key="snapshot?.commit_seq"
        @updated="emit('updated')"
        @cite="(id) => emit('cite', id)"
      />
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ResearchLiterature from './ResearchLiterature.vue'
import { resultName } from '@/utils/researchEvidence'
import WorkDocument from './WorkDocument.vue'
import { experimentAPI, type ComparisonRecord } from '@/api/experiments'
import type { ResearchSnapshot } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
import { researchQuestions } from '@/utils/researchProgress'
import { outcomeLabel } from '@/utils/outcomeLabels'
const props = defineProps<{
  workId: string
  snapshot: ResearchSnapshot | null
  disabled?: boolean
  evidenceId?: string
}>()
const emit = defineEmits<{ object: [object: WorkObject]; updated: []; cite: [id: string] }>()
const view = ref<'results' | 'sources'>('results'),
  questionId = ref('')
const questions = computed(() => researchQuestions(props.snapshot))
const questionFor = (id: string) => questions.value.find((q) => q.task_ids.includes(id))
const outcomes = computed(() =>
  (props.snapshot?.outcomes || []).filter(
    (o) => !questionId.value || questionFor(o.task_id)?.id === questionId.value,
  ),
)
const inputs = computed(() => [
  ...new Set([
    ...(props.snapshot?.inputs || []),
    ...(props.snapshot?.outcomes || []).flatMap((o) =>
      o.sources.filter((s) => s.available).map((s) => s.path),
    ),
  ]),
])
const inputUse = (path: string) => {
  const used = (props.snapshot?.outcomes || []).filter((o) =>
    o.sources.some((s) => s.path === path),
  )
  return used.length
    ? `用于 ${used.length} 项实验 · 每次执行保留输入版本`
    : '已提供，尚未用于已保存的实验'
}
const openResult = (id: string) => emit('object', { type: 'result', resultType: 'registered', id })
const experiments = ref<ComparisonRecord[]>([]),
  error = ref('')
let version = 0
async function load() {
  const request = ++version
  try {
    const result = await experimentAPI.read(props.workId)
    if (request === version) {
      experiments.value = result.comparisons
      error.value = ''
    }
  } catch (e) {
    if (request === version) error.value = e instanceof Error ? e.message : '实验记录暂不可读'
  }
}
watch(() => [props.workId, props.snapshot?.commit_seq], load, { immediate: true })
watch(
  () => props.evidenceId,
  (id) => {
    if (id) view.value = 'sources'
  },
  { immediate: true },
)
watch(
  () => props.workId,
  () => {
    questionId.value = ''
    view.value = props.evidenceId ? 'sources' : 'results'
  },
)
</script>
<style scoped>
.research-materials {
  padding: 28px;
  max-width: 1000px;
  margin: auto;
  color: var(--pa-ink);
  font-size: 13px;
}
header {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: flex-start;
}
h2 {
  font-size: 21px;
  margin: 0 0 8px;
}
header p,
.empty {
  color: var(--pa-muted);
  line-height: 1.8;
}
h3 {
  font-size: 16px;
  line-height: 1.6;
}
.views {
  display: flex;
  gap: 20px;
  border-bottom: 1px solid var(--pa-line);
  margin: 22px 0;
}
.views button {
  border: 0;
  border-bottom: 2px solid transparent;
  padding: 10px 0;
  background: transparent;
  color: var(--pa-muted);
  font: inherit;
  cursor: pointer;
}
.views button.selected {
  color: var(--pa-accent);
  border-color: var(--pa-accent);
}
small {
  font-size: 12px;
  color: var(--pa-muted);
}
select {
  max-width: 100%;
  padding: 8px;
  border: 1px solid var(--pa-line);
  border-radius: 6px;
  background: var(--pa-surface);
  color: var(--pa-ink);
}
.evidence-card {
  padding: 20px;
  border: 1px solid var(--pa-line);
  border-radius: 12px;
  margin: 16px 0;
}
.evidence-card header span {
  color: var(--pa-accent);
  white-space: nowrap;
  font-size: 12px;
}
.check,
.conditions {
  color: var(--pa-muted);
  line-height: 1.7;
}
.check span {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.key-results {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px;
}
.key-results button {
  border: 0;
  border-radius: 8px;
  background: var(--pa-surface-muted);
  padding: 12px;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--pa-ink);
  cursor: pointer;
}
.key-results strong {
  font-size: 17px;
  overflow-wrap: anywhere;
}
.figure {
  display: block;
  max-width: 100%;
  width: 100%;
  margin: 16px 0;
  border: 0;
  border-radius: 8px;
  background: var(--pa-surface-muted);
  padding: 12px;
  cursor: pointer;
  color: var(--pa-muted);
}
.figure :deep(.work-document) {
  max-height: 230px;
  overflow: hidden;
}
.figure :deep(img) {
  max-height: 210px;
  object-fit: contain;
}
.figure span {
  display: block;
  font-size: 12px;
  padding: 8px;
}
.evidence-card footer {
  border-top: 1px solid var(--pa-line);
  padding-top: 12px;
  margin-top: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.quiet {
  border: 0;
  background: transparent;
  color: var(--pa-accent);
  font: inherit;
  cursor: pointer;
  font-size: 12px;
}
.resource {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: left;
  padding: 14px;
  margin: 10px 0;
  width: 100%;
  border: 1px solid var(--pa-line);
  border-radius: 8px;
  background: var(--pa-surface);
  color: var(--pa-ink);
  cursor: pointer;
}
.resource span {
  font-size: 12px;
  color: var(--pa-muted);
}
.comparison {
  margin: 24px 0;
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
}
.sources > h3 {
  margin-top: 24px;
}
</style>
