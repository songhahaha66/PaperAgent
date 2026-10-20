<template>
  <article class="outcome">
    <header>
      <p>
        <span v-if="outcome.revision">方案版本 {{ outcome.revision }}</span>
      </p>
      <h2>{{ outcome.title }}</h2>
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
          ? '可查阅保留证据；受影响结果不能用于依赖计算或论文发布，须按现有问题流程处理。'
          : 'Retained evidence is readable. Affected results require the existing repair process before dependent computation or publication.'
      }}
    </p>
    <h3>{{ isZh ? '方案说明' : 'Method' }}</h3>
    <template v-if="outcome.method"
      ><h4>{{ outcome.method.name }}</h4>
      <p>{{ outcome.method.idea }}</p>
      <pre v-if="outcome.method.formulation">{{ outcome.method.formulation }}</pre>
      <ol v-if="outcome.method.steps?.length">
        <li v-for="step in outcome.method.steps" :key="step">{{ step }}</li>
      </ol>
      <p v-if="outcome.method.validation">{{ outcome.method.validation }}</p></template
    >
    <p v-else>
      {{ isZh ? '未登记方案说明；不从变量名称推断方法。' : 'No method description was recorded.' }}
    </p>
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
    <section v-for="result in outcome.results" :key="result.id" class="measurement">
      <h4>
        {{ result.name }}
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
        :active="true"
      />
      <p v-else-if="result.kind === 'figure'">
        {{ isZh ? '登记图表文件当前不可用。' : 'The registered figure is unavailable.' }}
      </p>
      <p v-else class="value">
        {{ displayValue(result.value, result.kind)
        }}<span v-if="result.unit"> {{ result.unit }}</span>
      </p>
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
          ? '未登记独立数据来源链接；计算记录可在各项成果中按需查看。'
          : 'No separate data source was recorded. Computation records are available with each result.'
      }}
    </p>
    <p v-if="outcome.sources.length" class="note">
      {{
        isZh
          ? '以下为本次尝试冻结的输入文件；单项成果与文件的精确对应未单独登记。'
          : 'These inputs were frozen for this attempt; per-result file attribution was not separately recorded.'
      }}
    </p>
    <div v-for="source in outcome.sources" :key="source.path">
      <button v-if="source.available" @click="emit('open-file', source.path)">
        {{ source.path }}
      </button>
      <p v-else>{{ source.path }} · {{ isZh ? '文件不可用' : 'Unavailable' }}</p>
      <details>
        <summary>{{ isZh ? '冻结来源哈希' : 'Frozen source hash' }}</summary>
        <code>{{ source.hash }}</code>
      </details>
    </div>
    <h3>{{ isZh ? '下一步与理由' : 'Next step and reason' }}</h3>
    <p>
      {{
        outcome.next_step ||
        (isZh ? '当前记录没有下一步决策或理由。' : 'No next-step decision or reason was recorded.')
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
import type { ResearchOutcome, ResearchSnapshot } from '@/types/research'
import { useStudioCopy } from '@/lang/studio'
import { outcomeLabel } from '@/utils/outcomeLabels'
import ResultSeries from './ResultSeries.vue'
import WorkDocument from './WorkDocument.vue'
defineProps<{ workId: string; outcome: ResearchOutcome; runs: ResearchSnapshot['runs'] }>()
const emit = defineEmits<{ 'open-file': [path: string]; issue: [id: string] }>()
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
