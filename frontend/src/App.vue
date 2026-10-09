<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import enUS from 'tdesign-vue-next/es/locale/en_US'
import zhCN from 'tdesign-vue-next/es/locale/zh_CN'

const { locale } = useI18n()
const componentLocale = computed(() => (locale.value === 'en-US' ? enUS : zhCN))
watch(
  locale,
  (value) => {
    document.documentElement.lang = value
  },
  { immediate: true },
)
</script>

<template>
  <div class="app-container">
    <!-- 路由出口 -->
    <t-config-provider :global-config="componentLocale">
      <router-view />
    </t-config-provider>
  </div>
</template>

<style>
/* 全局样式确保页面可以正常滚动 */
html,
body {
  margin: 0;
  padding: 0;
  overflow-x: hidden;
  overflow-y: auto;
  height: auto;
  min-height: 100vh;
}
</style>

<style scoped>
.app-container {
  min-height: 100vh;
  overflow-x: hidden;
}
</style>
