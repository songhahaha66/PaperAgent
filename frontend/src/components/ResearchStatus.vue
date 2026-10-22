<template>
  <section class="research-status">
    <ResearchProgress
      :snapshot="snapshot"
      :work-status="workStatus"
      :action-label="actionLabel"
      :action-busy="actionBusy"
      @action="emit('action')"
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
  max-width: 880px;
  margin: auto;
}
.resources {
  margin-top: 16px;
  border-top: 1px solid var(--pa-line);
  padding-top: 16px;
}
.resources > summary {
  cursor: pointer;
  color: var(--pa-muted);
  font-size: 13px;
}
@media (max-width: 600px) {
  .research-status {
    padding: 18px;
  }
}
</style>
