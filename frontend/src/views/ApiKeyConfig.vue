<template>
  <div class="models-page">
    <Sidebar
      :is-sidebar-collapsed="collapsed"
      :active-history-id="null"
      @toggle-sidebar="collapsed = !collapsed"
    />
    <main class="models-main">
      <header class="models-header">
        <div>
          <h1>{{ ui.modelTitle }}</h1>
          <p>{{ ui.modelSubtitle }}</p>
        </div>
        <LanguageSwitcher />
      </header>
      <div class="models-body">
        <t-card :title="ui.defaultConnection" class="connection-card"
          ><template #actions
            ><t-tag :theme="forms.brain.id ? 'success' : 'default'" variant="light" size="small">{{
              forms.brain.id ? ui.configured : ui.unconfigured
            }}</t-tag></template
          >
          <p class="field-hint">{{ ui.connectionHint }}</p>
          <div class="provider-options">
            <t-radio-group :value="isDeepSeek ? 'deepseek' : 'custom'" @change="selectProvider"
              ><t-radio-button value="deepseek">DeepSeek</t-radio-button
              ><t-radio-button value="custom">{{ ui.custom }}</t-radio-button></t-radio-group
            >
          </div>
          <ConnectionFields :form="forms.brain" :disabled="loading || saving" :ui="ui" />
          <div class="apply-option">
            <t-checkbox v-model="applyAll" :disabled="saving">{{ ui.applyAll }}</t-checkbox>
            <p>{{ ui.applyHint }}</p>
          </div>
          <t-alert
            v-if="feedback"
            :theme="feedbackError ? 'error' : 'success'"
            :message="feedback"
            class="connection-feedback"
          />
          <div class="connection-actions">
            <t-button
              theme="primary"
              :loading="saving"
              :disabled="loading || !valid(forms.brain)"
              @click="saveDefault"
              >{{ forms.brain.id ? ui.update : ui.save }}</t-button
            ><t-button
              variant="outline"
              theme="default"
              :loading="testing"
              :disabled="!valid(forms.brain) || saving || loading"
              @click="testConnection"
              >{{ testing ? ui.testing : ui.test }}</t-button
            ><span v-if="forms.brain.id" class="key-note"
              ><t-icon name="secured" />{{ ui.keepKey }}</span
            >
          </div>
        </t-card>
        <t-collapse class="advanced-config"
          ><t-collapse-panel value="roles" :header="ui.advanced"
            ><template #header-right-content
              ><span class="advanced-hint">{{ ui.advancedHint }}</span></template
            >
            <div class="advanced-content">
              <section v-for="role in roles.slice(1)" :key="role.type" class="role-form">
                <div class="advanced-role-title">
                  <h3>{{ role.label }}</h3>
                  <t-tag
                    :theme="forms[role.type].id ? 'success' : 'default'"
                    variant="light"
                    size="small"
                    >{{ forms[role.type].id ? ui.configured : ui.unconfigured }}</t-tag
                  >
                </div>
                <p class="field-hint">{{ role.hint }}</p>
                <ConnectionFields
                  :form="forms[role.type]"
                  :disabled="saving || !!savingRole"
                  :ui="ui"
                />
                <div class="role-actions">
                  <t-button
                    theme="primary"
                    variant="outline"
                    :disabled="!valid(forms[role.type]) || !!savingRole"
                    :loading="savingRole === role.type"
                    @click="saveRole(role.type)"
                    >{{ ui.save }}</t-button
                  ><t-button
                    v-if="forms[role.type].id"
                    theme="danger"
                    variant="text"
                    @click="confirmDelete(role.type)"
                    >{{ ui.delete }}</t-button
                  >
                </div>
              </section>
            </div></t-collapse-panel
          ></t-collapse
        >
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { Input, DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import Sidebar from '@/components/Sidebar.vue'
import LanguageSwitcher from '@/components/LanguageSwitcher.vue'
import { modelConfigAPI, type ModelConfig } from '@/api/modelConfig'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useStudioCopy } from '@/lang/studio'
type Role = 'brain' | 'code' | 'writing'
interface Form {
  id?: number
  apiKey: string
  baseUrl: string
  modelId: string
}
const { ui, isZh } = useStudioCopy(),
  { isMobile } = useBreakpoint()
const collapsed = ref(isMobile.value)
watch(isMobile, (m) => {
  if (m) collapsed.value = true
})
const forms = reactive<Record<Role, Form>>({
  brain: { apiKey: '', baseUrl: 'https://api.deepseek.com/v1', modelId: 'deepseek-flash' },
  code: { apiKey: '', baseUrl: '', modelId: '' },
  writing: { apiKey: '', baseUrl: '', modelId: '' },
})
const loading = ref(true),
  saving = ref(false),
  testing = ref(false),
  applyAll = ref(true),
  customProvider = ref(false),
  savingRole = ref('')
const feedback = ref(''),
  feedbackError = ref(false)
const isDeepSeek = computed(
  () => !customProvider.value && forms.brain.baseUrl.includes('api.deepseek.com'),
)
const roles = computed(() => [
  { type: 'brain' as Role, label: ui.value.brain, hint: ui.value.brainHint, icon: 'lightbulb' },
  { type: 'code' as Role, label: ui.value.code, hint: ui.value.codeHint, icon: 'code' },
  { type: 'writing' as Role, label: ui.value.writing, hint: ui.value.writingHint, icon: 'edit-1' },
])
const ConnectionFields = defineComponent({
  props: {
    form: { type: Object, required: true },
    disabled: Boolean,
    ui: { type: Object, required: true },
  },
  setup(props) {
    return () =>
      h(
        'div',
        { class: 'connection-fields' },
        [
          ['apiKey', 'key', props.form.id ? props.ui.keepKey : props.ui.enterKey, 'password'],
          ['baseUrl', 'endpoint', 'https://api.deepseek.com/v1', 'text'],
          ['modelId', 'modelId', 'deepseek-flash', 'text'],
        ].map(([key, label, placeholder, type]) =>
          h('label', { class: 'connection-field' }, [
            h('span', props.ui[label as string]),
            h(Input, {
              modelValue: props.form[key as string],
              'onUpdate:modelValue': (v: string) => {
                props.form[key as string] = v
              },
              type: type as 'text' | 'password',
              placeholder,
              disabled: props.disabled,
              autocomplete: key === 'apiKey' ? 'new-password' : 'off',
              'aria-label': props.ui[label as string],
            }),
          ]),
        ),
      )
  },
})
function selectProvider(value: unknown) {
  if (value === 'deepseek') setDeepSeek()
  else customProvider.value = true
}
function setDeepSeek() {
  customProvider.value = false
  forms.brain.baseUrl = 'https://api.deepseek.com/v1'
  forms.brain.modelId = 'deepseek-flash'
}
function valid(form: Form) {
  return (
    !!form.modelId.trim() &&
    !!(form.id || form.apiKey.trim()) &&
    (!form.baseUrl.trim() || /^https?:\/\/[^\s/]+/.test(form.baseUrl.trim()))
  )
}
function adopt(config: ModelConfig) {
  forms[config.type as Role] = {
    id: config.id,
    apiKey: '',
    baseUrl: config.base_url || '',
    modelId: config.model_id,
  }
}
async function persist(role: Role) {
  const form = forms[role]
  const data = {
    model_id: form.modelId.trim(),
    base_url: form.baseUrl.trim(),
    ...(form.apiKey.trim() ? { api_key: form.apiKey.trim() } : {}),
  }
  const result = form.id
    ? await modelConfigAPI.updateModelConfig(form.id, data)
    : await modelConfigAPI.createConfig(role, { ...data, api_key: form.apiKey.trim() })
  adopt(result)
}
async function saveDefault() {
  if (saving.value || !valid(forms.brain)) return
  saving.value = true
  feedback.value = ''
  try {
    await persist('brain')
    if (applyAll.value) {
      const configs = await modelConfigAPI.applyDefault()
      configs.forEach(adopt)
    }
    feedbackError.value = false
    feedback.value = ui.value.saveSuccess
  } catch {
    feedbackError.value = true
    feedback.value = ui.value.saveFailed
  } finally {
    saving.value = false
  }
}
async function saveRole(role: Role) {
  if (savingRole.value) return
  savingRole.value = role
  try {
    await persist(role)
    MessagePlugin.success(ui.value.saveSuccess)
  } catch {
    MessagePlugin.error(ui.value.saveFailed)
  } finally {
    savingRole.value = ''
  }
}
async function testConnection() {
  if (testing.value) return
  testing.value = true
  feedback.value = ''
  try {
    await modelConfigAPI.testConnection({
      type: 'brain',
      model_id: forms.brain.modelId.trim(),
      base_url: forms.brain.baseUrl.trim(),
      ...(forms.brain.apiKey.trim() ? { api_key: forms.brain.apiKey.trim() } : {}),
    })
    feedbackError.value = false
    feedback.value = ui.value.tested
  } catch (error) {
    feedbackError.value = true
    feedback.value = `${ui.value.connectionFailed}${error instanceof Error ? ` · ${error.message}` : ''}`
  } finally {
    testing.value = false
  }
}
function confirmDelete(role: Role) {
  const dialog = DialogPlugin.confirm({
    header: ui.value.deleteTitle,
    body: ui.value.deleteHint,
    confirmBtn: { content: ui.value.confirm, theme: 'danger' },
    cancelBtn: ui.value.cancel,
    onConfirm: async () => {
      try {
        await modelConfigAPI.deleteModelConfig(forms[role].id!)
        forms[role] = { apiKey: '', baseUrl: '', modelId: '' }
        dialog.destroy()
      } catch {
        MessagePlugin.error(ui.value.saveFailed)
      }
    },
  })
}
watch(
  () => [forms.brain.apiKey, forms.brain.baseUrl, forms.brain.modelId],
  () => {
    feedback.value = ''
  },
)
onMounted(async () => {
  try {
    const configs = await modelConfigAPI.getAllModelConfigs()
    configs.filter((c) => ['brain', 'code', 'writing'].includes(c.type)).forEach(adopt)
    const b = configs.find((c) => c.type === 'brain')
    applyAll.value =
      !b || configs.every((c) => c.model_id === b.model_id && c.base_url === b.base_url)
  } catch {
    MessagePlugin.error(ui.value.saveFailed)
  } finally {
    loading.value = false
  }
})
</script>
<style scoped>
.models-page {
  display: flex;
  height: 100dvh;
  overflow: hidden;
  background: #fff;
}
.models-main {
  flex: 1;
  min-width: 0;
  overflow: auto;
  padding: 36px 40px;
}
.models-header {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 24px;
  max-width: 900px;
  margin: 0 auto 28px;
}
.models-header h1 {
  font-family: var(--td-font-family);
  font-size: 26px;
  font-weight: 600;
  margin: 0 0 10px;
}
.models-header p {
  color: var(--td-text-color-secondary);
  font-size: 14px;
  line-height: 1.8;
  margin: 0;
}
.models-body {
  max-width: 900px;
  margin: 0 auto;
}
.connection-card :deep(.t-card__title) {
  font-family: var(--td-font-family);
  font-size: 16px;
}
.field-hint {
  font-size: 13px;
  line-height: 1.8;
  color: var(--td-text-color-secondary);
  margin: 0 0 20px;
}
.provider-options {
  margin: 0 0 24px;
}
:deep(.connection-fields) {
  display: flex;
  flex-direction: column;
  gap: 22px;
}
:deep(.connection-field) {
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 14px;
  font-weight: 400;
}
:deep(.connection-field .t-input) {
  height: 36px;
}
:deep(.connection-field .t-input__inner) {
  font-size: 14px;
}
.apply-option {
  margin-top: 28px;
}
.apply-option p {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  line-height: 1.8;
  margin: 7px 0 0 24px;
}
.connection-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 28px;
}
.key-note {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  margin-left: auto;
}
.key-note > .t-icon {
  flex-shrink: 0;
}
.connection-feedback {
  margin-top: 20px;
}
.advanced-config {
  margin-top: 24px;
}
.advanced-hint {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  font-weight: 400;
}
.advanced-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 32px;
  padding: 10px 0;
}
.role-form + .role-form {
  padding-left: 32px;
  border-left: 1px solid var(--td-component-stroke);
}
.advanced-role-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.advanced-role-title h3 {
  font-family: var(--td-font-family);
  font-size: 15px;
  font-weight: 500;
  margin: 0;
}
.role-actions {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-top: 24px;
}
@media (max-width: 1100px) {
  .models-main {
    padding: 30px;
  }
  .key-note {
    margin-left: 0;
    width: 100%;
  }
}
@media (max-width: 950px) {
  .advanced-content {
    grid-template-columns: 1fr;
  }
  .role-form + .role-form {
    padding: 28px 0 0;
    border-left: 0;
    border-top: 1px solid var(--td-component-stroke);
  }
}
@media (max-width: 768px) {
  .models-main {
    padding: 20px 16px;
  }
  .models-header {
    padding-left: 44px;
    gap: 12px;
    margin-bottom: 24px;
  }
  .models-header h1 {
    font-size: 22px;
  }
  .models-header p {
    font-size: 12px;
  }
  .connection-card :deep(.t-card__header),
  .connection-card :deep(.t-card__body) {
    padding: 16px;
  }
  .connection-card :deep(.t-card__title) {
    font-size: 15px;
  }
  .advanced-hint {
    display: none;
  }
  .key-note {
    font-size: 11px;
    line-height: 1.8;
  }
  .apply-option :deep(.t-checkbox__label) {
    font-size: 13px;
  }
  .apply-option p {
    font-size: 11px;
  }
  .advanced-config :deep(.t-collapse-panel__content) {
    padding: 16px;
  }
  .connection-actions {
    margin-top: 24px;
  }
  .field-hint {
    font-size: 12px;
  }
}
</style>
