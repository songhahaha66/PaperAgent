<template>
  <div class="models-page">
    <Sidebar
      :is-sidebar-collapsed="collapsed"
      :active-history-id="null"
      @toggle-sidebar="collapsed = !collapsed"
    />
    <main class="models-main">
      <header class="models-header">
        <h1>{{ ui.modelTitle }}</h1>
      </header>
      <div class="models-body">
        <t-card :title="ui.defaultConnection" class="connection-card">
          <template #actions
            ><t-tag :theme="forms.brain.id ? 'success' : 'default'" variant="light" size="small">{{
              forms.brain.id ? ui.configured : ui.unconfigured
            }}</t-tag></template
          >
          <div class="provider-row">
            <label>{{ isZh ? '服务商' : 'Provider' }}</label
            ><t-radio-group
              variant="primary-filled"
              :value="isDeepSeek ? 'deepseek' : 'custom'"
              :disabled="saving || loading"
              @change="selectProvider"
              ><t-radio-button value="deepseek">DeepSeek</t-radio-button
              ><t-radio-button value="custom">{{ ui.custom }}</t-radio-button></t-radio-group
            >
          </div>
          <ConnectionFields :form="forms.brain" :disabled="loading || saving" :ui="ui" />
          <div class="apply-option">
            <t-checkbox v-model="applyAll" :disabled="saving">{{
              isZh ? '计算和写作使用同一连接' : 'Use this connection for computation and writing'
            }}</t-checkbox>
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
            >
          </div>
        </t-card>
        <t-card
          :title="isZh ? 'Jev 意图识别' : 'Jev intent routing'"
          class="connection-card jev-card"
        >
          <template #actions
            ><t-tag :theme="jevReady ? 'success' : 'default'" variant="light" size="small">
              {{
                jevSource === 'deployment'
                  ? isZh
                    ? '部署默认'
                    : 'Deployment default'
                  : jevReady
                    ? ui.configured
                    : ui.unconfigured
              }}
            </t-tag></template
          >
          <p class="jev-note">
            {{
              isZh
                ? '自动聊天使用 Jev 识别操作。未配置时自动聊天会提示配置，明确操作按钮不依赖 Jev。此连接独立于研究、计算和写作模型。'
                : 'Automatic chat uses Jev to select an operation. Missing configuration blocks automatic routing; explicit action buttons do not require Jev. Research, computation and writing use their own models.'
            }}
          </p>
          <p v-if="jevSource === 'deployment'" class="jev-note">
            {{
              isZh
                ? '当前使用部署默认连接。保存个人密钥后优先使用个人配置。'
                : 'Using the deployment default. A saved personal connection takes precedence.'
            }}
          </p>
          <ConnectionFields :form="forms.jev" :disabled="loading || !!savingRole" :ui="ui" jev />
          <p class="jev-note">
            {{
              isZh
                ? '密钥不回显，编辑时留空保留原密钥。连接检查会发送一次 Jev 请求，可能产生费用。'
                : 'Saved keys are never returned. Leave the key blank to keep it. Checking the connection sends a Jev request and may incur a charge.'
            }}
          </p>
          <t-alert
            v-if="jevFeedback"
            :theme="jevFeedbackError ? 'error' : 'success'"
            :message="jevFeedback"
            class="connection-feedback"
          />
          <div class="connection-actions">
            <t-button
              theme="primary"
              :loading="savingRole === 'jev'"
              :disabled="loading || !valid(forms.jev) || !!savingRole"
              @click="saveRole('jev')"
              >{{ ui.save }}</t-button
            >
            <t-button
              theme="default"
              variant="outline"
              :loading="testingJev"
              :disabled="
                loading || !!savingRole || (!valid(forms.jev) && jevSource !== 'deployment')
              "
              @click="testJevConnection"
              >{{ ui.test }}</t-button
            >
            <t-button
              v-if="forms.jev.id"
              theme="danger"
              variant="text"
              @click="confirmDelete('jev')"
              >{{ ui.delete }}</t-button
            >
          </div>
        </t-card>
        <section class="advanced-config">
          <button
            class="advanced-toggle"
            :aria-expanded="advancedOpen"
            aria-controls="stage-connections"
            @click="advancedOpen = !advancedOpen"
          >
            <h2>{{ ui.advanced }}</h2>
            <t-icon :name="advancedOpen ? 'chevron-up' : 'chevron-down'" />
          </button>
          <div id="stage-connections" v-show="advancedOpen" class="advanced-content">
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
              <ConnectionFields
                :form="forms[role.type]"
                :disabled="loading || saving || !!savingRole"
                :ui="ui"
              />
              <div class="role-actions">
                <t-button
                  theme="primary"
                  variant="base"
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
          </div>
        </section>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { Input, DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import Sidebar from '@/components/Sidebar.vue'
import { modelConfigAPI, type ModelConfig } from '@/api/modelConfig'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useStudioCopy } from '@/lang/studio'
type Role = 'brain' | 'code' | 'writing' | 'jev'
interface Form {
  id?: number
  apiKey: string
  baseUrl: string
  modelId: string
  timeoutSeconds?: number | null
}
const { ui, isZh } = useStudioCopy(),
  { isMobile } = useBreakpoint()
const advancedOpen = ref(false)
const collapsed = ref(isMobile.value)
watch(isMobile, (m) => {
  if (m) collapsed.value = true
})
const forms = reactive<Record<Role, Form>>({
  brain: { apiKey: '', baseUrl: 'https://api.deepseek.com/v1', modelId: 'deepseek-flash' },
  code: { apiKey: '', baseUrl: '', modelId: '' },
  writing: { apiKey: '', baseUrl: '', modelId: '' },
  jev: { apiKey: '', baseUrl: 'https://api.typesafe.ai', modelId: 'jev-latest' },
})
const jevSource = ref<'user' | 'deployment' | 'missing'>('missing')
const jevReady = ref(false),
  testingJev = ref(false)
const jevFeedback = ref(''),
  jevFeedbackError = ref(false)
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
    jev: Boolean,
    ui: { type: Object, required: true },
  },
  setup(props) {
    return () =>
      h(
        'div',
        { class: 'connection-fields' },
        [
          [
            'apiKey',
            'key',
            props.form.id
              ? props.ui.key === 'API 密钥'
                ? '输入新密钥以更换'
                : 'Enter a new key to replace'
              : props.ui.enterKey,
            'password',
          ],
          [
            'baseUrl',
            'endpoint',
            props.jev ? 'https://api.typesafe.ai' : 'https://api.deepseek.com/v1',
            'text',
          ],
          ['modelId', 'modelId', props.jev ? 'jev-latest' : 'deepseek-flash', 'text'],
          ['timeoutSeconds', 'requestTimeout', props.jev ? '10' : '600', 'number'],
        ].map(([key, label, placeholder, type]) =>
          h('label', { class: 'connection-field' }, [
            h(
              'span',
              label === 'requestTimeout'
                ? isZh.value
                  ? `模型请求超时（秒，留空为 ${props.jev ? '10' : '600'}）`
                  : `Request timeout (seconds, blank for ${props.jev ? '10' : '600'})`
                : props.ui[label as string],
            ),
            h(Input, {
              modelValue: props.form[key as string],
              'onUpdate:modelValue': (v: string) => {
                props.form[key as string] =
                  key === 'timeoutSeconds' ? (v === '' ? null : Number(v)) : v
              },
              type: type as 'text' | 'password' | 'number',
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
    (form.timeoutSeconds == null ||
      (Number.isFinite(form.timeoutSeconds) &&
        form.timeoutSeconds > 0 &&
        form.timeoutSeconds <= 3600)) &&
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
    modelId: config.model_id || (config.type === 'jev' ? 'jev-latest' : ''),
    timeoutSeconds: config.timeout_seconds ?? null,
  }
}
async function persist(role: Role) {
  const form = forms[role]
  const data = {
    model_id: form.modelId.trim(),
    base_url: form.baseUrl.trim(),
    timeout_seconds: form.timeoutSeconds ?? null,
    ...(role === 'jev' ? { is_active: true } : {}),
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
      configs.filter((config) => config.type !== 'jev').forEach(adopt)
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
    if (role === 'jev') await loadJevReadiness()
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
    feedback.value = `${ui.value.connectionFailed}${error instanceof Error ? `：${error.message}` : ''}`
  } finally {
    testing.value = false
  }
}
async function loadJevReadiness() {
  const readiness = await modelConfigAPI.readiness()
  jevReady.value = !!readiness.roles.jev
  jevSource.value = readiness.jev.source
  if (!forms.jev.id && readiness.jev.source === 'deployment') {
    forms.jev.modelId = readiness.jev.model_id || 'jev-latest'
    forms.jev.baseUrl = readiness.jev.base_url || 'https://api.typesafe.ai'
    forms.jev.timeoutSeconds = readiness.jev.timeout_seconds ?? null
  }
}
async function testJevConnection() {
  if (testingJev.value) return
  testingJev.value = true
  jevFeedback.value = ''
  try {
    await modelConfigAPI.testConnection({
      type: 'jev',
      model_id: forms.jev.modelId.trim(),
      base_url: forms.jev.baseUrl.trim(),
      timeout_seconds: forms.jev.timeoutSeconds ?? null,
      ...(forms.jev.apiKey.trim() ? { api_key: forms.jev.apiKey.trim() } : {}),
    })
    jevFeedbackError.value = false
    jevFeedback.value = isZh.value ? 'Jev 连接检查通过' : 'Jev connection check passed'
  } catch (error) {
    jevFeedbackError.value = true
    jevFeedback.value =
      error instanceof Error
        ? error.message
        : isZh.value
          ? 'Jev 连接检查失败'
          : 'Jev connection check failed'
  } finally {
    testingJev.value = false
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
        if (role === 'jev') {
          forms.jev = { apiKey: '', baseUrl: 'https://api.typesafe.ai', modelId: 'jev-latest' }
          await loadJevReadiness()
        }
        dialog.destroy()
      } catch {
        MessagePlugin.error(ui.value.saveFailed)
      }
    },
  })
}
watch(
  () => [forms.jev.apiKey, forms.jev.baseUrl, forms.jev.modelId, forms.jev.timeoutSeconds],
  () => {
    jevFeedback.value = ''
  },
)
watch(
  () => [forms.brain.apiKey, forms.brain.baseUrl, forms.brain.modelId],
  () => {
    feedback.value = ''
  },
)
onMounted(async () => {
  try {
    const configs = await modelConfigAPI.getAllModelConfigs()
    configs.filter((c) => ['brain', 'code', 'writing', 'jev'].includes(c.type)).forEach(adopt)
    const b = configs.find((c) => c.type === 'brain')
    applyAll.value =
      !b ||
      configs
        .filter((c) => ['brain', 'code', 'writing'].includes(c.type))
        .every((c) => c.model_id === b.model_id && c.base_url === b.base_url)
    await loadJevReadiness()
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
  background: var(--pa-page);
}
.models-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 38px 40px 56px;
}
.models-header {
  max-width: 760px;
  margin: 0 auto 26px;
}
.models-header h1 {
  font-family: var(--td-font-family);
  font-size: 26px;
  font-weight: 600;
  margin: 0;
  color: var(--pa-ink);
  line-height: 1.4;
}
.models-body {
  max-width: 760px;
  margin: 0 auto;
}
.connection-card {
  border-color: var(--pa-line);
  border-radius: var(--pa-radius-card);
  box-shadow: none;
}
.jev-card {
  margin-top: 20px;
}
.jev-note {
  color: var(--pa-muted);
  font-size: 13px;
  line-height: 1.8;
  margin: 0 0 22px;
}
.connection-card :deep(.t-card__header) {
  padding: 22px 26px;
  border-bottom: 1px solid var(--pa-line);
}
.connection-card :deep(.t-card__title) {
  font-family: var(--td-font-family);
  font-size: 16px;
  font-weight: 600;
  color: var(--pa-ink);
}
.connection-card :deep(.t-card__body) {
  padding: 26px;
}
.connection-card :deep(.t-tag),
.advanced-role-title :deep(.t-tag) {
  border-radius: var(--pa-radius-inline);
  font-size: 11px;
  font-weight: 500;
}
.provider-row {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}
.provider-row > label {
  font-size: 13px;
  font-weight: 500;
  color: var(--pa-ink);
}
.provider-row > .t-radio-group {
  justify-self: start;
}
.provider-row :deep(.t-radio-button) {
  font-size: 13px;
  padding: 0 18px;
}
.provider-row :deep(.t-radio-group) {
  border-radius: var(--pa-radius-control);
  overflow: hidden;
}
:deep(.connection-fields) {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
:deep(.connection-field) {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  align-items: center;
  gap: 16px;
}
:deep(.connection-field > span) {
  font-size: 13px;
  font-weight: 500;
  color: var(--pa-ink);
}
:deep(.connection-field .t-input) {
  height: var(--pa-control-height-lg);
  border-color: var(--pa-line-strong);
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface);
  box-shadow: none;
}
:deep(.connection-field .t-input:hover) {
  border-color: var(--pa-line-strong);
}
:deep(.connection-field .t-input:focus-within) {
  border-color: var(--td-brand-color);
  background: var(--pa-surface);
  box-shadow: var(--pa-focus-ring);
}
:deep(.connection-field .t-input__inner) {
  font-size: 14px;
  color: var(--pa-ink);
}
:deep(.connection-field .t-input__inner::placeholder) {
  font-size: 13px;
  color: var(--pa-placeholder);
}
.apply-option {
  margin: 24px 0 0 126px;
}
.apply-option :deep(.t-checkbox__label) {
  font-size: 13px;
  color: var(--pa-ink);
}
.connection-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  border-top: 1px solid var(--pa-line);
  margin: 26px -26px -26px;
  padding: 18px 26px 18px 152px;
}
.connection-actions .t-button {
  font-size: 13px;
  height: var(--pa-control-height-md);
  border-radius: var(--pa-radius-control);
  padding: 0 18px;
}
.connection-feedback {
  margin: 22px 0 0 126px;
  border-radius: var(--pa-radius-control);
  font-size: 13px;
}
.advanced-config {
  margin-top: 20px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  overflow: hidden;
  background: var(--pa-surface);
}
.advanced-toggle {
  border: 0;
  background: var(--pa-surface);
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 21px 26px;
  text-align: left;
  cursor: pointer;
  color: var(--pa-ink);
}
.advanced-toggle:hover {
  background: var(--pa-surface-hover);
}
.advanced-toggle h2 {
  font-family: var(--td-font-family);
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  line-height: 1.5;
}
.advanced-toggle > .t-icon {
  font-size: 16px;
  color: var(--pa-muted);
  flex-shrink: 0;
}
.advanced-content {
  padding: 26px;
  border-top: 1px solid var(--pa-line);
  background: var(--pa-surface);
}
.advanced-role-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}
.advanced-role-title h3 {
  font-family: var(--td-font-family);
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  color: var(--pa-ink);
}
.role-form + .role-form {
  border-top: 1px solid var(--pa-line);
  margin-top: 30px;
  padding-top: 28px;
}
.role-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 24px 0 0 126px;
}
.role-actions .t-button {
  font-size: 13px;
  height: var(--pa-control-height-md);
  border-radius: var(--pa-radius-control);
}
@media (max-width: 1100px) {
  .models-main {
    padding: 30px 28px 44px;
  }
}
@media (max-width: 768px) {
  .models-main {
    padding: 26px 20px 36px;
  }
  .models-header {
    margin-bottom: 22px;
  }
  .models-header h1 {
    font-size: 23px;
  }
  .connection-card :deep(.t-card__header) {
    padding: 20px;
  }
  .connection-card :deep(.t-card__body) {
    padding: 22px 20px;
  }
  .provider-row {
    display: flex;
    align-items: center;
    gap: 20px;
    margin-bottom: 24px;
  }
  .provider-row > label {
    min-width: 58px;
  }
  .provider-row :deep(.t-radio-button) {
    padding: 0 17px;
  }
  :deep(.connection-field) {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
  :deep(.connection-fields) {
    gap: 22px;
  }
  :deep(.connection-field > span) {
    font-size: 13px;
  }
  :deep(.connection-field .t-input) {
    height: 41px;
  }
  .apply-option {
    margin: 24px 0 0;
  }
  .apply-option :deep(.t-checkbox__label) {
    font-size: 12px;
  }
  .connection-actions {
    margin: 24px -20px -22px;
    padding: 18px 20px;
  }
  .connection-feedback {
    margin: 20px 0 0;
  }
  .advanced-toggle {
    padding: 20px;
  }
  .advanced-content {
    padding: 22px 20px;
  }
  .advanced-role-title {
    margin-bottom: 22px;
  }
  .role-actions {
    margin: 22px 0 0;
  }
  .role-form + .role-form {
    margin-top: 28px;
    padding-top: 26px;
  }
}
@media (max-width: 360px) {
  .models-main {
    padding: 22px 14px 32px;
  }
  .provider-row {
    gap: 12px;
  }
  .provider-row :deep(.t-radio-button) {
    padding: 0 12px;
  }
}
</style>
