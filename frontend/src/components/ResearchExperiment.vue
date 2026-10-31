<template>
  <article class="experiment">
    <header>
      <small v-if="question">研究问题：{{ question.title }}</small>
      <h2>{{ experiment.title }}</h2>
      <small v-if="experiment.started_at">实验开始于 {{ startedAt }}</small>
    </header>
    <p class="experiment-status">{{ statusText }}</p>
    <p v-if="experiment.availability === 'historical'" role="alert">此处保留的是历史结果。</p>
    <MarkdownRenderer
      v-if="experiment.approach || experiment.summary"
      :content="evidenceMarkdown(experiment.summary || experiment.approach || '', snapshot || null)"
      :work-id="workId"
    />
    <section>
      <h3>实验目的</h3>
      <p>{{ experiment.objective }}</p>
      <p v-if="experiment.experiment">条件：{{ experiment.experiment.conditions }}</p>
      <p v-if="experiment.experiment">评价依据：{{ experiment.experiment.evaluation }}</p>
    </section>
    <h3 v-if="experiment.claims.length">主要发现与解释</h3>
    <div v-for="claim in experiment.claims" :key="claim.id">
      <MarkdownRenderer
        :content="evidenceMarkdown(claim.statement, snapshot || null)"
        :work-id="workId"
      />
      <small>{{ claim.strength }}</small>
    </div>
    <p v-for="limitation in experiment.record?.limitations || []" :key="limitation">
      局限：{{ limitation }}
    </p>
    <h3 v-if="primaryResults.length">关键依据</h3>
    <ResearchMeasurement
      v-for="result in primaryResults"
      :key="result.id"
      :result="result"
      :work-id="workId"
      :runs="runs"
      :snapshot="snapshot"
      :focus-ref="focusRef"
      @open-file="(path) => emit('open-file', path)"
      @paper="(use) => emit('paper', use)"
    />
    <details v-if="otherResults.length" :open="otherResults.some((r) => r.id === focusRef)">
      <summary>全部登记与辅助结果（{{ otherResults.length }} 项）</summary>
      <ResearchMeasurement
        v-for="result in otherResults"
        :key="result.id"
        :result="result"
        :work-id="workId"
        :runs="runs"
        :snapshot="snapshot"
        :focus-ref="focusRef"
        @open-file="(path) => emit('open-file', path)"
        @paper="(use) => emit('paper', use)"
      />
    </details>
    <section v-if="actions.length">
      <h3>当前行动与执行记录</h3>
      <article v-for="action in actions.slice(-1)" :key="action.id" class="action-record">
        <p>
          <strong>{{ action.label }}</strong
          ><span class="note"> {{ executionLabel(action.status) }}</span>
        </p>
        <p v-if="action.purpose">{{ action.purpose }}</p>
        <p v-if="action.message">{{ action.error_type ? '本次行动未完成，具体诊断见调试详情。' : action.message }}</p>
        <details>
          <summary>调试详情</summary>
          <p v-if="action.error_type">{{ action.error_type }}</p>
          <pre v-if="action.error_type && action.message">{{ action.message }}</pre>
          <pre v-if="action.inputs">{{ JSON.stringify(action.inputs, null, 2) }}</pre>
          <pre v-if="action.output">{{ JSON.stringify(action.output, null, 2) }}</pre>
          <template v-for="run in runs.filter((r) => r.id === action.run_id)" :key="run.id">
            <button v-if="run.input_path" @click="emit('open-file', run.input_path)">
              代码与工具输入
            </button>
            <button v-if="run.stdout_path" @click="emit('open-file', run.stdout_path)">
              实际输出与错误
            </button>
          </template>
        </details>
      </article>
    </section>
    <details v-if="actions.length > 1">
      <summary>先前行动</summary>
      <article v-for="action in actions.slice(0, -1)" :key="action.id">
        <p>{{ action.label }} {{ executionLabel(action.status) }}</p>
        <p v-if="action.message">{{ action.error_type ? '本次行动未完成，具体诊断见调试详情。' : action.message }}</p>
        <details><summary>调试详情</summary><pre>{{ JSON.stringify({ inputs: action.inputs, output: action.output, error_type: action.error_type, message: action.message }, null, 2) }}</pre></details>
      </article>
    </details>
    <details
      v-if="experiment.interpretation_history?.length || experiment.historical_claims?.length"
    >
      <summary>解释与观点的历史（已被当前解释替代）</summary>
      <article v-for="prior in experiment.interpretation_history || []" :key="prior.report_ref">
        <MarkdownRenderer
          :content="evidenceMarkdown(prior.report?.summary || '', snapshot || null)"
          :work-id="workId"
        />
        <p v-for="finding in prior.report?.key_findings || []" :key="finding.statement">
          {{ finding.statement }}
        </p>
      </article>
      <p v-for="claim in experiment.historical_claims || []" :key="claim.id">
        {{ claim.status }}：{{ claim.statement }}（{{ claim.reason }}）
      </p>
    </details>
    <details v-if="experiment.method">
      <summary>{{ isZh ? '最初的方法设想' : 'Initial method proposal' }}</summary>
      <MarkdownRenderer
        :content="
          [
            experiment.method.name,
            experiment.method.idea,
            experiment.method.formulation,
            ...(experiment.method.steps || []),
            experiment.method.validation,
          ]
            .filter(Boolean)
            .join('\n\n')
        "
        :work-id="workId"
      />
    </details>
    <details v-if="experiment.experiment">
      <summary>{{ isZh ? '方法、数据与评价标准' : 'Method, data and evaluation' }}</summary>
      <template v-if="experiment.experiment.candidates.length > 1">
        <p v-for="candidate in experiment.experiment.candidates" :key="candidate.name">
          <strong>{{ candidate.name }}</strong
          >：{{ candidate.instruction }}
        </p>
      </template>
      <p>{{ isZh ? '实验条件：' : 'Conditions: ' }}{{ experiment.experiment.conditions }}</p>
      <p>{{ isZh ? '评价依据：' : 'Evaluation: ' }}{{ experiment.experiment.evaluation }}</p>
    </details>
    <details v-if="fileGroups.length" class="experiment-files">
      <summary>
        {{ isZh ? '代码与运行文件' : 'Code and execution files' }}，{{ fileGroups.length }} 组
      </summary>
      <div v-for="(group, index) in fileGroups" :key="group.id" class="file-group">
        <span
          >{{ group.run ? `第 ${index + 1} 次运行` : '实验记录'
          }}<small v-if="group.status">，{{ group.status }}</small></span
        >
        <button
          v-for="artifact in group.files"
          :key="artifact.id"
          @click="emit('open-file', artifact.path!)"
        >
          {{ fileLabel(artifact.kind) }}
        </button>
      </div>
    </details>
    <h3 v-if="experiment.sources.length">{{ isZh ? '输入资料' : 'Sources' }}</h3>
    <div v-for="source in experiment.sources" :key="source.path">
      <button v-if="source.available" @click="emit('open-file', source.path)">
        {{ source.path }}
      </button>
      <p v-else>{{ source.path }}，{{ isZh ? '文件不可用' : 'Unavailable' }}</p>
    </div>
    <section v-if="question?.next_step">
      <h3>{{ isZh ? '下一步' : 'Next step' }}</h3>
      <p>{{ question.next_step }}</p>
    </section>
    <details v-if="experiment.issues.length">
      <summary>核查意见与故障历史</summary>
      <h3>{{ isZh ? '本次实验的问题' : 'Findings' }}</h3>
      <p v-for="issue in experiment.issues" :key="issue.id">
        {{
          issue.category === 'service'
            ? isZh
              ? '检查服务故障：'
              : 'Check service failure: '
            : ['suspected', 'inconclusive'].includes(issue.status)
              ? isZh
                ? '待核实意见：'
                : 'Finding to verify: '
              : ''
        }}{{ issue.message
        }}<button @click="emit('issue', issue.id)">
          {{ isZh ? '查看详情' : 'Details' }}
        </button>
      </p>
    </details>
    <details v-if="experiment.history.length">
      <summary>
        {{ isZh ? '保留的历史成果' : 'Retained history' }} ({{ experiment.history.length }})
      </summary>
      <section v-for="previous in experiment.history" :key="previous.attempt_id">
        <p>较早的执行记录</p>
        <p v-for="item in previous.results" :key="item.id">
          {{ item.name || '未命名结果' }}：{{ item.value }} {{ item.unit
          }}<button v-if="item.figure_path" @click="emit('open-file', item.figure_path)">
            {{ isZh ? '图表' : 'Figure' }}
          </button>
        </p>
      </section>
    </details>
  </article>
</template>
<script setup lang="ts">
import { computed, nextTick, watch } from 'vue'
import type {
  ResearchExperiment as Experiment,
  ResearchSnapshot,
  EvidenceUse,
} from '@/types/research'
import { useStudioCopy } from '@/lang/studio'
import { executionLabel } from '@/utils/researchProgress'
import { outcomeLabel } from '@/utils/outcomeLabels'
import ResearchMeasurement from './ResearchMeasurement.vue'
import { evidenceMarkdown } from '@/utils/researchEvidence'
import MarkdownRenderer from './MarkdownRenderer.vue'
const props = defineProps<{
  workId: string
  experiment: Experiment
  runs: ResearchSnapshot['runs']
  snapshot?: ResearchSnapshot | null
  focusRef?: string
}>()
const emit = defineEmits<{
  'open-file': [path: string]
  issue: [id: string]
  paper: [use: EvidenceUse]
  experiment: [id: string]
}>()
const startedAt = computed(() => {
  const raw = props.experiment.started_at
  if (!raw) return ''
  return new Date(typeof raw === 'number' ? raw * 1000 : raw).toLocaleString('zh-CN')
})
const question = computed(() =>
  props.snapshot?.questions?.find((q) => q.id === props.experiment.question_id),
)
const primaryResults = computed(() =>
  props.experiment.results.filter(
    (r) => props.experiment.primary_result_refs?.includes(r.id) && r.meaning,
  ),
)
const otherResults = computed(() =>
  props.experiment.results.filter(
    (r) => !primaryResults.value.some((primary) => primary.id === r.id),
  ),
)
const actions = computed(() => props.experiment.current_actions || [])
const statusText = computed(() => {
  const e = props.experiment
  if (e.execution_status === 'running') return '正在执行，尚未提交本次实验结果。'
  if (e.execution_status === 'failed')
    return e.results.length
      ? '本次实验未完成，已有结果已保留。'
      : '本次实验未完成，代码与运行输出已保留。'
  if (e.execution_status === 'succeeded') return `已完成，${e.results.length} 项结果，${label(e.checking)}`
  return `${label(e.computation)}，${label(e.checking)}`
})
const fileLabel = (kind: string) =>
  ({ code: '代码', stdout: '输出', stderr: '错误输出', data: '生成数据', report: '实验报告' })[
    kind
  ] || '文件'
const fileGroups = computed(() => {
  const groups = new Map<
    string,
    { id: string; run: boolean; status: string; files: Experiment['artifacts'] }
  >()
  for (const artifact of props.experiment.artifacts.filter((a) => a.path)) {
    const id = artifact.run_id || 'experiment'
    if (!groups.has(id)) {
      const status = props.runs.find((r) => r.id === artifact.run_id)?.status
      groups.set(id, {
        id,
        run: !!artifact.run_id,
        status: status === 'success' ? '成功' : status === 'failed' ? '失败' : '',
        files: [],
      })
    }
    groups.get(id)!.files.push(artifact)
  }
  return [...groups.values()]
})
watch(
  () => [props.focusRef, props.experiment] as const,
  async () => {
    await nextTick()
    if (props.focusRef)
      Array.from(document.querySelectorAll<HTMLElement>('[data-result]'))
        .find((el) => el.dataset.result === props.focusRef)
        ?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  },
  { immediate: true },
)
const { isZh } = useStudioCopy()
const label = (value: string) => outcomeLabel(value, isZh.value)
</script>
<style scoped>
.experiment-status {
  color: var(--pa-muted);
  margin: 12px 0 20px;
}
.follow-up {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px;
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface-active);
}
.follow-up button {
  margin-left: auto;
}
.file-group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 0;
  border-bottom: 1px solid var(--pa-line);
}
.file-group > span {
  flex: 1;
  min-width: 100px;
}
.experiment-files {
  margin-top: 24px;
}
header > small:first-child {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.focused {
  position: relative;
  padding-left: 16px;
}
.focused::before {
  content: '';
  position: absolute;
  left: 0;
  top: 18px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pa-accent);
}
.paper-uses {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--pa-muted);
  gap: 8px;
}
.experiment {
  padding: 24px;
  color: var(--pa-ink);
  max-width: 980px;
  margin: auto;
  overflow-wrap: anywhere;
}
h2 {
  font-size: 22px;
}
h3 {
  margin-top: 28px;
  font-size: 15px;
}
h4 {
  margin: 10px 0;
}
p,
li {
  line-height: 1.8;
  font-size: 15px;
}
dt,
small,
.note {
  font-size: 13px;
  color: var(--pa-muted);
}
.measurement {
  border-bottom: 1px solid var(--pa-line);
  padding: 12px 0 18px;
}
.value {
  font-size: 22px;
}
.value > span {
  margin-left: 6px;
}
pre,
code {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
details {
  margin-top: 12px;
  font-size: 13px;
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
}
button {
  margin: 4px;
  padding: 6px 10px;
  background: var(--pa-surface);
  color: inherit;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  cursor: pointer;
}
[role='alert'] {
  color: var(--pa-warning);
}
</style>
