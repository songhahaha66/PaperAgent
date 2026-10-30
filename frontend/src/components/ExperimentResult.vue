<template>
  <section class="experiment-result">
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="record"
      ><h2>方案比较实验</h2>
      <p>{{ record.reason }}</p>
      <p v-if="record.conditions">实验条件：{{ record.conditions }}</p>
      <p v-if="record.evaluation">评价依据：{{ record.evaluation }}</p>
      <p v-if="!record.input_current">本实验对应的输入已变化，保留供查阅。</p>
      <p v-if="record.protocol">
        评价指标：{{ record.protocol.metric }}；验证集比例
        {{ Math.round(record.protocol.validation_fraction * 100) }}%
      </p>
      <article v-for="branch in record.branches" :key="branch.index">
        <h3>
          {{ branch.name }} <small v-if="record.status === 'winner' && branch.index === record.chosen_index">本次实测较优</small>
          <small v-else-if="record.status === 'tied' && branch.status === 'valid'">保留候选</small>
        </h3>
        <p v-if="branch.instruction">{{ branch.instruction }}</p>
        <p>{{ statusLabel(branch.status) }}</p>
        <p v-if="branch.metric != null">
          验证集 {{ record.protocol?.metric }}：{{ branch.metric }}
        </p>
        <p v-if="branch.baseline != null">基线：{{ branch.baseline }}</p>
        <p v-if="branch.error">{{ branch.error }}</p>
        <p v-if="branch.error">本次执行未完成有效验证，不据此判定方法无效。</p>
        <ResultSeries
          v-if="branch.predictions?.length"
          :values="branch.predictions.map((p) => p.observed)"
          :comparison="branch.predictions.map((p) => p.prediction)"
          label="观测值"
          axis-label="验证样本"
          :comparison-label="branch.name + '预测'"
        />
        <details>
          <summary>计算依据</summary>
          <template v-for="runId in branch.run_ids" :key="runId"
            ><button
              v-if="run(runId)?.input_path"
              @click="emit('open-file', run(runId)!.input_path!)"
            >
              查看执行代码</button
            ><button
              v-if="run(runId)?.stdout_path"
              @click="emit('open-file', run(runId)!.stdout_path!)"
            >
              查看运行输出
            </button></template
          >
        </details>
      </article>
    </template>
    <p v-else-if="!error">正在读取实验…</p>
  </section>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { experimentAPI, type ComparisonRecord } from '@/api/experiments'
import ResultSeries from './ResultSeries.vue'
import type { ResearchSnapshot } from '@/types/research'
const props = defineProps<{
  workId: string
  recordId: string
  runs?: ResearchSnapshot['runs']
  refreshKey?: number
}>()
const emit = defineEmits<{ 'open-file': [path: string] }>()
const record = ref<ComparisonRecord | null>(null),
  error = ref('')
const run = (id: string) => props.runs?.find((r) => r.id === id)
const statusLabel = (s: string) =>
  ({
    valid: '完成计算',
    failed: '计算失败',
    pending: '等待执行',
    running: '正在执行',
    interrupted: '执行中断',
    contract_error: '方案不符合实验契约',
    timeout: '执行超时',
    invalid: '证据未通过检查',
  })[s] || '尚未通过实验检查'
let version = 0
watch(
  () => [props.workId, props.recordId, props.refreshKey],
  async () => {
    const request = ++version
    try {
      const result = await experimentAPI.read(props.workId)
      if (request === version) {
        record.value = result.comparisons.find((c) => c.id === props.recordId) || null
        error.value = record.value ? '' : '实验记录不存在'
      }
    } catch (e) {
      if (request === version) error.value = e instanceof Error ? e.message : '实验记录暂不可读'
    }
  },
  { immediate: true },
)
</script>
<style scoped>
.experiment-result {
  padding: 24px;
  overflow: auto;
  flex: 1;
}
article {
  border-bottom: 1px solid var(--pa-line);
  padding: 16px 0;
}
h2 {
  font-size: 17px;
}
h3 {
  font-size: 15px;
}
small {
  font-weight: normal;
  color: var(--pa-accent);
}
button {
  margin: 8px;
  cursor: pointer;
}
</style>
