<template>
  <nav class="mobile-tab-bar" aria-label="工作区导航">
    <button
      type="button"
      class="mobile-tab"
      :class="{ active: modelValue === 'chat' }"
      @click="$emit('update:modelValue', 'chat')"
    >
      <t-icon name="chat" />
      <span>对话</span>
    </button>
    <button
      type="button"
      class="mobile-tab"
      :class="{ active: modelValue === 'files' }"
      @click="$emit('update:modelValue', 'files')"
    >
      <t-icon name="folder-open" />
      <span>文件</span>
    </button>
    <button
      type="button"
      class="mobile-tab"
      :class="{ active: modelValue === 'preview' }"
      @click="$emit('update:modelValue', 'preview')"
    >
      <t-icon name="browse" />
      <span>预览</span>
    </button>
  </nav>
</template>

<script setup lang="ts">
defineProps<{
  modelValue: 'chat' | 'files' | 'preview'
}>()

defineEmits<{
  'update:modelValue': [value: 'chat' | 'files' | 'preview']
}>()
</script>

<style scoped>
.mobile-tab-bar {
  display: flex;
  flex-shrink: 0;
  border-top: 1px solid #eee;
  background: #fff;
  padding-bottom: env(safe-area-inset-bottom, 0px);
}

.mobile-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 8px 0;
  border: none;
  background: none;
  color: #666;
  font-size: 12px;
}

.mobile-tab.active {
  color: #0052d9;
}

.mobile-tab .t-icon {
  font-size: 20px;
}
</style>
