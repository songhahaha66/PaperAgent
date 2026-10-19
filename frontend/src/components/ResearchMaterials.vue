<template>
  <section class="research-materials">
    <header>
      <h2>资料与成果</h2>
      <button @click="load">刷新</button>
    </header>
    <p v-if="error" role="alert">{{ error }}</p>
    <h3>研究成果</h3>
    <p v-if="!snapshot?.outcomes?.length">完成研究后，成果及其证据会出现在这里。</p>
    <button
      v-for="outcome in snapshot?.outcomes || []"
      :key="outcome.id"
      class="resource"
      @click="emit('object', { type: 'result', resultType: 'task', id: outcome.id })"
    >
      <strong>{{ outcome.title }}</strong
      ><span>{{ outcome.objective }}</span>
    </button>
    <h3>实验记录</h3>
    <p v-if="!experiments.length">尚无方案比较实验。</p>
    <button
      v-for="experiment in experiments"
      :key="experiment.id"
      class="resource"
      @click="emit('object', { type: 'result', resultType: 'experiment', id: experiment.id })"
    >
      <strong>{{ experiment.branches.map((b) => b.name).join(' / ') }}</strong
      ><span>{{ experiment.reason || '查看实验结果' }}</span>
    </button>
    <h3>交付文件</h3>
    <button
      v-for="path in snapshot?.artifacts || []"
      :key="path"
      class="resource"
      @click="emit('object', { type: 'file', path })"
    >
      {{ path }}
    </button>
    <details v-if="snapshot?.legacy_records?.length">
      <summary>保留的历史记录（含义未确认）</summary>
      <article v-for="(record, index) in snapshot.legacy_records" :key="index">
        <p>来源：{{ record.source }}</p>
        <pre>{{ record.raw }}</pre>
      </article>
    </details>
    <ResearchLiterature
      :work-id="workId"
      :disabled="disabled"
      :evidence-id="evidenceId"
      :refresh-key="snapshot?.commit_seq"
      @updated="emit('updated')"
      @cite="(id) => emit('cite', id)"
    />
  </section>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import ResearchLiterature from './ResearchLiterature.vue'
import { experimentAPI, type ComparisonRecord } from '@/api/experiments'
import type { ResearchSnapshot } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
const props = defineProps<{
  workId: string
  snapshot: ResearchSnapshot | null
  disabled?: boolean
  evidenceId?: string
}>()
const emit = defineEmits<{ object: [object: WorkObject]; updated: []; cite: [id: string] }>()
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
</script>
<style scoped>
.research-materials {
  padding: 24px;
  overflow: auto;
  width: 100%;
  box-sizing: border-box;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
h2 {
  font-size: 20px;
}
h3 {
  font-size: 16px;
  margin-top: 24px;
}
.resource {
  display: flex;
  flex-direction: column;
  text-align: left;
  gap: 6px;
  padding: 14px;
  margin: 8px 0;
  width: 100%;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  background: var(--pa-surface);
  color: var(--pa-ink);
  cursor: pointer;
}
.resource span {
  color: var(--pa-muted);
  font-size: 13px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
header > button {
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface);
  color: var(--pa-ink);
  font: inherit;
  font-size: 12px;
  line-height: 20px;
  padding: 5px 12px;
  cursor: pointer;
}
</style>
