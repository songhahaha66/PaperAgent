<template>
  <ExperimentResult
    v-if="resultType === 'experiment'"
    :work-id="workId"
    :record-id="resultId"
    :runs="snapshot?.runs"
    @open-file="(path) => emit('open-file', path)"
    :refresh-key="snapshot?.commit_seq"
  />
  <section v-else-if="outcome" class="result-viewer readable-result">
    <ResearchOutcome
      :work-id="workId"
      :outcome="outcome"
      :snapshot="snapshot"
      :focus-ref="resultType === 'registered' ? resultId : undefined"
      @paper="(use) => emit('paper', use)"
      :runs="snapshot?.runs || []"
      @open-file="(path) => emit('open-file', path)"
      @issue="(id) => emit('issue', id)"
    />
  </section>
  <section v-else class="result-viewer">
    <template v-if="result">
      <header>
        <span>{{ task?.objective || (isZh ? '研究结果' : 'Research result') }}</span>
        <h2>{{ result.name }}</h2>
      </header>
      <p class="result-value">{{ result.value }} {{ result.unit }}</p>
      <button v-if="result.figure_path" @click="emit('open-file', result.figure_path)">
        {{ isZh ? '打开图表' : 'Open figure' }}
      </button>
      <h3>{{ isZh ? '数据与计算' : 'Data and computation' }}</h3>
      <button v-if="run?.input_path" @click="emit('open-file', run.input_path)">
        {{ isZh ? '查看执行代码' : 'Open code' }}
      </button>
      <button v-if="run?.stdout_path" @click="emit('open-file', run.stdout_path)">
        {{ isZh ? '查看运行输出' : 'Open run output' }}
      </button>
      <p v-if="!run?.input_path && !run?.stdout_path">
        {{
          isZh
            ? '当前记录未提供可打开的代码或输出。'
            : 'No code or output link is available in this record.'
        }}
      </p>
      <p class="source-note">
        {{
          isZh
            ? '当前记录提供执行代码和输出，未登记独立的数据来源链接。'
            : 'This record provides code and output, without a separate data-source link.'
        }}
      </p>
      <p v-for="claim in claims" :key="claim.id">{{ claim.statement }}</p>
      <details v-if="run">
        <summary>{{ isZh ? '运行记录' : 'Run record' }}</summary>
        <p>{{ run.id }} · {{ run.status }}</p>
      </details>
    </template>
    <p v-else role="status">
      {{
        isZh
          ? '此结果不在当前快照中。可在文件中查阅保留的产物，或刷新运行状态。'
          : 'This result is outside the current snapshot. Open retained files or refresh run status.'
      }}
    </p>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import type { ResearchSnapshot, EvidenceUse } from '@/types/research'
import { useStudioCopy } from '@/lang/studio'
import ExperimentResult from './ExperimentResult.vue'
import ResearchOutcome from './ResearchOutcome.vue'
const props = defineProps<{
  workId: string
  resultType: 'experiment' | 'registered' | 'task'
  resultId: string
  snapshot: ResearchSnapshot | null
}>()
const emit = defineEmits<{
  'open-file': [path: string]
  issue: [id: string]
  paper: [use: EvidenceUse]
}>()
const { isZh } = useStudioCopy()
const outcome = computed(() =>
  props.snapshot?.outcomes?.find((o) =>
    props.resultType === 'task'
      ? o.id === props.resultId
      : o.results.some((r) => r.id === props.resultId),
  ),
)
const result = computed(
  () =>
    props.snapshot?.results.find((r) => r.id === props.resultId) ||
    props.snapshot?.issue_evidence?.find((r) => r.id === props.resultId),
)
const task = computed(() =>
  props.snapshot?.tasks?.find((t) => t.task_id === result.value?.subproblem_id),
)
const run = computed(() => props.snapshot?.runs.find((r) => r.id === result.value?.run_id))
const claims = computed(
  () => props.snapshot?.claims.filter((c) => c.evidence.includes(props.resultId)) || [],
)
</script>
<style scoped>
.readable-result {
  padding: 0 !important;
}
.result-viewer {
  padding: 28px;
  overflow: auto;
  flex: 1;
  color: var(--pa-ink);
}
header span {
  font-size: 13px;
  color: var(--pa-muted);
}
h2 {
  font-size: 22px;
}
h3 {
  font-size: 15px;
  margin-top: 28px;
}
.result-value {
  font-size: 24px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
button {
  padding: 8px 12px;
  margin: 4px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface);
  color: inherit;
  cursor: pointer;
}
p {
  line-height: 1.8;
}
details {
  margin-top: 24px;
  font-size: 13px;
}
</style>
