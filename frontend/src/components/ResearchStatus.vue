<template>
  <section class="research-status">
    <header>
      <h2>{{ stage || '研究进度' }}</h2>
      <button v-if="actionLabel" :disabled="actionBusy" @click="emit('action')">
        {{ actionLabel }}
      </button>
    </header>
    <p v-if="workStatus === 'waiting'">研究需要补充信息，请在对话中回复。</p>
    <ResearchProgress
      :snapshot="snapshot"
      :work-status="workStatus"
      @object="(o) => emit('object', o)"
      @issue="(id) => emit('issue', id)"
    />
    <section v-if="snapshot?.plans?.length">
      <h3>研究方案</h3>
      <article v-for="plan in snapshot.plans" :key="plan.id">
        <strong>{{ plan.objective }}</strong>
        <p v-for="method in plan.methods" :key="method.name">
          {{ method.name }}<span v-if="method.idea">：{{ method.idea }}</span>
        </p>
      </article>
    </section>
    <ResearchReferences :work-id="workId" :snapshot="snapshot" @revise="(message, task) => emit('revise', message, task)" />
    <section v-if="faults.length" aria-label="运行故障">
      <h3>运行需要处理</h3>
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
