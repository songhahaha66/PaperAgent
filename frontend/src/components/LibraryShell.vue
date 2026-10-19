<template>
  <div class="library-page">
    <Sidebar
      :is-sidebar-collapsed="collapsed"
      :active-history-id="null"
      @toggle-sidebar="collapsed = !collapsed"
    />
    <main class="library-main">
      <header class="library-heading">
        <h1>{{ title }}</h1>
        <p v-if="description">{{ description }}</p>
        <slot name="actions" />
      </header>
      <div class="library-content"><slot /></div>
    </main>
  </div>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import Sidebar from './Sidebar.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
defineProps<{ title: string; description?: string }>()
const { isMobile } = useBreakpoint()
const collapsed = ref(isMobile.value)
watch(isMobile, (mobile) => {
  if (mobile) collapsed.value = true
})
</script>
<style scoped>
.library-page {
  display: flex;
  height: 100dvh;
  width: 100%;
  overflow: hidden;
  background: var(--pa-surface);
  color: var(--pa-ink);
}
.library-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}
.library-heading {
  padding: 24px 32px;
  border-bottom: 1px solid var(--pa-line);
}
h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
}
p {
  margin: 8px 0 0;
  color: var(--pa-muted);
  font-size: 13px;
  line-height: 1.7;
}
.library-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
@media (max-width: 768px) {
  .library-page {
    flex-direction: column;
  }
  .library-main {
    min-height: 0;
  }
  .library-heading {
    padding: 20px;
  }
}
</style>
