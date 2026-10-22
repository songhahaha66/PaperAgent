<template>
  <div class="work-page">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="currentWork?.id || null"
      @toggle-sidebar="toggleSidebar"
    />
    <main v-if="loadError" class="workspace-container">
      <section class="work-load-error" role="alert">
        <t-icon name="error-circle" />
        <h1>{{ loadErrorText }}</h1>
        <p>
          {{
            isZh
              ? '请返回工作列表，选择有权访问的研究。'
              : 'Return to your work list and choose an accessible research.'
          }}
        </p>
        <div>
          <t-button theme="primary" @click="router.push('/home')">{{
            isZh ? '返回工作列表' : 'Back to work list'
          }}</t-button>
          <t-button v-if="loadError === 'failed'" theme="default" @click="loadWork">{{
            isZh ? '重试' : 'Retry'
          }}</t-button>
        </div>
      </section>
    </main>
    <main v-else class="workspace-container">
      <header class="workspace-header">
        <div class="work-heading">
          <div class="work-title-row">
            <h1>{{ currentWork?.title?.trim() || ui.untitled }}</h1>
          </div>
          <div class="work-context" aria-live="polite">
            <span
              :class="[
                'live-dot',
                {
                  running: isStreaming,
                  attention: needsRecovery,
                  complete: currentWork?.status === 'completed',
                },
              ]"
            />
            <span class="work-context-text">{{ stageText }}</span>
            <span v-if="elapsedText" class="elapsed">{{ elapsedText }}</span>
            <span v-if="connectionState === 'reconnecting'" class="connection-state">{{
              ui.reconnect
            }}</span>
            <button v-if="connectionState === 'lost'" class="text-action" @click="reconnectTask">
              {{ ui.reconnect }}
            </button>
          </div>
        </div>
        <div class="work-actions">
          <button
            class="progress-trigger"
            :aria-label="isZh ? '查看研究进度' : 'View research progress'"
            @click="openProgress"
          >
            <t-icon name="task" />
            <span>{{ isZh ? '研究进度' : 'Research progress' }}</span>
            <span v-if="researchSnapshot?.experiments?.length" class="progress-count"
              >{{ acceptedTaskCount }}/{{ researchSnapshot.questions?.length || 0 }}
              {{ isZh ? '已有结果' : 'reviewed' }}</span
            >
          </button>
          <button
            class="progress-trigger"
            @click="openPanel('files', isMobile ? 'main' : 'secondary')"
          >
            <t-icon name="folder-open" />{{ isZh ? '文件' : 'Files' }}
          </button>
          <button class="progress-trigger" @click="handleMainPaperClick">
            {{ isZh ? '论文' : 'Paper' }}
          </button>
          <t-button
            v-if="primaryAction"
            class="work-primary-action"
            theme="primary"
            :title="primaryAction.title"
            :loading="pausing || downloadLoading"
            :disabled="loading"
            @click="handlePrimaryAction"
          >
            <template #icon><t-icon :name="primaryAction.icon" /></template>
            {{ primaryAction.label }}
          </t-button>
          <t-dropdown :options="workActionOptions" trigger="click"
            ><t-button theme="default" variant="outline" shape="square" :aria-label="ui.more"
              ><t-icon name="more" /></t-button
          ></t-dropdown>
        </div>
      </header>
      <WorkPaneLayout
        :session="panelSession"
        :panels="displayPanels"
        :options="panelOptions"
        :stacked="stackedPanes"
        @select="(id) => panelStore.activate(workId, id)"
        @close="(id) => panelStore.close(workId, id)"
        @open="openPanel"
        @move="(id, group, before) => panelStore.move(workId, id, group, before)"
        @split="(id, edge) => panelStore.split(workId, id, edge)"
        @ratio="(ratio) => (panelSession.splitRatio = ratio)"
        @focus="(group) => (panelSession.focusedGroup = group)"
      >
        <template #default="{ panel, active }">
          <section v-if="panel.kind === 'chat'" class="chat-section">
            <div class="chat-container">
              <div class="chat-messages-container" @scroll.capture="trackChatScroll">
                <div v-if="loading && !chatMessages.length" class="chat-loading">
                  <t-loading :text="ui.loading" />
                </div>
                <JsonChatRenderer
                  v-else
                  :messages="chatMessages"
                  :tasks="researchSnapshot?.experiments"
                  :snapshot="researchSnapshot"
                  :work-status="currentWork?.status"
                  @open-progress="openProgress"
                  @confirm-plan="replyToResearch"
                  :confirm-busy="isStreaming || loading"
                  @open-result="
                    (id) => openObject({ type: 'result', resultType: 'registered', id })
                  "
                  @open-file="handleWorkspaceFileSelect"
                />
                <button v-if="!atBottom && isStreaming" class="jump-latest" @click="jumpToLatest">
                  <t-icon name="arrow-down" />{{ isZh ? '查看最新进展' : 'Latest progress' }}
                </button>
              </div>
              <div class="chat-input">
                <form class="work-composer" @submit.prevent="sendMessage()">
                  <div v-if="paperContexts.length" class="paper-composer-quotes">
                    <div
                      v-for="(context, index) in paperContexts"
                      :key="index"
                      class="paper-composer-quote"
                    >
                      <span>引用 {{ index + 1 }}</span>
                      <button
                        type="button"
                        :aria-label="`移除引用 ${index + 1}`"
                        @click="paperContexts.splice(index, 1)"
                      >
                        ×
                      </button>
                      <blockquote>{{ context.quote }}</blockquote>
                    </div>
                  </div>
                  <p v-if="paperCommentError" role="alert">{{ paperCommentError }}</p>
                  <p v-if="intentConfigChecking" role="status">
                    {{ isZh ? '正在读取 Jev 配置' : 'Reading Jev configuration' }}
                  </p>
                  <p v-if="intentConfigMessage" role="alert">
                    {{ intentConfigMessage }}
                    <router-link to="/api-config">{{
                      isZh ? '打开模型设置' : 'Open model settings'
                    }}</router-link>
                  </p>
                  <t-textarea
                    v-model="inputValue"
                    :placeholder="ui.inputHint"
                    :aria-label="isZh ? '研究消息' : 'Research message'"
                    :autosize="{ minRows: 1, maxRows: 6 }"
                    :disabled="isStreaming || paperSending || loading"
                    class="work-message-input"
                    @keydown="handleComposerKeydown"
                  />
                  <div class="composer-controls">
                    <button
                      v-if="questionScope"
                      type="button"
                      class="question-scope"
                      @click="questionScope = null"
                    >
                      {{
                        researchSnapshot?.questions?.find((q) => q.id === questionScope)?.title ||
                        researchSnapshot?.experiments?.find((e) => e.task_id === questionScope)
                          ?.title ||
                        '当前引用范围'
                      }}<t-icon name="close" />
                    </button>
                    <input
                      ref="chatFileInput"
                      type="file"
                      hidden
                      @change="handleAttachmentChange"
                    />
                    <t-button
                      theme="default"
                      variant="text"
                      shape="circle"
                      :disabled="isStreaming || paperSending || loading"
                      :aria-label="isZh ? '添加附件' : 'Add attachment'"
                      @click="chatFileInput?.click()"
                      ><t-icon name="attach"
                    /></t-button>
                    <t-button
                      type="submit"
                      theme="primary"
                      shape="circle"
                      :loading="intentConfigChecking"
                      :disabled="isStreaming || paperSending || loading || !inputValue.trim()"
                      :aria-label="isZh ? '发送消息' : 'Send message'"
                      ><t-icon name="arrow-up"
                    /></t-button>
                  </div>
                </form>
                <div
                  v-if="researchSnapshot?.has_research"
                  class="model-usage"
                  aria-label="会话累计模型用量"
                >
                  <span
                    :title="`会话累计输入 ${modelUsage?.input_tokens?.toLocaleString() ?? '—'} tokens`"
                    >输入 {{ formatTokens(modelUsage?.input_tokens) }}</span
                  >
                  <span
                    :title="`会话累计输出 ${modelUsage?.output_tokens?.toLocaleString() ?? '—'} tokens`"
                    >输出 {{ formatTokens(modelUsage?.output_tokens) }}</span
                  >
                  <span
                    title="会话平均 TPS：全部已完成请求的输出 token / 累计请求耗时，包含等待与推理时间"
                    >TPS {{ modelUsage?.tps?.toFixed(1) ?? '—' }}</span
                  >
                  <span
                    title="缓存输入 token / 输入 token；仅统计网关返回缓存计数的请求，旧记录未保存或网关未提供时无法计算"
                    >缓存
                    {{
                      modelUsage?.cache_hit_rate == null
                        ? '暂无统计'
                        : (modelUsage.cache_hit_rate * 100).toFixed(1) + '%'
                    }}</span
                  >
                </div>
              </div>
            </div>
          </section>
          <section
            v-else-if="panel.kind === 'progress'"
            class="progress-panel"
            @scroll.capture="rememberPanelScroll('progress', $event)"
          >
            <ResearchStatus
              :work-id="workId"
              :snapshot="researchSnapshot"
              :work-status="currentWork?.status"
              :stage="stageText"
              :running="isStreaming"
              :action-label="primaryAction?.label"
              :action-busy="loading || pausing || downloadLoading"
              @action="handlePrimaryAction"
              @object="openObject"
              @issue="openIssueEditor"
              @resolved="refreshPaper"
              @revise="preparePlanRevision"
              @ask="askSubproblem"
              @pause="pauseTask"
            />
          </section>
          <ResearchResultViewer
            v-else-if="panel.kind === 'result'"
            :work-id="workId"
            :result-type="panel.resultType || 'registered'"
            :result-id="panel.resultId || ''"
            :snapshot="researchSnapshot"
            @issue="openIssueEditor"
            @paper="openEvidenceUse"
            @experiment="(id) => openObject({ type: 'result', resultType: 'experiment', id })"
            @open-file="handleWorkspaceFileSelect"
          />
          <section
            v-else-if="panel.kind === 'files'"
            class="files-section"
            @scroll.capture="rememberPanelScroll('files', $event)"
          >
            <FileManager
              fill-height
              :work-id="workId"
              :loading="loading || isFileRefreshing"
              :file-tree-data="workspaceFiles"
              @file-select="handleWorkspaceFileSelect"
              @refresh="handleFileRefresh"
              @main-paper-click="handleMainPaperClick"
            />
          </section>
          <section
            v-else-if="panel.kind === 'figures'"
            ref="figureScroller"
            class="figures-panel"
            @scroll="rememberPanelScroll('figures', $event)"
          >
            <div v-if="!figureFiles.length" class="studio-empty">
              <t-icon name="chart-line" size="32px" />
              <h2>{{ ui.noFigures }}</h2>
              <p>{{ ui.noFiguresHint }}</p>
            </div>
            <div v-else class="figure-grid">
              <div v-for="figure in figureFiles" :key="figure.path" class="figure-card">
                <button class="figure-open" @click="handleWorkspaceFileSelect(figure.path)">
                  <img
                    v-if="imageUrls[figure.path]"
                    :src="imageUrls[figure.path]"
                    :alt="figure.name"
                  />
                  <div v-else class="figure-placeholder"><t-icon name="image" /></div>
                  <span>{{ figure.name }}</span>
                </button>
                <button
                  @click.stop="askPaper(`请解释图表 ${figure.path} 的数据、计算依据和局限。`)"
                >
                  {{ isZh ? '追问依据' : 'Ask about evidence' }}
                </button>
              </div>
            </div>
          </section>
          <PaperEditor
            :title="currentWork?.title"
            :owner-id="authStore.currentUser?.id"
            v-else-if="panel.kind === 'paper' && researchSnapshot?.has_research"
            :snapshot="researchSnapshot"
            :active="active"
            :work-id="workId"
            :latex="currentWork?.output_mode === 'latex'"
            :refresh-key="(researchSnapshot?.commit_seq || 0) + paperRefresh"
            :disabled="isStreaming"
            @open-file="handleWorkspaceFileSelect"
            :citation-request="citationRequest"
            :evidence-request="evidenceRequest"
            :issue-request="issueRequest"
            @updated="refreshPaper"
            @quote="attachPaperQuote"
            @ask="askPaper"
            @research="revisePaper"
          />
          <WorkDocument
            v-if="
              panel.kind === 'file' || (panel.kind === 'paper' && !researchSnapshot?.has_research)
            "
            :key="`${workId}:${panel.id}`"
            :work-id="workId"
            :path="panel.path || paperPath"
            :paper="panel.kind === 'paper'"
            :available="panel.kind !== 'paper' || paperAvailable"
            :fallback-path="primaryArtifact?.path"
            :latex-source="workspaceFiles.some((file) => file.path === 'paper.tex')"
            :active="active"
            :scroll-top="panelSession.scroll[panel.id] || 0"
            :refresh-key="researchSnapshot?.commit_seq"
            @scroll-position="(top) => (panelSession.scroll[panel.id] = top)"
            @open-file="handleWorkspaceFileSelect"
            @open-files="openFiles"
          />
        </template>
      </WorkPaneLayout>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import { paperProjectAPI, type PaperComposerContext } from '@/api/paper'
import { workspaceAPI } from '@/api/workspace'
import { modelConfigAPI } from '@/api/modelConfig'
import { useAuthStore } from '@/stores/auth'
import { useWorkHistoryStore } from '@/stores/workHistory'
import Sidebar from '@/components/Sidebar.vue'
import FileManager from '@/components/FileManager.vue'
import JsonChatRenderer from '@/components/JsonChatRenderer.vue'
import WorkPaneLayout from '@/components/WorkPaneLayout.vue'
import WorkDocument from '@/components/WorkDocument.vue'
import PaperEditor from '@/components/PaperEditor.vue'
import ResearchStatus from '@/components/ResearchStatus.vue'
import ResearchResultViewer from '@/components/ResearchResultViewer.vue'
import { useWorkSession } from '@/composables/useWorkSession'
import { useWorkStream } from '@/composables/useWorkStream'
import { useArtifactPreview } from '@/composables/useArtifactPreview'
import { useWorkLayout } from '@/composables/useWorkLayout'
import { useWorkAttachments } from '@/composables/useWorkAttachments'
import { useStudioCopy } from '@/lang/studio'

const { ui, isZh } = useStudioCopy()
const route = useRoute(),
  router = useRouter()
const authStore = useAuthStore(),
  historyStore = useWorkHistoryStore()
// The keyed route gives each work its own lifetime, including in-flight requests.
const workId = ref(route.params.work_id as string)
const session = useWorkSession(workId)
const {
  currentWork,
  loading,
  loadError,
  loadErrorText,
  researchSnapshot,
  applyResearchSnapshot,
  loadResearchSnapshot,
} = session
const layout = useWorkLayout(workId, researchSnapshot)
const {
  panelStore,
  panelSession,
  isMobile,
  stackedPanes,
  isSidebarCollapsed,
  toggleSidebar,
  inputValue,
  atBottom,
  figureScroller,
  displayPanels,
  panelOptions,
  openProgress,
  openObject,
  openPanel,
  rememberPanelScroll,
  openFiles,
  handleMainPaperClick,
  handleWorkspaceFileSelect,
  trackChatScroll,
  jumpToLatest,
} = layout
const artifacts = useArtifactPreview({
  workId,
  currentWork,
  researchSnapshot,
  figuresVisible: layout.figuresVisible,
})
const {
  workspaceFiles,
  imageUrls,
  isFileRefreshing,
  downloadLoading,
  primaryArtifact,
  paperAvailable,
  paperPath,
  downloadLabel,
  figureFiles,
  downloadArtifact,
  exportWorkspace,
} = artifacts
// Metadata arrives after cached panes mount; restore scroll once their content is rendered.
watch(figureFiles, layout.restorePanelScroll, { flush: 'post' })
const issueRequest = ref<{ id: string; at: number } | null>(null)
const modelUsage = computed(() => researchSnapshot.value?.runtime?.usage)
const formatTokens = (value?: number | null) => {
  if (value == null) return '—'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}
const evidenceRequest = ref<{ section: string; section_version: string; at: number } | null>(null)
function openEvidenceUse(use: { section: string; section_version: string }) {
  evidenceRequest.value = { ...use, at: Date.now() }
  openPanel('paper', isMobile.value ? 'main' : 'secondary')
}
function openRegisteredEvidence(event: Event) {
  const detail = (event as CustomEvent).detail
  if (detail?.workId !== workId.value || !/^[RF][1-9]\d*$/.test(detail?.resultId || '')) return
  openObject({ type: 'result', resultType: 'registered', id: detail.resultId })
}
function openIssueEditor(id: string) {
  const issue = researchSnapshot.value?.feedback?.find((i) => i.id === id)
  if (issue && !issue.section) {
    openObject({ type: 'result', resultType: 'experiment', id: issue.task_id })
    return
  }
  issueRequest.value = { id, at: Date.now() }
  openPanel('paper', isMobile.value ? 'main' : 'secondary')
}
const citationRequest = ref<{ id: string; at: number } | null>(null)
function citeLiterature(id: string) {
  citationRequest.value = { id, at: Date.now() }
  openPanel('paper', isMobile.value ? 'main' : 'secondary')
}
function openLiteratureEvidence(event: Event) {
  const detail = (event as CustomEvent).detail
  if (detail?.workId !== workId.value || !/^C[1-9]\d*$/.test(detail?.evidenceId || '')) return
  citeLiterature(detail.evidenceId)
}
async function refreshPaper() {
  await loadResearchSnapshot()
  await artifacts.loadWorkspaceFiles(true)
}
function askPaper(message: string, task?: string) {
  questionScope.value = task || null
  layout.activateChat()
  sendMessage(message, 'ask')
}
function revisePaper(
  message: string,
  task?: string,
  sourceIssue?: { id: string; version: string },
) {
  questionScope.value = task || null
  layout.activateChat()
  sendMessage(message, 'revise_research', sourceIssue)
}
function preparePlanRevision(message: string, task: string) {
  questionScope.value = task
  layout.activateChat()
  return sendMessage(message, 'revise_research')
}
const stream = useWorkStream({ session, layout, refreshFiles: artifacts.loadWorkspaceFiles })
const {
  chatMessages,
  isStreaming,
  runningAction,
  questionScope,
  askSubproblem,
  pausing,
  connectionState,
  needsRecovery,
  recoveryUnavailable,
  canResume,
  recoveryHint,
  prepareNewResearch,
  stageText,
  elapsedText,
  pauseTask,
  reconnectTask,
  sendMessage: streamSendMessage,
  handleStreamMessage,
} = stream
const paperContexts = ref<PaperComposerContext[]>([])
const intentConfigMessage = ref(''),
  intentConfigChecking = ref(false)
const paperSubmitting = ref(false),
  paperCommentError = ref(''),
  paperRefresh = ref(0)
let paperRequest: { signature: string; id: string } | null = null
watch(workId, () => {
  paperContexts.value = []
  paperRequest = null
  paperCommentError.value = ''
})
const paperSending = computed(
  () => paperSubmitting.value || chatMessages.value.some((m) => m.paper_execution && m.isStreaming),
)
async function attachPaperQuote(context: PaperComposerContext) {
  if (context.work_id !== workId.value) return
  if (paperContexts.value.some((item) => JSON.stringify(item) === JSON.stringify(context))) return
  if (paperContexts.value.length >= 24) {
    paperCommentError.value = '一次最多添加 24 条引用。'
    return
  }
  paperContexts.value.push(context)
  paperCommentError.value = ''
  layout.activateChat()
  await nextTick()
  document.querySelector<HTMLTextAreaElement>('.work-message-input textarea')?.focus()
}
async function sendMessage(...args: Parameters<typeof streamSendMessage>) {
  // Explicit research actions keep their existing path. Composer references use
  // the versioned comment API and never enter research intent classification.
  if (args.length || !paperContexts.value.length) {
    if ((args[1] || 'auto') !== 'auto') {
      intentConfigMessage.value = ''
      return streamSendMessage(...args)
    }
    if (
      intentConfigChecking.value ||
      isStreaming.value ||
      !authStore.token ||
      session.isDisposed() ||
      !(args[0] || inputValue.value.trim())
    )
      return
    intentConfigChecking.value = true
    intentConfigMessage.value = ''
    let configured = false
    try {
      configured = !!(await modelConfigAPI.readiness()).roles.jev
      if (!session.isDisposed() && !configured)
        intentConfigMessage.value = isZh.value
          ? '自动聊天需要配置 Jev。明确操作按钮不依赖 Jev。'
          : 'Automatic chat requires Jev configuration. Explicit action buttons do not require Jev.'
    } catch {
      if (!session.isDisposed())
        intentConfigMessage.value = isZh.value
          ? 'Jev 配置暂时无法读取，请重试。明确操作按钮不受此检查影响。'
          : 'Jev configuration could not be read. Retry, or use an explicit action.'
    } finally {
      intentConfigChecking.value = false
    }
    if (configured && !session.isDisposed()) return streamSendMessage(...args)
    return
  }
  if (paperSending.value || isStreaming.value || loading.value || !inputValue.value.trim()) return
  const contexts = [...paperContexts.value],
    context = contexts[0]!,
    text = inputValue.value.trim(),
    work = workId.value
  const body = {
    research_id: context.research_id,
    message: text,
    references: contexts.map(({ selection, read_quote, read_version, quote }) => ({
      selection,
      read_quote,
      read_version,
      quote,
    })),
    thread_id: contexts.length === 1 ? context.thread_id : null,
  }
  const signature = JSON.stringify(body)
  if (paperRequest?.signature !== signature) paperRequest = { signature, id: crypto.randomUUID() }
  paperSubmitting.value = true
  paperCommentError.value = ''
  try {
    await paperProjectAPI.comment(work, { ...body, request_id: paperRequest.id })
    paperRequest = null
    inputValue.value = ''
    paperContexts.value = paperContexts.value.filter((item) => !contexts.includes(item))
    paperRefresh.value++
    await stream.loadChatHistory(true)
  } catch (cause) {
    paperCommentError.value =
      cause instanceof Error ? cause.message : '评论提交未确认，重试将复用同一请求。'
  } finally {
    paperSubmitting.value = false
  }
}
const { chatFileInput, handleAttachmentChange } = useWorkAttachments({
  workId,
  onUploaded: async (filename, size) => {
    await artifacts.loadWorkspaceFiles(true)
    stream.appendAttachment(filename, size)
  },
})
async function replyToResearch() {
  if (isStreaming.value || loading.value || currentWork.value?.status !== 'waiting') return
  layout.activateChat()
  await nextTick()
  document.querySelector<HTMLTextAreaElement>('.work-message-input textarea')?.focus()
}
const acceptedTaskCount = computed(
  () =>
    researchSnapshot.value?.questions?.filter((q) =>
      researchSnapshot.value?.experiments?.some(
        (e) => e.question_id === q.id && e.acceptance === 'accepted' && e.validity === 'current',
      ),
    ).length || 0,
)
const primaryAction = computed(() => {
  if (!currentWork.value) return null
  if (isStreaming.value) {
    const label =
      runningAction.value === 'ask' ? (isZh.value ? '停止回复' : 'Stop reply') : ui.value.pause
    return { kind: 'pause', icon: 'pause', label, title: label } as const
  }
  if (currentWork.value.status === 'waiting') {
    const label = isZh.value ? '补充信息' : 'Provide input'
    return { kind: 'confirm', icon: 'check', label, title: label } as const
  }
  if (canResume.value) {
    return {
      kind: 'resume',
      icon: 'play',
      label: isZh.value ? '继续' : 'Continue',
      title: recoveryHint.value,
    } as const
  }
  if (primaryArtifact.value) {
    return {
      kind: 'download',
      icon: 'download',
      label: isZh.value ? '下载' : 'Download',
      title: downloadLabel.value,
    } as const
  }
  return null
})
async function handlePrimaryAction() {
  if (loading.value || pausing.value || downloadLoading.value) return
  switch (primaryAction.value?.kind) {
    case 'pause':
      return pauseTask()
    case 'confirm':
      return replyToResearch()
    case 'resume':
      return sendMessage('继续', 'resume')
    case 'download':
      return downloadArtifact()
  }
}
async function loadWork() {
  await session.loadWork({
    loadFiles: artifacts.loadWorkspaceFiles,
    loadHistory: stream.loadChatHistory,
    resume: stream.checkAndResumeRunningTask,
    sendMessage,
  })
}
async function handleFileRefresh() {
  await Promise.all([artifacts.loadWorkspaceFiles(true), session.loadResearchSnapshot()])
}
const workActionOptions = computed(() => [
  ...(primaryArtifact.value && primaryAction.value?.kind !== 'download'
    ? [
        {
          content: downloadLabel.value,
          value: 'download',
          disabled: downloadLoading.value,
          onClick: () => downloadArtifact(),
        },
      ]
    : []),
  ...(needsRecovery.value && recoveryUnavailable.value
    ? [{ content: ui.value.restartResearch, value: 'restart', onClick: () => prepareNewResearch() }]
    : []),
  { content: ui.value.archive, value: 'export', onClick: () => exportWorkspace() },
  {
    content: ui.value.delete,
    value: 'delete',
    theme: 'error' as const,
    disabled: isStreaming.value,
    onClick: () => confirmDeleteWork(),
  },
])
function handleComposerKeydown(_value: string | number, { e }: { e: KeyboardEvent }) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    if (!loading.value) void sendMessage()
  }
}
function confirmDeleteWork() {
  if (isStreaming.value) return
  const dialog = DialogPlugin.confirm({
    header: ui.value.deleteWorkTitle,
    body: ui.value.deleteWorkHint,
    confirmBtn: { content: ui.value.confirm, theme: 'danger' },
    cancelBtn: ui.value.cancel,
    onConfirm: async () => {
      await deleteWork()
      dialog.destroy()
    },
  })
}
// 删除工作
const deleteWork = async () => {
  if (!workId.value || !authStore.token || !currentWork.value) return

  try {
    await workspaceAPI.deleteWork(authStore.token, workId.value)
    historyStore.remove(workId.value)
    panelStore.remove(workId.value)
    MessagePlugin.success('工作已删除')

    // 跳转回首页
    router.push('/home')
  } catch (error) {
    console.error('删除工作失败:', error)
    MessagePlugin.error('删除工作失败')
  }
}

onMounted(() => {
  void loadWork()
  window.addEventListener('paper-literature-open', openLiteratureEvidence)
  window.addEventListener('paper-result-open', openRegisteredEvidence)
})
onUnmounted(() => {
  window.removeEventListener('paper-literature-open', openLiteratureEvidence)
  window.removeEventListener('paper-result-open', openRegisteredEvidence)
})
</script>
<style scoped>
.work-page {
  display: flex;
  height: 100dvh;
  width: 100%;
  background: var(--pa-page);
  overflow: hidden;
}
.workspace-container {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}
.work-load-error {
  margin: auto;
  padding: 32px;
  max-width: 560px;
  text-align: center;
}
.work-load-error h1 {
  font-size: 20px;
}
.work-load-error p {
  color: var(--td-text-color-secondary);
  margin-bottom: 24px;
}
.work-load-error > div {
  display: flex;
  justify-content: center;
  gap: 12px;
}
.workspace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 28px;
  min-height: 88px;
  box-sizing: border-box;
  flex-shrink: 0;
  border-bottom: 1px solid var(--pa-line);
}
.work-heading,
.work-title-row {
  min-width: 0;
}
.work-title-row h1 {
  margin: 0;
  font-family: var(--td-font-family);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: -0.3px;
}
.work-context {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 6px;
  min-width: 0;
  font-size: 11px;
  color: var(--pa-muted);
}
.work-context-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.live-dot {
  width: 5px;
  height: 5px;
  border-radius: var(--pa-radius-circle);
  background: var(--pa-surface-active);
  flex-shrink: 0;
}
.live-dot.running,
.live-dot.complete {
  background: var(--td-brand-color);
}
.live-dot.attention {
  background: var(--pa-warning);
}
.elapsed {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.work-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.work-actions .t-button {
  height: var(--pa-control-height-md);
  font-size: 12px;
  border-radius: var(--pa-radius-control);
}
.work-actions .t-button--shape-square {
  width: 32px;
  border-color: var(--pa-line);
}
.progress-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: var(--pa-control-height-md);
  padding: 0 10px;
  border: 1px solid var(--pa-line);
  background: var(--pa-surface);
  border-radius: var(--pa-radius-control);
  color: var(--pa-muted);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.progress-trigger:hover {
  background: var(--pa-surface-hover);
  border-color: var(--pa-line-strong);
}
.progress-count {
  color: var(--pa-accent);
  padding-left: 8px;
  border-left: 1px solid var(--pa-line);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.progress-panel {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: var(--pa-surface);
}
.chat-section,
.files-section {
  display: flex;
  flex-direction: column;
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--pa-surface);
}
.chat-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.chat-messages-container {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.chat-messages-container :deep(.json-chat-renderer) {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.chat-messages-container :deep(.chat-messages) {
  height: 100%;
  overflow-y: auto;
  padding: 28px max(24px, calc((100% - 880px) / 2));
  box-sizing: border-box;
  scrollbar-width: thin;
}
.chat-messages-container :deep(.t-chat__inner) {
  padding: 0;
}
.chat-messages-container :deep(.chat-markdown) {
  min-width: 0;
  overflow-wrap: anywhere;
}
.chat-messages-container :deep(.markdown-body) {
  font-size: 15px;
  line-height: 1.85;
}
.chat-messages-container :deep(.markdown-body h1) {
  font-size: 24px;
}
.chat-messages-container :deep(.markdown-body h2) {
  font-size: 20px;
}
.chat-messages-container :deep(.markdown-body h3) {
  font-size: 17px;
}
.chat-loading {
  height: 100%;
  display: grid;
  place-items: center;
}
.chat-input {
  width: calc(100% - 48px);
  max-width: 880px;
  margin: 0 auto;
  flex-shrink: 0;
  padding: 10px 0 max(20px, env(safe-area-inset-bottom));
}
.work-composer {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--pa-line-strong);
  border-radius: var(--pa-radius-card);
  background: var(--pa-surface);
  box-shadow: var(--pa-shadow-card);
}
.model-usage {
  display: flex;
  justify-content: center;
  text-align: center;
  flex-wrap: wrap;
  gap: 6px 14px;
  padding: 8px 6px 0;
  color: var(--pa-muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.work-composer:focus-within {
  border-color: var(--pa-line-strong);
}
.work-message-input {
  flex: 1;
  min-width: 0;
}
.work-message-input :deep(.t-textarea__inner) {
  border: 0;
  background: transparent;
  box-shadow: none;
  font-size: 15px;
  line-height: 24px;
  padding: 6px 8px;
  resize: none;
  border-radius: 0;
}
.work-message-input :deep(.t-textarea__inner:focus-visible) {
  outline: none;
}
.composer-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.composer-controls .t-button {
  width: var(--pa-control-height-md);
  height: var(--pa-control-height-md);
  min-width: var(--pa-control-height-md);
}
.jump-latest {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-pill);
  background: var(--pa-surface);
  color: var(--pa-muted);
  box-shadow: var(--td-shadow-1);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.text-action {
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--td-brand-color);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.figures-panel {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 28px;
}
.figure-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 20px;
  max-width: 1080px;
  margin: 0 auto;
}
.figure-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  background: var(--pa-surface);
  cursor: pointer;
  text-align: left;
  overflow: hidden;
}
.figure-open {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.figure-card:hover {
  border-color: var(--pa-line-strong);
  background: var(--pa-surface);
}
.figure-card img,
.figure-placeholder {
  width: 100%;
  height: 200px;
  object-fit: contain;
}
.figure-placeholder {
  display: grid;
  place-items: center;
  background: var(--pa-surface-muted);
  color: var(--pa-muted);
  font-size: 30px;
}
.figure-open > span {
  font-size: 12px;
  color: var(--pa-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
@media (max-width: 768px) {
  .workspace-header {
    min-height: 74px;
    padding: 12px 16px;
    gap: 10px;
    flex-wrap: wrap;
  }
  .work-title-row h1 {
    font-size: 14px;
  }
  .work-actions {
    width: 100%;
    flex-wrap: wrap;
    gap: 6px;
  }
  .work-actions .t-button {
    padding-inline: 8px;
  }
  .work-actions .t-button--shape-square {
    padding: 0;
  }
  .chat-messages-container :deep(.chat-messages) {
    padding: 20px 16px;
  }
  .chat-messages-container :deep(.markdown-body) {
    font-size: 14px;
  }
  .chat-input {
    width: calc(100% - 24px);
    padding-top: 8px;
    padding-bottom: max(12px, env(safe-area-inset-bottom));
  }
  .work-composer {
    flex-wrap: wrap;
  }
  .work-message-input {
    flex-basis: 100%;
  }
  .composer-controls {
    width: 100%;
    justify-content: flex-end;
  }
  .work-message-input :deep(.t-textarea__inner) {
    font-size: 16px;
  }
  .figures-panel {
    padding: 20px 16px;
  }
  .figure-grid {
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 14px;
  }
}

.question-scope {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 0;
  border-radius: var(--pa-radius-inline);
  padding: 4px 7px;
  background: var(--pa-accent-soft);
  color: var(--pa-accent);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
</style>

<style scoped>
.paper-composer-quotes {
  flex-basis: 100%;
  min-width: 0;
  max-height: 160px;
  overflow-y: auto;
}
.paper-composer-quote {
  flex-basis: 100%;
  min-width: 0;
  padding: 8px 12px;
  border-left: 3px solid var(--pa-line);
  font-size: 12px;
}
.paper-composer-quote button {
  float: right;
  background: none;
  border: 0;
  cursor: pointer;
}
.paper-composer-quote blockquote {
  margin: 6px 0 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
}
</style>
