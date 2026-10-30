<template>
  <section
    class="measurement"
    :data-result="result.id"
    :class="{ focused: focusRef === result.id }"
  >
    <h4>
      {{ result.meaning?.statistic || result.name }}
    </h4>
    <dl v-if="result.meaning">
      <dt>对象与范围</dt>
      <dd>{{ result.meaning.object }}；{{ result.meaning.scope }}</dd>
      <dt>统计口径</dt>
      <dd>{{ result.meaning.statistic }}</dd>
      <dt v-if="result.meaning.conditions">条件</dt>
      <dd v-if="result.meaning.conditions">{{ result.meaning.conditions }}</dd>
    </dl>
    <p v-if="result.description">{{ result.description }}</p>
    <ResultSeries
      v-if="result.series?.length"
      :values="result.series"
      :label="
        (result.meaning?.statistic || result.name) + (result.unit ? ' (' + result.unit + ')' : '')
      "
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
      <p v-if="!result.meaning">未登记统计对象与范围，不能仅凭名称或数值判断与其他结果同口径。</p>
      <pre>{{ result.value }}<span v-if="result.unit"> {{ result.unit }}</span></pre>
      <template v-for="run in runs.filter((r) => r.id === result.run_id)" :key="run.id"
        ><button v-if="run.input_path" @click="emit('open-file', run.input_path)">
          {{ isZh ? '执行代码' : 'Code' }}</button
        ><button v-if="run.stdout_path" @click="emit('open-file', run.stdout_path)">
          {{ isZh ? '执行输出' : 'Output' }}
        </button></template
      >
    </details>
  </section>
</template>
<script setup lang="ts">
import type { ReadableResult, ResearchSnapshot, EvidenceUse } from '@/types/research'
import { useStudioCopy } from '@/lang/studio'
import ResultSeries from './ResultSeries.vue'
import WorkDocument from './WorkDocument.vue'
const props = defineProps<{
  result: ReadableResult
  workId: string
  runs: ResearchSnapshot['runs']
  snapshot?: ResearchSnapshot | null
  focusRef?: string
}>()
const emit = defineEmits<{ 'open-file': [path: string]; paper: [use: EvidenceUse] }>()
const { isZh } = useStudioCopy()
const usesFor = (ref: string) => props.snapshot?.evidence_uses?.filter((u) => u.ref === ref) || []
const displayValue = (value: string, kind: string) => {
  const number = Number(value)
  return kind === 'number' && value.trim() && Number.isFinite(number)
    ? number.toLocaleString(isZh.value ? 'zh-CN' : 'en-US', { maximumSignificantDigits: 6 })
    : value
}
</script>
<style scoped>
.measurement {
  border-bottom: 1px solid var(--pa-line);
  padding: 12px 0 18px;
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
.value {
  font-size: 22px;
}
p,
dd {
  line-height: 1.8;
  font-size: 15px;
}
dt,
summary {
  color: var(--pa-muted);
  font-size: 13px;
}
dd {
  margin: 0 0 8px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
button {
  margin: 4px;
  padding: 6px 10px;
  border: 1px solid var(--pa-line);
  background: var(--pa-surface);
  color: inherit;
  border-radius: var(--pa-radius-control);
  cursor: pointer;
}
details {
  margin-top: 12px;
}
summary {
  cursor: pointer;
}
.paper-uses {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
