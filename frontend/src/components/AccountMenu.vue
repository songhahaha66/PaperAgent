<template>
  <t-popup
    v-model:visible="visible"
    trigger="click"
    :placement="placement"
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
        ><strong :title="userName">{{ userName }}</strong></span
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
          <button class="account-action" @click="navigate('/passkey')">
            <t-icon name="secured" /><span>{{ t('sidebar.passkeys') }}</span
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
withDefaults(defineProps<{ collapsed: boolean; placement?: 'top-left' | 'bottom-right' }>(), {
  placement: 'top-left',
})
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
const popupStyle = {
  width: '272px',
  maxWidth: 'calc(100vw - 24px)',
  padding: '6px',
  borderRadius: 'var(--pa-radius-overlay)',
  border: '1px solid var(--pa-line)',
  boxShadow: 'var(--pa-shadow-overlay)',
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
  border-radius: var(--pa-radius-control);
  text-align: left;
  padding: 10px 11px;
  cursor: pointer;
  color: var(--pa-ink);
}
.account-trigger:hover,
.account-trigger[aria-expanded='true'] {
  background: var(--pa-surface-hover);
}
.account-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: var(--pa-radius-circle);
  background: var(--pa-surface-hover);
  color: var(--pa-muted);
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
  font-size: 15px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-trigger:not(.compact) > .account-avatar {
  width: 36px;
  height: 36px;
  font-size: 14px;
}
.account-trigger-chevron {
  font-size: 14px;
  color: var(--pa-placeholder);
  margin-left: auto;
}
.account-trigger.compact {
  width: var(--pa-control-height-lg);
  height: var(--pa-control-height-lg);
  padding: 6px;
  justify-content: center;
  border-radius: var(--pa-radius-control);
}
.account-panel {
  font-family: var(--td-font-family);
  color: var(--pa-ink);
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
  font-size: 15px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-summary > div > span {
  font-size: 13px;
  color: var(--pa-placeholder);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-divider {
  height: 1px;
  background: var(--pa-surface-hover);
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
  border-radius: var(--pa-radius-control);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  color: var(--pa-ink);
  line-height: 1.6;
}
.account-action:hover {
  background: var(--pa-surface-hover);
}
.account-action > .t-icon {
  font-size: 17px;
  color: var(--pa-muted);
  flex-shrink: 0;
}
.account-action > span {
  flex: 1;
  white-space: normal;
  overflow-wrap: anywhere;
}
.account-action > .action-chevron {
  font-size: 12px;
  color: var(--pa-disabled);
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
  color: var(--pa-muted);
}
.language-label > .t-icon {
  font-size: 16px;
}
.language-options {
  display: flex;
  align-items: center;
  border-radius: var(--pa-radius-control);
  padding: 3px;
  background: var(--pa-surface-muted);
  gap: 2px;
}
.language-options button {
  border: 0;
  background: transparent;
  cursor: pointer;
  color: var(--pa-placeholder);
  border-radius: var(--pa-radius-control);
  font-size: 11px;
  padding: 5px 7px;
  line-height: 1.4;
}
.language-options button.selected {
  background: var(--td-brand-color);
  box-shadow: none;
  color: var(--pa-on-accent);
}
.logout-action {
  margin-top: 4px;
  color: var(--pa-muted);
}
.logout-action > .t-icon {
  color: var(--pa-placeholder);
}
@media (prefers-reduced-motion: no-preference) {
  .account-trigger {
    transition: background 0.15s;
  }
}
</style>
