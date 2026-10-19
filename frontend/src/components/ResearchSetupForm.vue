<template>
  <details class="research-setup">
    <summary>执行设置</summary>
    <div class="setup-grid">
      <label
        ><input
          type="checkbox"
          v-model="value.reuse_experience"
          :disabled="disabled"
        />复用本人真实研究经验</label
      >
      <label
        ><input
          type="checkbox"
          v-model="value.confirm_models"
          :disabled="disabled"
        />设计后由我确认</label
      >
      <label
        ><input type="checkbox" v-model="value.compact_paper" :disabled="disabled" />简短论文</label
      >
      <label
        ><input
          type="checkbox"
          :checked="value.request_limit !== null"
          :disabled="disabled"
          @change="value.request_limit = ($event.target as HTMLInputElement).checked ? 40 : null"
        />设置本研究的明确调用上限</label
      >
      <label v-if="value.request_limit !== null"
        >调用上限<input
          type="number"
          min="1"
          v-model.number="value.request_limit"
          :disabled="disabled"
          required
      /></label>
      <label
        >文献检索
        <select v-model="value.literature_mode" :disabled="disabled">
          <option value="on_request">题目明确要求时</option>
          <option value="auto">自动检索研究标题</option>
          <option value="off">仅手动登记和检索</option>
        </select></label
      >
    </div>
    <p>
      默认累计调用与服务返回的 token
      用量，不设置整项研究的调用截止。暂停恢复不会清零。请求超时沿用模型连接设置；需要时可明确设置调用上限。
    </p>
  </details>
</template>
<script setup lang="ts">
import type { ResearchSetup } from '@/api/researchSetup'
defineProps<{ csvFiles: string[]; disabled?: boolean }>()
const value = defineModel<ResearchSetup>({ required: true })
</script>
<style scoped>
.research-setup {
  padding: 12px 16px;
  border-top: 1px solid var(--pa-line);
  font-size: 13px;
}
.setup-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 12px;
}
label {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
input:not([type='checkbox']),
select,
textarea {
  max-width: 100%;
  padding: 5px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
}
textarea {
  width: 100%;
  min-height: 55px;
}
p {
  margin-top: 10px;
  color: var(--pa-muted);
}
</style>
