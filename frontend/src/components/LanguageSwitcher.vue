<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t, locale } = useI18n()

const changeLanguage = (event: Event) => {
  const value = (event.target as HTMLSelectElement).value
  if (value !== 'zh-CN' && value !== 'en-US') return
  locale.value = value
  localStorage.setItem('locale', value)
}
</script>

<template>
  <select
    class="language-switcher"
    :value="locale"
    :aria-label="t('common.language')"
    @change="changeLanguage"
  >
    <option value="zh-CN">中文</option>
    <option value="en-US">English</option>
  </select>
</template>

<style scoped>
.language-switcher {
  padding: 6px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  color: #495057;
  background: white;
  cursor: pointer;
}
</style>
