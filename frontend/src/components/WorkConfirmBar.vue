<template>
  <div v-if="visible" class="work-confirm-bar">
    <div class="work-confirm-copy">
      <strong>校验未通过，已暂停自动修复</strong>
      <p v-if="issues.length">{{ issueSummary }}</p>
      <p v-else>确认后将采用当前稿并停止本轮修复。</p>
    </div>
    <div class="work-confirm-actions">
      <t-button size="small" variant="outline" :disabled="disabled" @click="$emit('dismiss')">稍后</t-button>
      <t-button size="small" theme="primary" :disabled="disabled" @click="$emit('confirm')">
        确认采用当前稿
      </t-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ValidationIssuePreview } from '@/composables/useAguiEvents'

const props = defineProps<{
  visible: boolean
  issues: ValidationIssuePreview[]
  disabled?: boolean
}>()

defineEmits<{
  confirm: []
  dismiss: []
}>()

const issueSummary = computed(() =>
  props.issues
    .slice(0, 3)
    .map((issue) => issue.detail || issue.code)
    .join('；'),
)
</script>

<style scoped>
.work-confirm-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: 0 16px 8px;
  padding: 10px 12px;
  background: #fff7e6;
  border: 1px solid #ffe58f;
  border-radius: 6px;
}

.work-confirm-copy {
  min-width: 0;
}

.work-confirm-copy strong {
  display: block;
  color: #ad6800;
  font-size: 13px;
}

.work-confirm-copy p {
  margin: 4px 0 0;
  color: #8c6d1f;
  font-size: 12px;
  line-height: 1.5;
}

.work-confirm-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
