<template>
  <article class="outcome">
    <header>
      <small v-if="question">研究问题 · {{ question.title }}</small>
      <h2>{{ outcome.title }}</h2>
      <small v-if="outcome.started_at">实验开始于 {{ startedAt }}</small>
    </header>
    <dl class="states">
      <div>
        <dt>{{ isZh ? '计算' : 'Computation' }}</dt>
        <dd>
          {{
            outcome.computation === 'failed'
              ? isZh
                ? '计算失败'
                : 'Computation failed'
              : label(outcome.computation)
          }}
        </dd>
      </div>
      <div>
        <dt>{{ isZh ? '成果' : 'Availability' }}</dt>
        <dd>{{ label(outcome.availability) }}</dd>
      </div>
      <div>
        <dt>{{ isZh ? '检查' : 'Checks' }}</dt>
        <dd>{{ label(outcome.checking) }}</dd>
      </div>
    </dl>
    <p v-if="outcome.availability === 'historical' || outcome.checking === 'failed'" role="alert">
      {{
        isZh
          ? '这是历史结果，仍需核查。'
          : 'Retained evidence is readable. Affected results require the existing repair process before dependent computation or publication.'
      }}
    </p>
    <h3>{{ isZh ? '实际执行' : 'Actual execution' }}</h3>
    <MarkdownRenderer
      :content="
        outcome.approach ||
        outcome.summary ||
        (isZh ? '尚无执行说明。' : 'No execution description recorded.')
      "
      :work-id="workId"
    />
    <details v-if="outcome.method">
      <summary>{{ isZh ? '最初的方法设想' : 'Initial method proposal' }}</summary>
      <MarkdownRenderer
        :content="
          [
            outcome.method.name,
            outcome.method.idea,
            outcome.method.formulation,
            ...(outcome.method.steps || []),
            outcome.method.validation,
          ]
            .filter(Boolean)
            .join('\n\n')
        "
        :work-id="workId"
      />
    </details>
    <section v-if="outcome.experiment">
      <h3>{{ isZh ? '本次试验' : 'This experiment' }}</h3>
      <template v-if="outcome.experiment.candidates.length > 1">
        <p v-for="candidate in outcome.experiment.candidates" :key="candidate.name">
          <strong>{{ candidate.name }}</strong
          >：{{ candidate.instruction }}
        </p>
      </template>
      <p>{{ isZh ? '实验条件：' : 'Conditions: ' }}{{ outcome.experiment.conditions }}</p>
      <p>{{ isZh ? '评价依据：' : 'Evaluation: ' }}{{ outcome.experiment.evaluation }}</p>
    </section>
    <h3>{{ isZh ? '图表与指标' : 'Figures and measurements' }}</h3>
    <p v-if="!outcome.results.length">
      {{ isZh ? '尚未登记可读成果。' : 'No results recorded yet.' }}
    </p>
    <section
      v-for="result in outcome.results"
      :key="result.id"
      class="measurement"
      :data-result="result.id"
      :class="{ focused: focusRef === result.id }"
    >
      <h4>
        {{ resultName(result.name) }}
      </h4>
      <p v-if="result.description">{{ result.description }}</p>
      <ResultSeries
        v-if="result.series?.length"
        :values="result.series"
        :label="result.name + (result.unit ? ' (' + result.unit + ')' : '')"
        :axis-label="
          isZh ? '登记顺序（未登记横轴含义）' : 'Recorded order (axis meaning unavailable)'
        "
      />
      <WorkDocument
        v-else-if="result.figure_path"
        :work-id="workId"
        :path="result.figure_path"
        :show-toolbar="false"
        :active="true"
      />
      <p v-else-if="result.kind === 'figure'">
        {{ isZh ? '登记图表文件当前不可用。' : 'The registered figure is unavailable.' }}
      </p>
      <p v-else class="value">
        {{ displayValue(result.value, result.kind)
        }}<span v-if="result.unit"> {{ result.unit }}</span>
      </p>
      <div v-if="usesFor(result.id).length" class="paper-uses">
        <span>用于正文</span
        ><button v-for="use in usesFor(result.id)" :key="use.section" @click="emit('paper', use)">
          {{ use.title }} ↗
        </button>
      </div>
      <details>
        <summary>{{ isZh ? '原始记录与计算来源' : 'Raw record and computation' }}</summary>
        <p>{{ isZh ? '结果引用：' : 'Result reference: ' }}{{ result.id }}</p>
        <pre>{{ result.value }}<span v-if="result.unit"> {{ result.unit }}</span></pre>
        <p>{{ result.run_id || (isZh ? '未登记运行编号' : 'No run recorded') }}</p>
        <template v-for="run in runs.filter((r) => r.id === result.run_id)" :key="run.id"
          ><button v-if="run.input_path" @click="emit('open-file', run.input_path)">
            {{ isZh ? '执行代码' : 'Code' }}</button
          ><button v-if="run.stdout_path" @click="emit('open-file', run.stdout_path)">
            {{ isZh ? '执行输出' : 'Output' }}
          </button></template
        >
      </details>
    </section>
    <h3>{{ isZh ? '结论' : 'Conclusions' }}</h3>
    <p v-if="!outcome.claims.length">
      {{ isZh ? '尚未登记有证据关联的结论。' : 'No evidence-linked conclusions recorded.' }}
    </p>
    <div v-for="claim in outcome.claims" :key="claim.id">
      <p>{{ claim.statement }}</p>
      <small>{{ claim.strength }} · {{ claim.evidence.join(', ') }}</small>
    </div>
    <h3>{{ isZh ? '来源' : 'Sources' }}</h3>
    <p v-if="!outcome.sources.length">
      {{
        isZh
          ? '未记录独立资料文件。'
          : 'No separate data source was recorded. Computation records are available with each result.'
      }}
    </p>
    <p v-if="outcome.sources.length" class="note">
      {{
        isZh
          ? '本次实验使用的输入文件。'
          : 'These inputs were frozen for this attempt; per-result file attribution was not separately recorded.'
      }}
    </p>
    <div v-for="source in outcome.sources" :key="source.path">
      <button v-if="source.available" @click="emit('open-file', source.path)">
        {{ source.path }}
      </button>
      <p v-else>{{ source.path }} · {{ isZh ? '文件不可用' : 'Unavailable' }}</p>
      <details>
        <summary>{{ isZh ? '文件版本' : 'Frozen source hash' }}</summary>
        <code>{{ source.hash }}</code>
      </details>
    </div>
    <h3>{{ isZh ? '后续安排' : 'Next step and reason' }}</h3>
    <p>
      {{
        outcome.next_step ||
        (isZh ? '暂无后续安排。' : 'No next-step decision or reason was recorded.')
      }}
    </p>
    <section v-if="outcome.issues.length">
      <h3>{{ isZh ? '待处理事项' : 'Findings' }}</h3>
      <p v-for="issue in outcome.issues" :key="issue.id">
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
          {{ isZh ? '查看意见与原文' : 'View finding and text' }}
        </button>
      </p>
    </section>
    <details v-if="outcome.history.length">
      <summary>
        {{ isZh ? '保留的历史成果' : 'Retained history' }} ({{ outcome.history.length }})
      </summary>
      <section v-for="previous in outcome.history" :key="previous.attempt_id">
        <p>v{{ previous.revision }} · {{ previous.attempt_id }}</p>
        <p v-for="item in previous.results" :key="item.id">
          {{ item.id }} · {{ item.name }} · {{ item.value }} {{ item.unit
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
import type { ResearchOutcome, ResearchSnapshot, EvidenceUse } from '@/types/research'
import { useStudioCopy } from '@/lang/studio'
import { outcomeLabel } from '@/utils/outcomeLabels'
import ResultSeries from './ResultSeries.vue'
import { resultName } from '@/utils/researchEvidence'
import WorkDocument from './WorkDocument.vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
const props = defineProps<{
  workId: string
  outcome: ResearchOutcome
  runs: ResearchSnapshot['runs']
  snapshot?: ResearchSnapshot | null
  focusRef?: string
}>()
const emit = defineEmits<{
  'open-file': [path: string]
  issue: [id: string]
  paper: [use: EvidenceUse]
}>()
const startedAt = computed(() => {
  const raw = props.outcome.started_at
  if (!raw) return ''
  return new Date(typeof raw === 'number' ? raw * 1000 : raw).toLocaleString('zh-CN')
})
const question = computed(() =>
  props.snapshot?.questions?.find((q) => q.task_ids.includes(props.outcome.task_id)),
)
const usesFor = (ref: string) => props.snapshot?.evidence_uses?.filter((u) => u.ref === ref) || []
watch(
  () => [props.focusRef, props.outcome] as const,
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
const displayValue = (value: string, kind: string) => {
  const number = Number(value)
  return kind === 'number' && value.trim() && Number.isFinite(number)
    ? number.toLocaleString(isZh.value ? 'zh-CN' : 'en-US', { maximumSignificantDigits: 6 })
    : value
}
const label = (value: string) => outcomeLabel(value, isZh.value)
</script>
<style scoped>
.focused {
  border-left: 3px solid var(--pa-accent);
  padding-left: 16px !important;
}
.paper-uses {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--pa-muted);
  gap: 8px;
}
.outcome {
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
  font-size: 16px;
}
h4 {
  margin: 10px 0;
}
p,
li {
  line-height: 1.8;
  font-size: 14px;
}
.states {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}
.states div {
  padding: 12px;
  background: var(--pa-surface-muted);
  border-radius: var(--pa-radius-card);
}
dt,
small,
.note {
  font-size: 12px;
  color: var(--pa-muted);
}
dd {
  margin: 6px 0 0;
  font-size: 13px;
}
.measurement {
  border-bottom: 1px solid var(--pa-line);
  padding: 12px 0 18px;
}
.value {
  font-size: 21px;
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
  font-size: 12px;
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
