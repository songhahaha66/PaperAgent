<template>
  <div v-if="source" class="source-backdrop" @click.self="$emit('close')">
    <section
      class="source-card"
      role="dialog"
      aria-modal="true"
      :aria-label="source.title"
      @keydown.esc="$emit('close')"
      tabindex="-1"
      ref="dialog"
    >
      <header>
        <h2>{{ source.title }}</h2>
        <button aria-label="关闭来源" @click="$emit('close')">×</button>
      </header>
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
  </div>
</template>
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { PaperSource } from '@/api/paper'
const props = defineProps<{ source: PaperSource | null }>()
defineEmits<{ close: []; 'open-file': [path: string] }>()
const dialog = ref<HTMLElement | null>(null)
watch(
  () => props.source,
  async (value) => {
    if (value) {
      await nextTick()
      dialog.value?.focus()
    }
  },
)
</script>
<style scoped>
.source-backdrop {
  position: absolute;
  inset: 0;
  z-index: 40;
  background: var(--pa-mask);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.source-card {
  background: var(--pa-surface);
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-overlay);
  box-shadow: var(--pa-shadow-overlay);
  padding: 24px;
  max-width: 620px;
  max-height: 80%;
  overflow: auto;
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
