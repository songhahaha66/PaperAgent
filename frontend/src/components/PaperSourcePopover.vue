<template>
  <PaperPopover
    :open="!!source"
    :title="source?.title || '引用来源'"
    :anchor="anchor"
    :host="host"
    tone="source"
    :width="420"
    @close="$emit('close')"
  >
    <section v-if="source" class="source-card">
      <p class="boundary">
        {{ source.abstract_only ? '仅摘要' : source.boundary }} ·
        {{ source.page ? `第 ${source.page} 页 · ` : '' }}{{ source.locator }}
      </p>
      <blockquote v-if="source.excerpt">{{ source.excerpt }}</blockquote>
      <p v-else>尚未获取可定位的原文摘录，不能补写原文。</p>
      <p class="note">{{ source.note }}</p>
      <a v-if="source.url" :href="source.url" target="_blank" rel="noopener noreferrer"
        >打开来源 ↗</a
      >
      <button v-for="path in source.paths" :key="path" @click="$emit('open-file', path)">
        {{ path.endsWith('.py') ? '查看实际代码' : '查看实际输出' }}
      </button>
      <details>
        <summary>技术详情</summary>
        <p>来源键：{{ source.key }}</p>
        <p>来源版本：{{ source.version }}</p>
      </details>
    </section>
  </PaperPopover>
</template>
<script setup lang="ts">
import type { PaperSource } from '@/api/paper'
import type { PaperAnchor } from '@/utils/paperAnnotations'
import PaperPopover from './PaperPopover.vue'
defineProps<{
  source: PaperSource | null
  anchor?: PaperAnchor | null
  host?: HTMLElement | null
}>()
defineEmits<{ close: []; 'open-file': [path: string] }>()
</script>
<style scoped>
.source-card {
  overflow-wrap: anywhere;
}
header {
  display: flex;
  gap: 20px;
  align-items: start;
  justify-content: space-between;
}
h2 {
  font-size: 18px;
  font-weight: 500;
  margin: 0;
}
button {
  cursor: pointer;
  border: 0;
  background: var(--pa-surface-muted);
  color: var(--pa-ink);
  padding: 7px 10px;
  border-radius: var(--pa-radius-control);
}
blockquote {
  white-space: pre-wrap;
  margin: 18px 0;
  padding: 12px 18px;
  border-left: 3px solid var(--pa-accent);
  line-height: 1.8;
  font-size: 14px;
}
.boundary,
.note,
details {
  font-size: 12px;
  color: var(--pa-muted);
  line-height: 1.7;
}
a {
  color: var(--pa-accent);
}
details {
  margin-top: 20px;
}
</style>
