<template>
  <div class="home-page">
    <Sidebar
      :is-sidebar-collapsed="collapsed"
      :active-history-id="null"
      @toggle-sidebar="collapsed = !collapsed"
      @create-new-task="resetDraft"
    />
    <main class="home-main">
      <header v-if="!isMobile" class="home-header"><span>PaperAgent</span></header>
      <section class="home-content" :aria-label="ui.newPaper">
        <h1>{{ isZh ? '想研究什么？' : 'What would you like to research?' }}</h1>
        <div class="research-composer" :class="{ 'is-busy': creating }">
          <div v-if="files.length" class="attachment-chips">
            <div v-for="(file, index) in files" :key="index" class="attachment-chip">
              <t-icon name="file-1" /><span :title="file.name">{{ file.name }}</span
              ><t-button
                theme="default"
                variant="text"
                shape="circle"
                size="small"
                :disabled="creating"
                :aria-label="`${isZh ? '移除附件' : 'Remove attachment'} ${file.name}`"
                @click="files.splice(index, 1)"
                ><t-icon name="close"
              /></t-button>
            </div>
          </div>
          <t-textarea
            id="research-question"
            ref="questionInput"
            v-model="question"
            :placeholder="
              isZh
                ? '输入研究题目，或添加题目与数据文件…'
                : 'Describe your research, or attach a question and data…'
            "
            :autosize="{ minRows: 3, maxRows: 10 }"
            :disabled="creating"
            :aria-label="ui.question"
            class="research-input"
          />
          <div class="composer-toolbar">
            <input
              ref="attachmentInput"
              :disabled="creating"
              type="file"
              multiple
              hidden
              accept=".csv,.tsv,.xls,.xlsx,.json,.pdf,.doc,.docx,.tex,.txt,.zip,.png,.jpg,.jpeg,.gif,.webp"
              @change="addAttachments"
            />
            <t-tooltip :content="ui.attachments"
              ><t-button
                theme="default"
                variant="text"
                shape="circle"
                class="attach-button"
                :disabled="creating"
                :aria-label="ui.attachments"
                @click="attachmentInput?.click()"
                ><t-icon name="add" /></t-button
            ></t-tooltip>
            <t-button
              theme="default"
              variant="text"
              shape="round"
              class="layout-button"
              :disabled="creating"
              :aria-expanded="layoutOpen"
              @click="layoutOpen = !layoutOpen"
              ><t-icon name="file-copy" /><span>{{ ui.template }}</span
              ><t-icon :name="layoutOpen ? 'chevron-up' : 'chevron-down'"
            /></t-button>
            <div class="composer-spacer" />
            <t-select
              v-model="mode"
              :disabled="creating"
              borderless
              :aria-label="ui.output"
              class="format-select"
              ><t-option
                v-for="format in formats"
                :key="format.value"
                :value="format.value"
                :label="format.label"
            /></t-select>
            <t-tooltip :content="creating ? ui.starting : ui.start"
              ><t-button
                theme="primary"
                shape="circle"
                class="start-button"
                :loading="creating"
                :disabled="!question.trim() || creating"
                :aria-label="creating ? ui.starting : ui.start"
                @click="startWork"
                ><t-icon name="arrow-up" /></t-button
            ></t-tooltip>
          </div>
          <div v-if="layoutOpen" class="layout-settings">
            <div>
              <label>{{ ui.template }}</label
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
                  :label="item.name"
              /></t-select>
            </div>
            <router-link to="/template"
              >{{ ui.manageTemplates }}<t-icon name="chevron-right"
            /></router-link>
          </div>
        </div>
        <t-alert
          v-if="creationError"
          theme="error"
          :message="creationError"
          class="creation-error"
        />
        <div class="composer-meta">
          <span>{{ isZh ? '支持 CSV、Excel、PDF、Word 等' : 'CSV, Excel, PDF, Word & more' }}</span
          ><router-link to="/api-config"
            ><span :class="['readiness-dot', { ready: modelsReady }]" />{{
              modelsReady ? ui.ready : ui.notReady
            }}</router-link
          >
        </div>
        <div class="suggestions">
          <button :disabled="creating" @click="fillExample">
            <t-icon name="lightbulb" /><span>{{
              isZh ? '试试食堂需求预测示例' : 'Try a cafeteria forecasting example'
            }}</span
            ><t-icon name="arrow-top-right" /></button
          ><button :disabled="creating" @click="attachmentInput?.click()">
            <t-icon name="attach" /><span>{{
              isZh ? '添加题目与数据，开始分析' : 'Add your question and data for analysis'
            }}</span
            ><t-icon name="arrow-top-right" /></button
          ><button v-if="recentWorks[0]" @click="router.push(`/work/${recentWorks[0].work_id}`)">
            <t-icon name="history" /><span
              >{{ isZh ? '继续研究：' : 'Continue: '
              }}{{ recentWorks[0].title.trim() || ui.untitled }}</span
            ><t-icon name="arrow-top-right" />
          </button>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { MessagePlugin, type UploadFile } from 'tdesign-vue-next'
import { templateAPI, type PaperTemplate } from '@/api/template'
import { attachmentAPI, workspaceAPI, type Work } from '@/api/workspace'
import { modelConfigAPI } from '@/api/modelConfig'
import { useAuthStore } from '@/stores/auth'
import { useWorkHistoryStore } from '@/stores/workHistory'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useStudioCopy } from '@/lang/studio'
import Sidebar from '@/components/Sidebar.vue'
const { ui, isZh } = useStudioCopy()
const { isMobile } = useBreakpoint()
const collapsed = ref(isMobile.value)
watch(isMobile, (mobile) => {
  if (mobile) collapsed.value = true
})
const historyStore = useWorkHistoryStore()
const auth = useAuthStore(),
  router = useRouter()
const question = ref(''),
  mode = ref<'markdown' | 'word' | 'latex'>('latex'),
  templateId = ref(0)
const files = ref<UploadFile[]>([]),
  templates = ref<PaperTemplate[]>([])
const recentWorks = computed(() => historyStore.works.slice(0, 3))
const loadingTemplates = ref(false),
  creating = ref(false),
  modelsReady = ref(false),
  creationError = ref('')
const formats = computed(() => [
  { value: 'latex' as const, label: ui.value.pdf, hint: ui.value.pdfHint, icon: 'file-pdf' },
  { value: 'word' as const, label: ui.value.word, hint: ui.value.wordHint, icon: 'file-word' },
  { value: 'markdown' as const, label: ui.value.md, hint: ui.value.mdHint, icon: 'file-1' },
])
const layoutOpen = ref(false)
const questionInput = ref<{ focus: () => void } | null>(null)
const attachmentInput = ref<HTMLInputElement | null>(null)
function addAttachments(event: Event) {
  const input = event.target as HTMLInputElement
  for (const raw of Array.from(input.files || [])) {
    if (files.value.length >= 10) {
      MessagePlugin.warning(isZh.value ? '最多添加 10 个附件' : 'Up to 10 attachments')
      break
    }
    if (raw.size > 50 * 1024 * 1024) {
      MessagePlugin.warning(
        `${raw.name}: ${isZh.value ? '单个文件不能超过 50 MB' : 'Files must be under 50 MB'}`,
      )
      continue
    }
    files.value.push({ name: raw.name, size: raw.size, raw })
  }
  input.value = ''
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
  if (creating.value) return
  question.value = isZh.value
    ? '请研究某高校食堂的午餐备餐问题。最近 20 个工作日销量（份）依次为：420、435、410、460、475、430、448、425、482、490、445、455、438、498、510、458、472、450、515、530。每 5 天为周一至周五。比较移动平均与线性趋势两种预测方法，用最后 5 天验证误差，预测下一周每日需求。假设每份售价 15 元、成本 8 元、剩余残值 2 元，讨论合理备餐量及不确定性。请保留代码、输出图表，并写出包含假设、建模、验证和局限性的简短论文。'
    : 'Study a university cafeteria’s lunch preparation. Sales for the last 20 weekdays were: 420, 435, 410, 460, 475, 430, 448, 425, 482, 490, 445, 455, 438, 498, 510, 458, 472, 450, 515, 530 portions. Each group of five is Monday–Friday. Compare moving averages and a linear trend, validate on the final five days, and forecast next week. With a selling price of 15, cost of 8 and salvage value of 2 per portion, discuss preparation quantities and uncertainty. Keep reproducible code and charts and write a short paper with assumptions, validation and limitations.'
  questionInput.value?.focus()
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
    historyStore.upsert(draft)
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
  layoutOpen.value = false
}
onMounted(async () => {
  if (!auth.token) return
  void historyStore.ensureLoaded()
  try {
    const configs = await modelConfigAPI.getAllModelConfigs()
    modelsReady.value = ['brain', 'code', 'writing'].every((role) =>
      configs.some((config) => config.type === role && config.is_active),
    )
  } catch {
    modelsReady.value = false
  }
})
</script>
<style scoped>
.home-page {
  display: flex;
  height: 100dvh;
  overflow: hidden;
  background: #fff;
}
.home-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  position: relative;
  background: #fff;
}
.home-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 72px;
  padding: 18px 30px;
  gap: 20px;
}
.home-header > span {
  font-size: 17px;
  font-weight: 500;
  color: var(--td-text-color-primary);
}
.home-header :deep(.language-switcher) {
  border: 0;
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: 13px;
}
.home-content {
  width: calc(100% - 80px);
  max-width: 780px;
  margin: clamp(44px, 13vh, 140px) auto 64px;
}
.home-content h1 {
  font-family: var(--td-font-family);
  font-size: 30px;
  font-weight: 600;
  line-height: 1.5;
  text-align: center;
  margin: 0 0 28px;
  color: var(--td-text-color-primary);
  letter-spacing: -0.4px;
}
.research-composer {
  border: 1px solid #d9dedd;
  border-radius: 24px;
  background: #fcfcfc;
  box-shadow: 0 4px 18px #00000007;
  transition:
    border-color 0.2s,
    box-shadow 0.2s;
  overflow: hidden;
  padding: 7px 10px 10px;
}
.research-composer:focus-within {
  border-color: #9db5a8;
  box-shadow:
    0 0 0 3px #16865d08,
    0 4px 18px #00000007;
}
.research-input {
  width: 100%;
}
.research-input :deep(.t-textarea__inner) {
  border: 0 !important;
  box-shadow: none !important;
  background: transparent !important;
  border-radius: 0 !important;
  padding: 12px 12px 10px;
  font-size: 16px;
  line-height: 1.8;
  resize: none;
}
.research-input :deep(.t-textarea__inner::placeholder) {
  color: #818181;
  font-size: 15px;
}
.research-input :deep(.t-textarea__inner:focus-visible) {
  outline: none;
}
.composer-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 7px;
  padding: 9px 0 0;
  min-height: 46px;
  border-top: 1px solid #eceeed;
}
.composer-toolbar .attach-button {
  width: 36px;
  height: 36px;
  color: #333;
  font-size: 23px;
  flex-shrink: 0;
}
.composer-toolbar .layout-button {
  font-size: 12px;
  color: #555;
  height: 34px;
  padding: 0 9px;
}
.layout-button :deep(.t-button__text) {
  display: flex;
  align-items: center;
  gap: 5px;
}
.layout-button .t-icon {
  font-size: 15px;
}
.layout-button .t-icon:last-child {
  font-size: 12px;
}
.composer-spacer {
  flex: 1;
}
.format-select {
  width: 126px;
  flex-shrink: 0;
}
.format-select :deep(.t-input) {
  background: transparent !important;
  border-radius: 18px;
  color: #4d4d4d;
  font-size: 13px;
  font-weight: 500;
}
.format-select :deep(.t-input:hover) {
  background: #f6f6f6 !important;
}
.format-select :deep(.t-input__suffix) {
  color: #999;
}
.composer-toolbar .start-button {
  width: 36px;
  height: 36px;
  min-width: 36px;
  flex-shrink: 0;
  margin: 0 2px 0 5px;
  font-size: 22px;
}
.composer-toolbar .start-button.t-is-disabled {
  background: #eee;
  color: #aaa;
  border-color: #eee;
}
.attachment-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 10px 2px;
}
.attachment-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #e9e9e9;
  border-radius: 14px;
  padding: 7px 7px 7px 12px;
  max-width: 250px;
  font-size: 12px;
  color: #666;
}
.attachment-chip > .t-icon {
  font-size: 19px;
  color: #737373;
  flex-shrink: 0;
}
.attachment-chip > span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.attachment-chip .t-button {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  font-size: 12px;
}
.composer-meta {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: center;
  padding: 14px 15px 0;
  color: #777;
  font-size: 12px;
  line-height: 1.7;
}
.composer-meta a {
  color: #777;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.composer-meta a:hover {
  color: #555;
}
.readiness-dot {
  width: 5px;
  height: 5px;
  background: #c7a270;
  border-radius: 50%;
}
.readiness-dot.ready {
  background: #79a38c;
}
.suggestions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 20px;
  padding: 0 9px;
}
.suggestions > button {
  display: flex;
  align-items: center;
  gap: 13px;
  border: 0;
  background: transparent;
  border-radius: 12px;
  padding: 13px 12px;
  text-align: left;
  color: #636363;
  font-size: 14px;
  cursor: pointer;
  width: 100%;
  min-width: 0;
  transition:
    background 0.15s,
    color 0.15s;
}
.suggestions > button:hover {
  background: #f7f7f7;
  color: #555;
}
.suggestions > button > .t-icon {
  font-size: 19px;
  color: #737373;
  flex-shrink: 0;
}
.suggestions > button > .t-icon:last-child {
  font-size: 14px;
  color: #aaa;
  margin-left: auto;
  opacity: 0;
}
.suggestions > button:hover > .t-icon:last-child,
.suggestions > button:focus-visible > .t-icon:last-child {
  opacity: 1;
}
.suggestions > button > span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.layout-settings {
  border-top: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  gap: 16px;
  margin: 10px 9px 0;
  padding: 16px 3px 6px;
}
.layout-settings > div {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 1;
  min-width: 0;
}
.layout-settings label {
  font-size: 12px;
  color: #777;
  white-space: nowrap;
}
.layout-settings :deep(.t-select__wrap) {
  flex: 1;
  min-width: 0;
}
.layout-settings :deep(.t-input) {
  border-radius: 10px;
}
.layout-settings > a {
  display: flex;
  gap: 4px;
  align-items: center;
  text-decoration: none;
  color: #888;
  font-size: 12px;
  white-space: nowrap;
}
.creation-error {
  margin-top: 16px;
  border-radius: 12px;
}
@media (min-width: 1700px) {
  .home-content {
    margin-top: clamp(80px, 16vh, 190px);
  }
}
@media (max-width: 1000px) {
  .home-content {
    width: calc(100% - 48px);
  }
  .home-content h1 {
    font-size: 27px;
  }
}
@media (max-width: 768px) {
  .home-header {
    min-height: 64px;
    padding: 14px 18px;
  }
  .home-header > span {
    font-size: 16px;
  }
  .home-content {
    width: calc(100% - 32px);
    margin: clamp(54px, 13vh, 110px) auto 40px;
  }
  .home-content h1 {
    font-size: 25px;
    font-weight: 600;
    margin-bottom: 26px;
  }
  .research-composer {
    border-radius: 22px;
    padding: 6px 8px 8px;
  }
  .research-input :deep(.t-textarea__inner) {
    font-size: 16px;
    padding: 12px 10px 10px;
  }
  .research-input :deep(.t-textarea__inner::placeholder) {
    font-size: 14px;
  }
  .composer-toolbar {
    gap: 2px;
  }
  .composer-toolbar .layout-button {
    padding: 0 6px;
    font-size: 11px;
  }
  .layout-button > .t-icon {
    font-size: 13px;
  }
  .format-select {
    width: 115px;
  }
  .format-select :deep(.t-input) {
    font-size: 12px;
  }
  .composer-toolbar .start-button {
    width: 34px;
    min-width: 34px;
    height: 34px;
    margin-left: 2px;
  }
  .composer-meta {
    padding: 12px 6px 0;
    font-size: 11px;
    gap: 10px;
  }
  .suggestions {
    margin-top: 25px;
    padding: 0;
  }
  .suggestions > button {
    font-size: 13px;
    padding: 13px 8px;
    gap: 10px;
  }
  .suggestions > button > .t-icon {
    font-size: 17px;
  }
  .layout-settings {
    flex-wrap: wrap;
    gap: 12px;
    margin-left: 5px;
    margin-right: 5px;
    padding-top: 14px;
  }
  .layout-settings > div {
    flex-basis: 100%;
  }
  .layout-settings > a {
    margin-left: auto;
  }
  .attachment-chip {
    max-width: 100%;
  }
}
@media (max-width: 360px) {
  .composer-meta > span {
    max-width: 155px;
  }
  .layout-button :deep(.t-icon:first-child) {
    display: none;
  }
  .format-select {
    width: 108px;
  }
  .suggestions > button {
    font-size: 12px;
  }
}
@media (max-height: 650px) {
  .home-content {
    margin-top: 38px;
  }
}
.suggestions > button:disabled {
  cursor: not-allowed;
  color: #bbb;
  background: transparent;
}
</style>
