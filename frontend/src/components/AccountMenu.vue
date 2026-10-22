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
      type="button"
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
      <t-icon v-if="!collapsed" name="more" class="account-trigger-chevron" />
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
        <div class="account-language">
          <span class="language-label"><t-icon name="translate" />{{ t('common.language') }}</span>
          <LanguageSwitcher />
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
import LanguageSwitcher from './LanguageSwitcher.vue'
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
const popupStyle = {
  width: '248px',
  maxWidth: 'calc(100vw - 24px)',
  padding: '6px',
  borderRadius: 'var(--pa-radius-card)',
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
  padding: 8px 10px;
  cursor: pointer;
  color: var(--pa-ink);
}
.account-trigger:hover,
.account-trigger[aria-expanded='true'] {
  background: var(--pa-surface-muted);
}
.account-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: var(--pa-radius-circle);
  background: var(--pa-accent-soft);
  color: var(--pa-accent);
  font-size: 12px;
  font-weight: 600;
}
.account-trigger-details {
  min-width: 0;
  flex: 1;
}
.account-trigger-details strong {
  display: block;
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-trigger-chevron {
  font-size: 16px;
  color: var(--pa-muted);
  margin-left: auto;
}
.account-trigger.compact {
  width: var(--pa-control-height-lg);
  height: var(--pa-control-height-lg);
  padding: 6px;
  justify-content: center;
}
.account-panel {
  font-family: var(--td-font-family);
  color: var(--pa-ink);
  max-height: calc(100dvh - 36px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.account-summary {
  padding: 10px 10px 12px;
}
.account-summary > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.account-summary strong,
.account-summary > div > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.account-summary strong {
  font-size: 14px;
  font-weight: 600;
}
.account-summary > div > span {
  font-size: 12px;
  color: var(--pa-muted);
}
.account-divider {
  height: 1px;
  background: var(--pa-line);
  margin: 3px 8px;
}
.account-panel nav {
  padding-top: 4px;
}
.account-action,
.account-language {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 36px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.5;
}
.account-action {
  border: 0;
  background: transparent;
  border-radius: var(--pa-radius-control);
  text-align: left;
  cursor: pointer;
  color: var(--pa-ink);
}
.account-action:hover {
  background: var(--pa-surface-hover);
}
.account-action > .t-icon,
.language-label > .t-icon {
  font-size: 16px;
  color: var(--pa-muted);
  flex-shrink: 0;
}
.account-action > span {
  flex: 1;
}
.account-action > .action-chevron {
  font-size: 12px;
  color: var(--pa-placeholder);
}
.account-language {
  justify-content: space-between;
  padding-top: 5px;
  padding-bottom: 5px;
}
.language-label {
  display: flex;
  align-items: center;
  gap: 10px;
}
.account-language :deep(.language-switcher) {
  max-width: 108px;
  padding: 3px 6px;
  border-color: transparent;
  background: transparent;
  color: var(--pa-muted);
  font: inherit;
  font-size: 12px;
}
.account-language :deep(.language-switcher:hover) {
  background: var(--pa-surface-hover);
}
.logout-action {
  margin-top: 3px;
  color: var(--pa-muted);
}
.account-trigger:focus-visible,
.account-action:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
@media (prefers-reduced-motion: no-preference) {
  .account-trigger {
    transition: background 0.15s;
  }
}
</style>
