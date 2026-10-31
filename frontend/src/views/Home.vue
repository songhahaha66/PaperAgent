<template>
  <div class="home-page">
    <Sidebar
      :is-sidebar-collapsed="collapsed"
      :active-history-id="null"
      @toggle-sidebar="collapsed = !collapsed"
      @create-new-task="resetDraft"
    />
    <main class="home-main">
      <header v-if="!isMobile" class="home-header">
        <span class="paperagent-wordmark">PaperAgent</span>
      </header>
      <section
        class="home-content"
        :class="{ 'has-history': recentWorks.length }"
        :aria-label="ui.newPaper"
      >
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
            :autosize="{ minRows: 2, maxRows: 10 }"
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
            <t-tooltip :content="ui.fileHint"
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
              aria-controls="composer-layout-settings"
              :aria-label="ui.template"
              @click="layoutOpen = !layoutOpen"
              ><t-icon name="file-copy" /><span>{{ ui.template }}</span></t-button>
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
                ><template #icon><t-icon name="arrow-up" /></template></t-button
            ></t-tooltip>
          </div>
          <div v-if="layoutOpen" id="composer-layout-settings" class="layout-settings">
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
            <router-link to="/template">{{ ui.manageTemplates }}</router-link>
          </div>
        </div>
        <t-alert
          v-if="creationError"
          theme="error"
          :message="creationError"
          class="creation-error"
        />
        <router-link v-if="creationError && preparedWorkId" :to="'/work/' + preparedWorkId"
          >查看已准备的工作区与失败原因</router-link
        >
        <div v-if="modelConfigLoaded && !modelsReady" class="composer-meta">
          <router-link to="/api-config">
            <t-icon name="setting" />{{ isZh ? '配置模型连接' : 'Set up a model connection' }}
          </router-link>
        </div>
        <section v-if="recentWorks.length" class="recent-research" :aria-label="ui.recent">
          <h2>{{ ui.recent }}</h2>
          <p v-if="historyStore.loadError" class="history-message" role="status">
            {{
              isZh
                ? '最近记录更新失败，显示上次读取的结果。'
                : 'Could not refresh. Showing the last loaded records.'
            }}<t-button theme="default" variant="text" @click="historyStore.ensureLoaded(true)">{{
              isZh ? '重试' : 'Retry'
            }}</t-button>
          </p>
          <ul>
            <li v-for="work in recentWorks" :key="work.work_id">
              <div class="recent-info">
                <h3>{{ work.title?.trim() || ui.untitled }}</h3>
                <div class="recent-meta">
                  <span v-if="workStatus(work.status, work.execution_reason)">{{
                    workStatus(work.status, work.execution_reason)
                  }}</span>
                  <time v-if="updatedTime(work.updated_at)" :datetime="work.updated_at"
                    >{{ isZh ? '更新于 ' : 'Updated ' }}{{ updatedTime(work.updated_at) }}</time
                  >
                </div>
              </div>
              <t-button theme="default" variant="outline" @click="openWork(work)">{{
                work.status === 'completed'
                  ? isZh
                    ? '查看论文'
                    : 'View paper'
                  : isZh
                    ? '打开研究'
                    : 'Open research'
              }}</t-button>
            </li>
          </ul>
        </section>
        <p
          v-else-if="!historyStore.hasLoaded || historyStore.loading"
          class="history-message"
          role="status"
        >
          {{ isZh ? '正在读取最近研究…' : 'Loading recent research…' }}
        </p>
        <div v-else-if="historyStore.loadError" class="history-message" role="status">
          {{ isZh ? '暂时无法读取最近研究。' : 'Could not load recent research.' }}
          <t-button theme="default" variant="text" @click="historyStore.ensureLoaded(true)">{{
            isZh ? '重试' : 'Retry'
          }}</t-button>
        </div>
        <div v-else class="suggestions" :aria-label="isZh ? '研究示例' : 'Research examples'">
          <button :disabled="creating" @click="fillExample">
            <t-icon name="chart-line" /><span>{{
              isZh
                ? '20 天食堂销量 → 需求预测、图表与分析论文'
                : '20 days of cafeteria sales → Forecasts, charts and an analysis paper'
            }}</span
            ><t-icon name="arrow-top-right" />
          </button>
          <button :disabled="creating" @click="fillDataExample">
            <t-icon name="attach" /><span>{{
              isZh
                ? '上传成本与产能数据 → 排产方案与计算结果'
                : 'Upload costs and capacity → A production plan and calculations'
            }}</span
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
import { useWorkPanelsStore } from '@/stores/workPanels'
import { parseWorkDate, formatWorkDate } from '@/utils/dates'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useStudioCopy } from '@/lang/studio'
import { researchSetupAPI, defaultResearchSetup } from '@/api/researchSetup'
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
const preparedWorkId = ref('')
const files = ref<UploadFile[]>([]),
  templates = ref<PaperTemplate[]>([])
const panelStore = useWorkPanelsStore()
const recentWorks = computed(() =>
  [...historyStore.works]
    .sort(
      (a, b) =>
        (workTimestamp(b.updated_at) ?? -Infinity) - (workTimestamp(a.updated_at) ?? -Infinity),
    )
    .slice(0, 5),
)
function workTimestamp(value?: string) {
  if (!value) return null
  const timestamp = parseWorkDate(value).getTime()
  return Number.isNaN(timestamp) ? null : timestamp
}
function updatedTime(value?: string) {
  return workTimestamp(value) === null ? '' : formatWorkDate(value!, isZh.value ? 'zh-CN' : 'en-US')
}
function workStatus(status: string, reason?: { kind: string; message: string } | null) {
  if (['paused', 'failed', 'cancelled'].includes(status) && reason) {
    const known: Record<string, [string, string]> = {
      manual_pause: ['已手动暂停', 'Paused by you'],
      explicit_limit: ['达到明确调用上限', 'Explicit request limit reached'],
      legacy_limit: ['达到原项目调用上限', 'Existing project limit reached'],
      authentication: ['模型连接认证失败', 'Model authentication failed'],
      quota: ['服务额度不足', 'Provider quota unavailable'],
      rate_limit: ['服务限流', 'Service rate limited'],
      timeout: ['服务响应超时', 'Service timed out'],
      service_error: ['服务异常', 'Service error'],
      repeated_failure: ['相同操作重复失败', 'Repeated operation failure'],
      invalid_response: ['模型响应需检查', 'Model response needs review'],
    }
    if (known[reason.kind]) return known[reason.kind]![isZh.value ? 0 : 1]
  }
  return (
    {
      created: ['待开始', 'Not started'],
      in_progress: ['进行中', 'In progress'],
      running: ['进行中', 'In progress'],
      waiting: ['需要确认', 'Needs confirmation'],
      completed: ['已完成', 'Completed'],
      paused: ['已暂停', 'Paused'],
      failed: ['执行失败', 'Execution failed'],
      cancelled: ['已停止', 'Stopped'],
    }[status]?.[isZh.value ? 0 : 1] || ''
  )
}
function openWork(work: Work) {
  panelStore.open(work.work_id, work.status === 'completed' ? 'paper' : 'chat', undefined, 'main')
  router.push(`/work/${work.work_id}`)
}
function fillDataExample() {
  if (creating.value) return
  question.value = isZh.value
    ? '请根据我上传的产品成本、产能和订单需求数据，建立排产模型，列出约束，计算可行的生产安排，并输出方案表、计算代码和说明假设与局限性的分析论文。缺少必要数据时先向我确认。'
    : 'Use my uploaded product costs, capacity and order data to model a production plan. State constraints, compute a feasible schedule, and produce a plan table, reproducible code and an analysis paper explaining assumptions and limitations. Ask me to confirm missing inputs.'
  questionInput.value?.focus()
  attachmentInput.value?.click()
}
const loadingTemplates = ref(false),
  creating = ref(false),
  modelsReady = ref(false),
  modelConfigLoaded = ref(false),
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
    preparedWorkId.value = draft.work_id
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
    await researchSetupAPI.save(draft.work_id, defaultResearchSetup())
    historyStore.upsert(draft)
    panelStore.open(draft.work_id, 'progress', undefined, 'secondary')
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
  preparedWorkId.value = ''
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
    const readiness = await modelConfigAPI.readiness()
    modelsReady.value = readiness.ready
  } catch {
    modelsReady.value = false
  } finally {
    modelConfigLoaded.value = true
  }
})
</script>
<style scoped>
.home-page {
  display: flex;
  height: 100dvh;
  overflow: hidden;
  background: var(--pa-page);
}
.home-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  position: relative;
  background: var(--pa-page);
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
.home-content {
  width: calc(100% - 80px);
  max-width: 760px;
  margin: clamp(44px, 13vh, 140px) auto 64px;
}
.home-content.has-history {
  margin-top: clamp(24px, 5vh, 56px);
}
.recent-research {
  margin-top: 28px;
}
.recent-research h2 {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 500;
  color: var(--pa-muted);
}
.recent-research ul {
  list-style: none;
  padding: 0;
  margin: 0;
}
.recent-research li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 0;
  border-bottom: 1px solid var(--pa-line);
}
.recent-info {
  min-width: 0;
}
.recent-info h3 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 500;
  color: var(--pa-ink);
  overflow-wrap: anywhere;
}
.recent-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  font-size: 13px;
  color: var(--pa-muted);
}
.recent-research .t-button {
  flex-shrink: 0;
}
.history-message {
  margin-top: 24px;
  font-size: 15px;
  color: var(--pa-muted);
}
.home-content h1 {
  font-family: var(--td-font-family);
  font-size: 22px;
  font-weight: 500;
  line-height: 1.5;
  text-align: center;
  margin: 0 0 30px;
  color: var(--td-text-color-primary);
  letter-spacing: -0.4px;
}
.research-composer {
  border: 1px solid var(--pa-line-strong);
  border-radius: var(--pa-radius-card);
  background: var(--pa-surface);
  box-shadow: var(--pa-shadow-card);
  transition:
    border-color 0.2s,
    box-shadow 0.2s;
  overflow: hidden;
  padding: 8px 12px 12px;
}
.research-composer:focus-within {
  border-color: var(--pa-line-strong);
  box-shadow: var(--pa-focus-ring);
}
.research-input {
  width: 100%;
}
.research-input :deep(.t-textarea__inner) {
  border: 0 !important;
  box-shadow: none !important;
  background: transparent !important;
  border-radius: 0 !important;
  padding: 12px 10px 8px;
  font-size: 15px;
  line-height: 1.65;
  resize: none;
}
.research-input :deep(.t-textarea__inner::placeholder) {
  color: var(--pa-muted);
  font-size: 15px;
}
.research-input :deep(.t-textarea__inner:focus-visible) {
  outline: none;
}
.composer-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0 0;
  min-height: 40px;
}
.composer-toolbar .attach-button {
  width: var(--pa-control-height-md);
  height: var(--pa-control-height-md);
  color: var(--pa-ink);
  font-size: 17px;
  flex-shrink: 0;
}
.composer-toolbar .layout-button {
  font-size: 13px;
  font-weight: 400;
  color: var(--pa-ink);
  height: var(--pa-control-height-md);
  padding: 0 10px;
}
.layout-button :deep(.t-button__text) {
  display: flex;
  align-items: center;
  gap: 7px;
}
.layout-button .t-icon {
  font-size: 15px;
}
.layout-button .t-icon:last-child {
  font-size: 13px;
}
.composer-spacer {
  flex: 1;
}
.format-select {
  width: 120px;
  flex-shrink: 0;
}
.format-select :deep(.t-input) {
  background: transparent !important;
  border-radius: var(--pa-radius-control);
  color: var(--pa-ink);
  font-size: 13px;
  font-weight: 400;
  height: var(--pa-control-height-md);
  min-height: var(--pa-control-height-md);
}
.format-select :deep(.t-input:hover) {
  background: var(--pa-surface-muted) !important;
}
.format-select :deep(.t-input__suffix) {
  color: var(--pa-placeholder);
}
.composer-toolbar .start-button {
  width: var(--pa-control-height-md);
  height: var(--pa-control-height-md);
  min-width: var(--pa-control-height-md);
  flex-shrink: 0;
  margin: 0 0 0 2px;
  font-size: 17px;
}
.composer-toolbar .start-button.t-is-disabled {
  background: var(--pa-surface-hover);
  color: var(--pa-placeholder);
  border-color: var(--pa-line);
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
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  padding: 7px 7px 7px 12px;
  max-width: 250px;
  font-size: 13px;
  color: var(--pa-muted);
}
.attachment-chip > .t-icon {
  font-size: 17px;
  color: var(--pa-muted);
  flex-shrink: 0;
}
.attachment-chip > span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.attachment-chip .t-button {
  width: var(--pa-control-height-sm);
  height: var(--pa-control-height-sm);
  flex-shrink: 0;
  font-size: 13px;
}
.composer-meta {
  display: flex;
  justify-content: flex-end;
  padding: 12px 4px 0;
  font-size: 13px;
}
.composer-meta a {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--pa-muted);
  text-decoration: none;
}
.composer-meta a:hover {
  color: var(--td-brand-color);
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
  border-radius: var(--pa-radius-control);
  padding: 13px 12px;
  text-align: left;
  color: var(--pa-muted);
  font-size: 15px;
  cursor: pointer;
  width: 100%;
  min-width: 0;
  transition:
    background 0.15s,
    color 0.15s;
}
.suggestions > button:hover {
  background: var(--pa-surface-hover);
  color: var(--pa-ink);
}
.suggestions > button > .t-icon {
  font-size: 17px;
  color: var(--pa-muted);
  flex-shrink: 0;
}
.suggestions > button > .t-icon:last-child {
  font-size: 15px;
  color: var(--pa-placeholder);
  margin-left: auto;
  opacity: 0;
}
.suggestions > button:hover > .t-icon:last-child,
.suggestions > button:focus-visible > .t-icon:last-child {
  opacity: 1;
}
.suggestions > button > span {
  white-space: normal;
  overflow-wrap: anywhere;
}
.layout-settings {
  border-top: 1px solid var(--pa-line);
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
  font-size: 13px;
  color: var(--pa-muted);
  white-space: nowrap;
}
.layout-settings :deep(.t-select__wrap) {
  flex: 1;
  min-width: 0;
}
.layout-settings :deep(.t-input) {
  border-radius: var(--pa-radius-control);
}
.layout-settings > a {
  display: flex;
  gap: 4px;
  align-items: center;
  text-decoration: none;
  color: var(--pa-muted);
  font-size: 13px;
  white-space: nowrap;
}
.creation-error {
  margin-top: 16px;
  border-radius: var(--pa-radius-card);
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
    font-size: 22px;
  }
}
@media (max-width: 768px) {
  .home-header {
    min-height: 64px;
    padding: 14px 18px;
  }
  .home-header > span {
    font-size: 15px;
  }
  .home-content {
    width: calc(100% - 32px);
    margin: clamp(54px, 13vh, 110px) auto 40px;
  }
  .home-content h1 {
    font-size: 22px;
    font-weight: 500;
    margin-bottom: 26px;
  }
  .research-composer {
    border-radius: var(--pa-radius-card);
    padding: 8px 10px 10px;
  }
  .research-input :deep(.t-textarea__inner) {
    font-size: 15px;
    padding: 10px 8px 8px;
  }
  .research-input :deep(.t-textarea__inner::placeholder) {
    font-size: 15px;
  }
  .composer-toolbar {
    gap: 4px;
  }
  .composer-toolbar .layout-button {
    padding: 0 6px;
    font-size: 13px;
  }
  .layout-button > .t-icon {
    font-size: 13px;
  }
  .format-select {
    width: 108px;
  }
  .composer-toolbar .start-button {
    width: var(--pa-control-height-md);
    min-width: var(--pa-control-height-md);
    height: var(--pa-control-height-md);
    margin-left: 2px;
  }
  .composer-meta {
    padding: 12px 6px 0;
    font-size: 13px;
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
@media (max-width: 380px) {
  .layout-button :deep(.t-button__text > span) {
    display: none;
  }
  .format-select {
    width: 108px;
  }
  .suggestions > button {
    font-size: 13px;
  }
}
@media (max-height: 650px) {
  .home-content {
    margin-top: 38px;
  }
}
.suggestions > button:disabled {
  cursor: not-allowed;
  color: var(--pa-disabled);
  background: transparent;
}
</style>
