<template>
  <section class="research-status">
    <header>
      <h2>研究</h2>
      <button v-if="actionLabel && !running" :disabled="actionBusy" @click="emit('action')">
        {{ actionLabel }}
      </button>
    </header>
    <ResearchProgress
      :snapshot="snapshot"
      :work-status="workStatus"
      @object="(o) => emit('object', o)"
      @issue="(id) => emit('issue', id)"
      @ask="(id) => emit('ask', id)"
      @pause="emit('pause')"
    />
    <details class="resources">
      <summary>研究资源</summary>
      <ResearchReferences
        :work-id="workId"
        :snapshot="snapshot"
        @revise="(message, task) => emit('revise', message, task)"
      />
    </details>
  </section>
</template>
<script setup lang="ts">
import ResearchProgress from './ResearchProgress.vue'
import ResearchReferences from './ResearchReferences.vue'
import type { ResearchSnapshot } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
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
  ask: [id: string]
  pause: []
}>()
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
