<template>
  <t-popup
    v-model:visible="visible"
    trigger="click"
    placement="top-left"
    :show-arrow="false"
    overlay-inner-class-name="account-menu-popup"
    :overlay-inner-style="popupStyle"
  >
    <button
      ref="trigger"
      :class="['account-trigger', { compact: collapsed }]"
      :aria-label="isZh ? '账户与设置' : 'Account and settings'"
      aria-haspopup="dialog"
      :aria-expanded="visible"
      :aria-controls="panelId"
    >
      <span class="account-avatar">{{ userName.charAt(0).toUpperCase() }}</span>
      <span v-if="!collapsed" class="account-trigger-details"
        ><strong>{{ userName }}</strong
        ><span>{{ auth.currentUser?.email }}</span></span
      >
      <t-icon v-if="!collapsed" name="chevron-up" class="account-trigger-chevron" />
    </button>
    <template #content>
      <section
        :id="panelId"
        class="account-panel"
        role="dialog"
        :aria-label="isZh ? '账户与设置' : 'Account and settings'"
        @keydown.esc.stop.prevent="closeAndFocus"
      >
        <div class="account-summary">
          <span class="account-avatar">{{ userName.charAt(0).toUpperCase() }}</span>
          <div>
            <strong>{{ userName }}</strong
            ><span>{{ auth.currentUser?.email }}</span>
          </div>
        </div>
        <div class="account-divider" />
        <nav :aria-label="isZh ? '账户设置' : 'Account settings'">
          <button
            v-for="item in items"
            :key="item.path"
            class="account-action"
            @click="navigate(item.path)"
          >
            <t-icon :name="item.icon" /><span>{{ item.label }}</span
            ><t-icon name="chevron-right" class="action-chevron" />
          </button>
        </nav>
        <div class="account-divider" />
        <div class="account-language">
          <span class="language-label"><t-icon name="translate" />{{ t('common.language') }}</span>
          <div class="language-options" role="group" :aria-label="t('common.language')">
            <button
              v-for="language in languages"
              :key="language.value"
              :aria-pressed="locale === language.value"
              :class="{ selected: locale === language.value }"
              @click="changeLanguage(language.value)"
            >
              {{ language.label }}
            </button>
          </div>
        </div>
        <div class="account-divider" />
        <button class="account-action logout-action" @click="logout">
          <t-icon name="logout" /><span>{{ t('sidebar.logout') }}</span>
        </button>
      </section>
    </template>
  </t-popup>
</template>
<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ (event: 'navigate'): void }>()
const { t, locale } = useI18n()
const auth = useAuthStore(),
  router = useRouter()
const visible = ref(false),
  trigger = ref<HTMLButtonElement | null>(null)
const panelId = useId()
const isZh = computed(() => locale.value.startsWith('zh'))
const userName = computed(() => auth.currentUser?.username || t('common.user'))
const languages = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en-US', label: 'English' },
]
const items = computed(() => [
  { path: '/template', label: t('sidebar.templates'), icon: 'file-copy' },
  { path: '/api-config', label: t('sidebar.apiKey'), icon: 'setting' },
  { path: '/passkey', label: t('sidebar.passkeys'), icon: 'secured' },
])
const popupStyle = {
  width: '272px',
  maxWidth: 'calc(100vw - 24px)',
  padding: '6px',
  borderRadius: '16px',
  border: '1px solid #e9e9e9',
  boxShadow: '0 6px 28px #00000012, 0 1px 4px #00000005',
}
function closeAndFocus() {
  visible.value = false
  trigger.value?.focus()
}
function navigate(path: string) {
  visible.value = false
  emit('navigate')
  router.push(path)
}
function changeLanguage(value: string) {
  locale.value = value
  localStorage.setItem('locale', value)
}
function logout() {
  auth.logout()
  navigate('/login')
}
</script>
<style scoped>
.account-trigger {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 10px;
  border: 0;
  background: transparent;
  border-radius: 12px;
  text-align: left;
  padding: 10px 11px;
  cursor: pointer;
  color: #444;
}
.account-trigger:hover,
.account-trigger[aria-expanded='true'] {
  background: #f5f5f5;
}
.account-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #eee;
  color: #666;
  font-size: 13px;
  font-weight: 500;
}
.account-trigger-details {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}
.account-trigger-details strong {
  font-size: 12px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-trigger-details > span {
  font-size: 10px;
  color: #999;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-trigger-chevron {
  font-size: 14px;
  color: #aaa;
  margin-left: auto;
}
.account-trigger.compact {
  width: 44px;
  height: 44px;
  padding: 6px;
  justify-content: center;
  border-radius: 13px;
}
.account-panel {
  font-family: var(--td-font-family);
  color: #444;
  max-height: calc(100dvh - 36px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.account-summary {
  display: flex;
  gap: 11px;
  align-items: center;
  padding: 12px 11px 14px;
}
.account-summary .account-avatar {
  width: 36px;
  height: 36px;
  font-size: 14px;
}
.account-summary > div {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.account-summary strong {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-summary > div > span {
  font-size: 11px;
  color: #999;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-divider {
  height: 1px;
  background: #f0f0f0;
  margin: 4px 6px;
}
.account-panel nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 0;
}
.account-action {
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  padding: 11px 12px;
  border: 0;
  background: transparent;
  border-radius: 10px;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  color: #555;
  line-height: 1.6;
}
.account-action:hover {
  background: #f5f5f5;
}
.account-action > .t-icon {
  font-size: 17px;
  color: #777;
  flex-shrink: 0;
}
.account-action > span {
  flex: 1;
  white-space: normal;
  overflow-wrap: anywhere;
}
.account-action > .action-chevron {
  font-size: 12px;
  color: #bbb;
}
.account-language {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 12px;
}
.language-label {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #777;
}
.language-label > .t-icon {
  font-size: 16px;
}
.language-options {
  display: flex;
  align-items: center;
  border-radius: 9px;
  padding: 3px;
  background: #f4f4f4;
  gap: 2px;
}
.language-options button {
  border: 0;
  background: transparent;
  cursor: pointer;
  color: #999;
  border-radius: 7px;
  font-size: 11px;
  padding: 5px 7px;
  line-height: 1.4;
}
.language-options button.selected {
  background: #fff;
  box-shadow: 0 1px 3px #0000000b;
  color: #444;
}
.logout-action {
  margin-top: 4px;
  color: #777;
}
.logout-action > .t-icon {
  color: #999;
}
@media (prefers-reduced-motion: no-preference) {
  .account-trigger {
    transition: background 0.15s;
  }
}
</style>
