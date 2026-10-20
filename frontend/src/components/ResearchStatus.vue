<template>
  <section class="research-status">
    <header>
      <h2>研究进度</h2>
      <button v-if="actionLabel" :disabled="actionBusy" @click="emit('action')">
        {{ actionLabel }}
      </button>
    </header>
    <p v-if="workStatus === 'waiting'">研究需要补充信息，请在对话中回复。</p>
    <ResearchRuntime
      :snapshot="snapshot"
      :work-status="workStatus"
      @file="(path) => emit('object', { type: 'file', path })"
    />
    <ResearchProgress
      :snapshot="snapshot"
      :work-status="workStatus"
      @object="(o) => emit('object', o)"
      @issue="(id) => emit('issue', id)"
    />
    <details class="resources">
      <summary>使用的研究资源</summary>
      <ResearchReferences
        :work-id="workId"
        :snapshot="snapshot"
        @revise="(message, task) => emit('revise', message, task)"
      />
    </details>
    <section v-if="faults.length" aria-label="运行故障">
      <h3>执行中断记录</h3>
      <article v-for="fault in faults" :key="fault.id">
        <p role="alert">{{ fault.message }}</p>
        <button
          v-if="fault.category === 'execution' && fault.status === 'confirmed'"
          :disabled="running || busy"
          @click="recover(fault.id)"
        >
          核验并记录恢复
        </button>
      </article>
      <p v-if="error" role="alert">{{ error }}</p>
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import ResearchRuntime from './ResearchRuntime.vue'
import ResearchProgress from './ResearchProgress.vue'
import ResearchReferences from './ResearchReferences.vue'
import type { ResearchSnapshot } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
import { executionRecoveryAPI } from '@/api/researchSetup'
const props = defineProps<{
  workId: string
  snapshot: ResearchSnapshot | null
  workStatus?: string
  stage?: string
  running?: boolean
  actionLabel?: string
  actionBusy?: boolean
}>()
const emit = defineEmits<{
  action: []
  object: [object: WorkObject]
  issue: [id: string]
  resolved: []
  revise: [message: string, task: string]
}>()
const faults = computed(
  () =>
    props.snapshot?.faults?.filter(
      (f) => !['resolved', 'dismissed', 'superseded'].includes(f.status),
    ) || [],
)
const busy = ref(false),
  error = ref('')
async function recover(id: string) {
  busy.value = true
  error.value = ''
  try {
    const proof = await executionRecoveryAPI.preview(props.workId, id)
    await executionRecoveryAPI.resolve(props.workId, id, proof.proof_hash)
    emit('resolved')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '恢复核验未通过'
  } finally {
    busy.value = false
  }
}
</script>
<style scoped>
.research-status {
  padding: 24px;
  max-width: 1000px;
  margin: auto;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
article {
  padding: 12px 0;
  border-bottom: 1px solid var(--pa-line);
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
  font-size: 12px;
  margin: 20px 0;
}
h2 {
  font-size: 20px;
}
h3 {
  font-size: 16px;
}
button {
  cursor: pointer;
  padding: 6px 12px;
}
p {
  line-height: 1.7;
  color: var(--pa-muted);
}
</style>
