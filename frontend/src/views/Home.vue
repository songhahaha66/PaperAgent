<template>
  <div class="home-page">
    <Sidebar
      :is-sidebar-collapsed="collapsed"
      :active-history-id="null"
      @toggle-sidebar="collapsed = !collapsed"
      @create-new-task="resetDraft"
    />
    <main class="home-main">
      <header class="home-header">
        <div>
          <h1>{{ ui.title }}</h1>
          <p>{{ ui.subtitle }}</p>
        </div>
        <LanguageSwitcher />
      </header>
      <section class="home-content">
        <t-card :title="ui.question" :bordered="true"
          ><template #actions
            ><t-button theme="default" variant="text" size="small" @click="fillExample"
              ><template #icon><t-icon name="lightbulb" /></template>{{ ui.example }}</t-button
            ></template
          >
          <t-textarea
            id="research-question"
            v-model="question"
            :placeholder="ui.placeholder"
            :autosize="{ minRows: 5, maxRows: 10 }"
            :disabled="creating"
            class="research-input"
          />
          <div class="attachment-area">
            <t-upload
              v-model="files"
              :auto-upload="false"
              multiple
              accept=".csv,.tsv,.xls,.xlsx,.json,.pdf,.doc,.docx,.tex,.txt,.zip,.png,.jpg,.jpeg,.gif,.webp"
              :max="10"
              :size-limit="{ size: 50, unit: 'MB' }"
              :disabled="creating"
              ><t-button variant="outline" theme="default"
                ><template #icon><t-icon name="attach" /></template>{{ ui.attachments }}</t-button
              ></t-upload
            >
            <p class="field-hint">{{ ui.fileHint }}</p>
          </div>
          <t-divider />
          <div class="output-fields">
            <div class="output-field">
              <label class="field-label" id="output-label">{{ ui.output }}</label
              ><t-radio-group
                v-model="mode"
                variant="default-filled"
                :disabled="creating"
                aria-labelledby="output-label"
                ><t-radio-button
                  v-for="format in formats"
                  :key="format.value"
                  :value="format.value"
                  >{{ format.label }}</t-radio-button
                ></t-radio-group
              >
              <p class="field-hint">{{ formats.find((f) => f.value === mode)?.hint }}</p>
            </div>
            <div class="output-field">
              <label class="field-label"
                >{{ ui.template }} <span>{{ ui.optional }}</span></label
              ><t-select
                v-model="templateId"
                :loading="loadingTemplates"
                :disabled="creating || mode === 'latex'"
                :aria-label="ui.template"
                ><t-option
                  :value="0"
                  :label="mode === 'latex' ? ui.builtIn : ui.noTemplate" /><t-option
                  v-for="item in templates"
                  :key="item.id"
                  :value="item.id"
                  :label="item.name" /></t-select
              ><router-link to="/template" class="manage-link">{{
                ui.manageTemplates
              }}</router-link>
            </div>
          </div>
          <div class="form-footer">
            <router-link to="/api-config" class="model-link"
              ><t-icon :name="modelsReady ? 'check-circle' : 'setting'" /><span>{{
                modelsReady ? ui.ready : ui.notReady
              }}</span></router-link
            ><t-button
              theme="primary"
              :loading="creating"
              :disabled="!question.trim()"
              @click="startWork"
              >{{ creating ? ui.starting : ui.start
              }}<template #suffix><t-icon name="arrow-right" /></template
            ></t-button>
          </div>
          <t-alert
            v-if="creationError"
            theme="error"
            :message="creationError"
            class="creation-error"
          />
        </t-card>
        <section v-if="recentWorks.length" class="recent-section">
          <h2>{{ ui.recent }}</h2>
          <t-list :split="true"
            ><t-list-item v-for="work in recentWorks" :key="work.work_id"
              ><t-list-item-meta
                :title="work.title.trim() || ui.untitled"
                :description="formatWorkDate(work.created_at, locale)" /><template #action
                ><t-space
                  ><t-tag size="small" variant="light">{{ statusLabel(work.status) }}</t-tag
                  ><t-button
                    theme="default"
                    variant="text"
                    size="small"
                    @click="router.push(`/work/${work.work_id}`)"
                    >{{ ui.openWork
                    }}<template #suffix
                      ><t-icon
                        name="chevron-right" /></template></t-button></t-space></template></t-list-item
          ></t-list>
        </section>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { UploadFile } from 'tdesign-vue-next'
import { templateAPI, type PaperTemplate } from '@/api/template'
import { attachmentAPI, workspaceAPI, type Work } from '@/api/workspace'
import { modelConfigAPI } from '@/api/modelConfig'
import { useAuthStore } from '@/stores/auth'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useStudioCopy } from '@/lang/studio'
import { formatWorkDate } from '@/utils/dates'
import Sidebar from '@/components/Sidebar.vue'
import LanguageSwitcher from '@/components/LanguageSwitcher.vue'
const { ui, isZh } = useStudioCopy()
const { t, locale } = useI18n()
const { isMobile } = useBreakpoint()
const collapsed = ref(isMobile.value)
watch(isMobile, (mobile) => {
  if (mobile) collapsed.value = true
})
const auth = useAuthStore(),
  router = useRouter()
const question = ref(''),
  mode = ref<'markdown' | 'word' | 'latex'>('latex'),
  templateId = ref(0)
const files = ref<UploadFile[]>([]),
  templates = ref<PaperTemplate[]>([]),
  recentWorks = ref<Work[]>([])
const loadingTemplates = ref(false),
  creating = ref(false),
  modelsReady = ref(false),
  creationError = ref('')
const formats = computed(() => [
  { value: 'latex' as const, label: ui.value.pdf, hint: ui.value.pdfHint, icon: 'file-pdf' },
  { value: 'word' as const, label: ui.value.word, hint: ui.value.wordHint, icon: 'file-word' },
  { value: 'markdown' as const, label: ui.value.md, hint: ui.value.mdHint, icon: 'file-1' },
])
function statusLabel(status: string) {
  return t(
    `sidebar.${({ created: 'created', in_progress: 'inProgress', running: 'inProgress', completed: 'completed', paused: 'paused', failed: 'failed', cancelled: 'cancelled', waiting: 'waiting' } as Record<string, string>)[status] || 'created'}`,
  )
}
let templateRequest = 0
async function loadTemplates() {
  const request = ++templateRequest
  templateId.value = 0
  if (!auth.token || mode.value === 'latex') {
    templates.value = []
    loadingTemplates.value = false
    return
  }
  loadingTemplates.value = true
  try {
    const result = await templateAPI.getUserTemplates(auth.token, 0, 100, mode.value)
    if (request === templateRequest) templates.value = result
  } catch {
    if (request === templateRequest) templates.value = []
  } finally {
    if (request === templateRequest) loadingTemplates.value = false
  }
}
watch(mode, loadTemplates)
function fillExample() {
  question.value = isZh.value
    ? '请研究某高校食堂的午餐备餐问题。最近 20 个工作日销量（份）依次为：420、435、410、460、475、430、448、425、482、490、445、455、438、498、510、458、472、450、515、530。每 5 天为周一至周五。比较移动平均与线性趋势两种预测方法，用最后 5 天验证误差，预测下一周每日需求。假设每份售价 15 元、成本 8 元、剩余残值 2 元，讨论合理备餐量及不确定性。请保留代码、输出图表，并写出包含假设、建模、验证和局限性的简短论文。'
    : 'Study a university cafeteria’s lunch preparation. Sales for the last 20 weekdays were: 420, 435, 410, 460, 475, 430, 448, 425, 482, 490, 445, 455, 438, 498, 510, 458, 472, 450, 515, 530 portions. Each group of five is Monday–Friday. Compare moving averages and a linear trend, validate on the final five days, and forecast next week. With a selling price of 15, cost of 8 and salvage value of 2 per portion, discuss preparation quantities and uncertainty. Keep reproducible code and charts and write a short paper with assumptions, validation and limitations.'
  document.getElementById('research-question')?.focus()
}
let draft: Work | null = null
const uploaded = new WeakSet<File>()
async function startWork() {
  if (!auth.token || !question.value.trim() || creating.value) return
  creating.value = true
  creationError.value = ''
  try {
    const data = {
      title: ' ',
      description: question.value,
      tags: '研究,论文',
      output_mode: mode.value,
      template_id: templateId.value || undefined,
    }
    if (!draft) draft = await workspaceAPI.createWork(auth.token, data)
    else
      await workspaceAPI.updateWork(auth.token, draft.work_id, {
        ...data,
        template_id: templateId.value || undefined,
      })
    for (const item of files.value) {
      const raw = item.raw
      if (!raw)
        throw new Error(
          isZh.value
            ? '附件不可读取，请移除后重新添加。'
            : 'Cannot read attachment. Please remove it and add it again.',
        )
      if (!uploaded.has(raw)) {
        await attachmentAPI.uploadAttachment(auth.token, draft.work_id, raw)
        uploaded.add(raw)
      }
    }
    localStorage.setItem(`pendingQuestion:${draft.work_id}`, question.value.trim())
    await router.push(`/work/${draft.work_id}`)
  } catch (error) {
    creationError.value = `${isZh.value ? '准备工作空间失败，已保留输入。可重试：' : 'Could not prepare workspace. Your input is preserved. Retry: '}${error instanceof Error ? error.message : ''}`
  } finally {
    creating.value = false
  }
}
function resetDraft() {
  if (creating.value) return
  question.value = ''
  files.value = []
  templateId.value = 0
  draft = null
  creationError.value = ''
}
onMounted(async () => {
  if (!auth.token) return
  const results = await Promise.allSettled([
    modelConfigAPI.getAllModelConfigs(),
    workspaceAPI.getWorks(auth.token, 0, 3),
  ])
  const configs = results[0]
  if (configs.status === 'fulfilled')
    modelsReady.value = ['brain', 'code', 'writing'].every((role) =>
      configs.value.some((c) => c.type === role && c.is_active),
    )
  const works = results[1]
  if (works.status === 'fulfilled') recentWorks.value = works.value.works
})
</script>
<style scoped>
.home-page {
  display: flex;
  height: 100dvh;
  background: #fff;
  overflow: hidden;
}
.home-main {
  flex: 1;
  min-width: 0;
  overflow: auto;
  padding: 36px 40px;
}
.home-header {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 24px;
  max-width: 900px;
  margin: 0 auto 28px;
}
.home-header h1 {
  font-family: var(--td-font-family);
  font-size: 26px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  margin: 0 0 10px;
}
.home-header p {
  font-size: 14px;
  color: var(--td-text-color-secondary);
  line-height: 1.8;
  margin: 0;
}
.home-content {
  max-width: 900px;
  margin: 0 auto;
}
.home-content :deep(.t-card__title) {
  font-family: var(--td-font-family);
  font-size: 16px;
}
.research-input :deep(textarea) {
  font-size: 14px;
  line-height: 1.8;
  padding: 14px;
}
.attachment-area {
  margin-top: 16px;
}
.field-hint {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  line-height: 1.7;
  margin: 10px 0 0;
}
.output-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 32px;
}
.output-field {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: start;
}
.output-field > .t-select__wrap {
  width: 100%;
}
.field-label {
  display: block;
  font-size: 14px;
  margin-bottom: 12px;
  font-weight: 500;
}
.field-label > span {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  font-weight: 400;
  margin-left: 6px;
}
.manage-link {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  margin-top: 10px;
  text-decoration: none;
}
.manage-link:hover {
  text-decoration: underline;
}
.form-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 30px;
}
.model-link {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 6px;
}
.model-link:hover {
  color: var(--td-text-color-primary);
}
.recent-section {
  margin-top: 32px;
}
.recent-section h2 {
  font-family: var(--td-font-family);
  font-size: 16px;
  font-weight: 500;
  margin: 0 0 10px;
}
.recent-section :deep(.t-list-item) {
  padding: 16px 0;
}
.recent-section :deep(.t-list-item__meta-title) {
  font-size: 14px;
}
.recent-section :deep(.t-list-item__meta-description) {
  font-size: 12px;
}
.creation-error {
  margin-top: 16px;
}
@media (max-width: 1150px) {
  .home-main {
    padding: 30px;
  }
  .output-fields {
    gap: 24px;
  }
  .output-field :deep(.t-radio-button) {
    padding: 0 12px;
  }
}
@media (max-width: 950px) {
  .output-fields {
    grid-template-columns: 1fr;
    gap: 24px;
  }
}
@media (max-width: 768px) {
  .home-main {
    padding: 20px 16px;
  }
  .home-header {
    padding-left: 44px;
    gap: 12px;
    margin-bottom: 24px;
    min-height: 50px;
  }
  .home-header h1 {
    font-size: 22px;
  }
  .home-header p {
    font-size: 12px;
  }
  .home-header :deep(.language-switcher) {
    min-width: 76px;
  }
  .home-content :deep(.t-card__header) {
    padding: 16px;
  }
  .home-content :deep(.t-card__body) {
    padding: 16px;
  }
  .home-content :deep(.t-card__title) {
    font-size: 15px;
  }
  .output-fields {
    grid-template-columns: 1fr;
    gap: 24px;
  }
  .output-field :deep(.t-radio-group) {
    width: 100%;
    display: flex;
  }
  .output-field :deep(.t-radio-button) {
    flex: 1;
    text-align: center;
    padding: 0 8px;
    font-size: 12px;
  }
  .research-input :deep(textarea) {
    font-size: 14px;
    padding: 12px;
  }
  .attachment-area > .field-hint {
    font-size: 11px;
  }
  .form-footer {
    margin-top: 26px;
  }
  .recent-section :deep(.t-list-item) {
    align-items: flex-start;
    gap: 12px;
  }
  .recent-section :deep(.t-list-item__action) {
    margin-left: 0;
  }
  .recent-section :deep(.t-space) {
    flex-direction: column;
    align-items: end;
  }
  .recent-section :deep(.t-list-item__meta) {
    min-width: 0;
  }
  .recent-section :deep(.t-list-item__meta-title) {
    line-height: 1.7;
    font-size: 13px;
  }
  .recent-section :deep(.t-list-item__meta-description) {
    font-size: 11px;
  }
}
</style>
