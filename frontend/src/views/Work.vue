<template>
  <div class="work-page">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="currentWork?.id || null"
      @toggle-sidebar="toggleSidebar"
    />
    <main class="workspace-container">
      <header class="workspace-header">
        <div class="work-heading">
          <div class="work-title-row">
            <h1>{{ currentWork?.title?.trim() || ui.untitled }}</h1>
          </div>
        </div>
        <div class="work-actions">
          <t-button
            v-if="isStreaming"
            theme="default"
            variant="outline"
            :loading="pausing"
            @click="pauseTask"
            ><template #icon><t-icon name="pause" /></template
            >{{ runningAction === 'ask' ? (isZh ? '停止回复' : 'Stop reply') : ui.pause }}</t-button
          >
          <t-button
            v-if="primaryArtifact"
            theme="primary"
            :loading="downloadLoading"
            @click="downloadArtifact"
            ><template #icon><t-icon name="download" /></template>{{ downloadLabel }}</t-button
          ><t-dropdown :options="workActionOptions" trigger="click"
            ><t-button theme="default" variant="outline" shape="square" :aria-label="ui.more"
              ><t-icon name="more" /></t-button
          ></t-dropdown>
        </div>
      </header>
      <section class="progress-strip" aria-label="研究进度">
        <div class="progress-summary" aria-live="polite">
          <span class="progress-label">{{ isZh ? '研究进度' : 'Progress' }}</span>
          <span
            :class="[
              'live-dot',
              {
                running: isStreaming,
                attention: needsRecovery,
                complete: currentWork?.status === 'completed',
              },
            ]"
          /><strong>{{ stageText }}</strong
          ><span v-if="elapsedText" class="elapsed">{{ ui.elapsed }} {{ elapsedText }}</span
          ><span
            v-if="isStreaming"
            :class="['connection-state', { lost: connectionState === 'lost' }]"
            >{{
              connectionState === 'lost'
                ? ui.disconnected
                : connectionState === 'reconnecting'
                  ? ui.reconnect
                  : ui.connected
            }}</span
          ><button v-if="connectionState === 'lost'" class="text-action" @click="reconnectTask">
            {{ ui.reconnect }}
          </button>
          <button
            class="phase-toggle"
            :aria-expanded="phasesExpanded"
            aria-controls="work-phases"
            @click="phasesExpanded = !phasesExpanded"
          >
            <span
              >{{ completedPhaseCount }}/{{ visiblePhases.length }}
              {{ isZh ? '阶段' : 'stages' }}</span
            >
            <t-icon :name="phasesExpanded ? 'chevron-up' : 'chevron-down'" />
          </button>
        </div>
        <div v-show="phasesExpanded" id="work-phases" class="phase-track">
          <span
            v-for="(phase, index) in visiblePhases"
            :key="phase.key"
            :class="['phase-step', `is-${phase.status}`]"
            ><span class="phase-number"
              ><t-icon v-if="phase.status === 'completed'" name="check" /><template v-else>{{
                index + 1
              }}</template></span
            >{{ phase.label }}</span
          >
        </div>
      </section>
      <div v-if="needsRecovery" class="recovery-banner" role="status">
        <t-icon name="pause-circle" />
        <div>
          <strong>{{ recoveryTitle }}</strong>
          <p>{{ recoveryHint }}</p>
          <p v-if="researchSnapshot?.validation_count">
            {{
              isZh
                ? `仍有 ${researchSnapshot.validation_count} 项校验问题，可在研究结果中查看。`
                : `${researchSnapshot.validation_count} validation issues remain. Review them in Research results.`
            }}
          </p>
        </div>
        <t-button
          v-if="canResume"
          theme="primary"
          size="small"
          @click="sendMessage('继续', 'resume')"
          >{{ ui.resume }}<template #suffix><t-icon name="arrow-right" /></template
        ></t-button>
        <t-button
          v-else-if="recoveryUnavailable"
          theme="primary"
          size="small"
          @click="prepareNewResearch"
        >
          {{ ui.restartResearch }}
        </t-button>
      </div>
      <div
        v-else-if="currentWork?.status === 'waiting' && !isStreaming"
        class="recovery-banner confirmation-banner"
      >
        <t-icon name="task-checked" />
        <div>
          <strong>{{ isZh ? '建模方案已准备好' : 'The modeling plan is ready' }}</strong>
          <p>
            {{
              isZh
                ? '在对话中查看方案，确认后开始求解；也可以直接补充修改意见。'
                : 'Review the plan in the conversation, then confirm or suggest changes.'
            }}
          </p>
        </div>
        <t-button theme="primary" size="small" @click="sendMessage('确认', 'confirm')">{{
          isZh ? '确认并执行' : 'Confirm & run'
        }}</t-button>
      </div>
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
                  @open-file="handleWorkspaceFileSelect"
                >
                  <ResearchEvidence
                    :snapshot="researchSnapshot"
                    @open-file="handleWorkspaceFileSelect"
                    @ask="askSubproblem"
                    @revise="reviseSubproblem"
                  />
                </JsonChatRenderer>
                <button v-if="!atBottom && isStreaming" class="jump-latest" @click="jumpToLatest">
                  <t-icon name="arrow-down" />{{ isZh ? '查看最新进展' : 'Latest progress' }}
                </button>
              </div>
              <div class="chat-input">
                <form class="work-composer" @submit.prevent="sendMessage()">
                  <t-textarea
                    v-model="inputValue"
                    :placeholder="ui.inputHint"
                    :aria-label="isZh ? '研究消息' : 'Research message'"
                    :autosize="{ minRows: 1, maxRows: 6 }"
                    :disabled="isStreaming || loading"
                    class="work-message-input"
                    @keydown="handleComposerKeydown"
                  />
                  <div class="composer-controls">
                    <t-dropdown :options="messageActionOptions" trigger="click">
                      <button
                        type="button"
                        class="message-action-select"
                        :disabled="isStreaming || loading"
                      >
                        {{ messageActionLabel }}<t-icon name="chevron-down" />
                      </button>
                    </t-dropdown>
                    <button
                      v-if="questionScope"
                      type="button"
                      class="question-scope"
                      @click="questionScope = null"
                    >
                      {{ questionScope }}<t-icon name="close" />
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
                      :disabled="isStreaming || loading"
                      :aria-label="isZh ? '添加附件' : 'Add attachment'"
                      @click="chatFileInput?.click()"
                      ><t-icon name="attach"
                    /></t-button>
                    <t-button
                      type="submit"
                      theme="primary"
                      shape="circle"
                      :disabled="isStreaming || loading || !inputValue.trim()"
                      :aria-label="isZh ? '发送消息' : 'Send message'"
                      ><t-icon name="arrow-up"
                    /></t-button>
                  </div>
                </form>
              </div>
            </div>
          </section>
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
              :plan-data="planData"
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
              <button
                v-for="figure in figureFiles"
                :key="figure.path"
                class="figure-card"
                @click="handleWorkspaceFileSelect(figure.path)"
              >
                <img
                  v-if="imageUrls[figure.path]"
                  :src="imageUrls[figure.path]"
                  :alt="figure.name"
                />
                <div v-else class="figure-placeholder"><t-icon name="image" /></div>
                <span>{{ figure.name }}</span>
              </button>
            </div>
          </section>
          <WorkDocument
            v-else
            :key="`${workId}:${panel.id}`"
            :work-id="workId"
            :path="panel.path || 'paper.md'"
            :paper="panel.kind === 'paper'"
            :available="panel.kind !== 'paper' || paperAvailable"
            :fallback-path="primaryArtifact?.path"
            :latex-source="workspaceFiles.some((file) => file.path === 'paper.tex')"
            :active="active"
            :scroll-top="panelSession.scroll[panel.id] || 0"
            @scroll-position="(top) => (panelSession.scroll[panel.id] = top)"
            @open-file="handleWorkspaceFileSelect"
            @open-files="openFiles"
          />
        </template>
      </WorkPaneLayout>
      <t-drawer
        v-model:visible="detailsOpen"
        :header="ui.details"
        :size="isMobile ? '90%' : '420px'"
        :footer="false"
        ><div v-if="currentWork" class="work-details">
          <t-tag variant="light">{{ workId }}</t-tag>
          <h2>{{ currentWork.title }}</h2>
          <p>{{ currentWork.description }}</p>
          <dl>
            <dt>{{ isZh ? '创建时间' : 'Created' }}</dt>
            <dd>{{ formatDate(currentWork.created_at) }}</dd>
            <dt>{{ ui.output }}</dt>
            <dd>{{ getOutputModeText(currentWork.output_mode) }}</dd>
            <dt>{{ isZh ? '状态' : 'Status' }}</dt>
            <dd>{{ getStatusText(currentWork.status) }}</dd>
          </dl>
        </div></t-drawer
      >
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'

import { useAuthStore } from '@/stores/auth'
import { useWorkHistoryStore } from '@/stores/workHistory'
import {
  workspaceAPI,
  workspaceFileAPI,
  attachmentAPI,
  type Work,
  type FileInfo,
  type PlanData,
  type PlanItemStatus,
} from '@/api/workspace'
import { chatAPI, WebSocketChatHandler, type ChatMessage } from '@/api/chat'
import Sidebar from '@/components/Sidebar.vue'
import FileManager from '@/components/FileManager.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import JsonChatRenderer from '@/components/JsonChatRenderer.vue'
import WorkPaneLayout from '@/components/WorkPaneLayout.vue'
import WorkDocument from '@/components/WorkDocument.vue'
import ResearchEvidence from '@/components/ResearchEvidence.vue'
import type { ResearchAction, ResearchSnapshot } from '@/types/research'
import {
  useWorkPanelsStore,
  type WorkPanel,
  type PanelGroup,
  type PanelKind,
} from '@/stores/workPanels'

import { useStudioCopy } from '@/lang/studio'
import { formatWorkDate } from '@/utils/dates'
import { asText, derivePhases, isPaperEvent, type PipelineBlock } from '@/utils/paperPipeline'
const { ui, isZh } = useStudioCopy()
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const historyStore = useWorkHistoryStore()
// The keyed route gives each work its own lifetime, including in-flight requests.
const workId = ref(route.params.work_id as string)
const panelStore = useWorkPanelsStore()
const panelSession = computed(() => panelStore.session(workId.value))
const isPanelVisible = (id: string) =>
  panelSession.value.activeId === id || panelSession.value.secondaryActiveId === id
let disposed = false

const { isMobile, width } = useBreakpoint()

// 侧边栏折叠状态 - 手机端默认收起
const isSidebarCollapsed = ref(isMobile.value)
const stackedPanes = computed(
  () => width.value - (isMobile.value ? 0 : isSidebarCollapsed.value ? 64 : 248) < 760,
)
watch(isMobile, (mobile) => {
  if (mobile) isSidebarCollapsed.value = true
})

// 当前工作信息
const currentWork = ref<Work | null>(null)

// 加载状态
const loading = ref(false)

// 定义聊天消息类型（使用API中的类型）
interface ChatMessageDisplay extends ChatMessage {}

// 聊天消息数据
const chatMessages = ref<ChatMessageDisplay[]>([])

const inputValue = computed({
  get: () => panelSession.value.draft,
  set: (value) => {
    panelSession.value.draft = value
  },
})
const imageUrls = ref<Record<string, string>>({})
const figureScroller = ref<HTMLElement | null>(null)

const planData = ref<PlanData | null>(null)

// 导出状态
const exportLoading = ref(false)

// 工作空间文件列表
const workspaceFiles = ref<FileInfo[]>([])

// 聊天相关状态
const isStreaming = ref(false)
const researchSnapshot = ref<ResearchSnapshot | null>(null)
const messageAction = ref<ResearchAction>('auto')
const runningAction = ref<ResearchAction>('auto')
const questionScope = ref<string | null>(null)
const messageActionLabel = computed(() =>
  messageAction.value === 'revise_research'
    ? isZh.value
      ? '增量修改研究'
      : 'Revise research'
    : messageAction.value === 'run'
      ? isZh.value
        ? '重新研究'
        : 'Run research'
      : messageAction.value === 'revise'
        ? isZh.value
          ? '修改方案'
          : 'Revise plan'
        : researchSnapshot.value?.has_research || currentWork.value?.status !== 'created'
          ? isZh.value
            ? '追问结果'
            : 'Ask about results'
          : isZh.value
            ? '开始研究'
            : 'Start research',
)
const messageActionOptions = computed(() => [
  {
    content: isZh.value ? '追问已有结果' : 'Ask about existing results',
    value: 'ask',
    onClick: () => {
      messageAction.value = 'ask'
    },
  },
  {
    content: isZh.value ? '重新研究' : 'Run research again',
    value: 'run',
    onClick: () => {
      messageAction.value = 'run'
      questionScope.value = null
    },
  },
  ...(researchSnapshot.value?.engine_version === 'research-v3' &&
  researchSnapshot.value?.tasks?.length &&
  currentWork.value?.status !== 'waiting'
    ? [
        {
          content: isZh.value ? '增量修改研究' : 'Revise research',
          value: 'revise_research',
          onClick: () => {
            messageAction.value = 'revise_research'
          },
        },
      ]
    : []),
  ...(currentWork.value?.status === 'waiting'
    ? [
        {
          content: isZh.value ? '修改建模方案' : 'Revise modeling plan',
          value: 'revise',
          onClick: () => {
            messageAction.value = 'revise'
            questionScope.value = null
          },
        },
      ]
    : []),
])
function reviseSubproblem(id: string) {
  questionScope.value = id
  messageAction.value = 'revise_research'
  panelStore.activate(workId.value, 'chat')
}
function askSubproblem(id: string) {
  questionScope.value = id
  messageAction.value = 'ask'
  panelStore.activate(workId.value, 'chat')
  nextTick(() =>
    document.querySelector<HTMLTextAreaElement>('.work-message-input textarea')?.focus(),
  )
}
let evidenceRequest = 0
async function loadResearchSnapshot() {
  const request = ++evidenceRequest
  try {
    const snapshot = await chatAPI.getResearchSnapshot(workId.value)
    if (!disposed && request === evidenceRequest && snapshot.work_id === workId.value)
      researchSnapshot.value = snapshot
  } catch {
    // Do not blank existing evidence on transient read/network failures.
  }
}
const webSocketHandler = ref<WebSocketChatHandler | null>(null)
const isFileRefreshing = ref(false) // 防止重复刷新文件列表

// Stable controls stay outside the scrolling transcript.
const phasesExpanded = ref(false)
const completedPhaseCount = computed(
  () => visiblePhases.value.filter((phase) => phase.status === 'completed').length,
)
const detailsOpen = ref(false),
  pausing = ref(false),
  downloadLoading = ref(false)
const connectionState = ref<'idle' | 'live' | 'reconnecting' | 'lost'>('idle')
const atBottom = ref(panelSession.value.followChat),
  taskStartedAt = ref<number | null>(null),
  clockNow = ref(Date.now())
let elapsedTimer: ReturnType<typeof setInterval> | undefined
const latestBlocks = computed<PipelineBlock[]>(
  () =>
    [...chatMessages.value]
      .reverse()
      .find((m) => m.role === 'assistant' && m.json_blocks?.some(isPaperEvent))?.json_blocks || [],
)
const needsRecovery = computed(
  () =>
    !isStreaming.value &&
    !!currentWork.value &&
    ['paused', 'cancelled', 'failed'].includes(currentWork.value.status),
)
const recoveryUnavailable = computed(
  () =>
    !!researchSnapshot.value &&
    (researchSnapshot.value.engine_version !== 'research-v3' ||
      !researchSnapshot.value.research_id),
)
const canResume = computed(
  () => needsRecovery.value && !!researchSnapshot.value?.research_id && !recoveryUnavailable.value,
)
const recoveryTitle = computed(() =>
  recoveryUnavailable.value
    ? ui.value.incompleteTitle
    : currentWork.value?.status === 'failed'
      ? ui.value.failedTitle
      : ui.value.pausedTitle,
)
const recoveryHint = computed(() =>
  recoveryUnavailable.value
    ? ui.value.restartHint
    : currentWork.value?.status === 'failed'
      ? ui.value.failedHint
      : ui.value.pausedHint,
)
function prepareNewResearch() {
  messageAction.value = 'run'
  questionScope.value = null
  panelStore.activate(workId.value, 'chat')
  nextTick(() =>
    document.querySelector<HTMLTextAreaElement>('.work-message-input textarea')?.focus(),
  )
}
const visiblePhases = computed(() =>
  derivePhases(latestBlocks.value, {
    done: currentWork.value?.status === 'completed',
  }),
)
const stageText = computed(() => {
  if (isStreaming.value && runningAction.value === 'ask')
    return isZh.value ? '正在分析已有结果' : 'Reviewing existing results'
  if (needsRecovery.value) return ui.value.needsAttention
  if (currentWork.value?.status === 'waiting')
    return isZh.value ? '等待确认建模方案' : 'Waiting for plan approval'
  if (!isStreaming.value)
    return currentWork.value ? getStatusText(currentWork.value.status) : ui.value.loading
  return (
    asText([...latestBlocks.value].reverse().find((b) => b.type === 'paper_stage')?.content) ||
    ui.value.working
  )
})
const elapsedText = computed(() => {
  if (!isStreaming.value || !taskStartedAt.value) return ''
  const seconds = Math.max(0, Math.floor((clockNow.value - taskStartedAt.value) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
})
const primaryArtifact = computed(() => {
  const names =
    currentWork.value?.output_mode === 'latex'
      ? ['paper.pdf', 'paper.md']
      : currentWork.value?.output_mode === 'word'
        ? ['paper.docx', 'paper.md']
        : ['paper.md']
  for (const name of names) {
    const file = workspaceFiles.value.find(
      (f) => f.type === 'file' && f.path === name && (f.size || 0) > 0,
    )
    if (file) return file
  }
  return null
})
const paperAvailable = computed(() => !!primaryArtifact.value)
const downloadLabel = computed(() =>
  primaryArtifact.value?.name.endsWith('.pdf')
    ? ui.value.downloadPdf
    : primaryArtifact.value?.name.endsWith('.docx')
      ? ui.value.downloadWord
      : ui.value.downloadMd,
)
const figureFiles = computed(() =>
  workspaceFiles.value.filter(
    (f) => f.type === 'file' && isImageFile(f.path) && f.category !== 'attachments',
  ),
)
const displayPanels = computed(() =>
  panelSession.value.panels.map((panel) => ({ ...panel, ...panelPresentation(panel) })),
)
function panelPresentation(panel: Pick<WorkPanel, 'kind' | 'path'>) {
  if (panel.kind === 'file')
    return {
      label: panel.path!.split('/').pop()!,
      icon:
        panel.path!.endsWith('.py') || panel.path!.endsWith('.tex')
          ? 'code'
          : isImageFile(panel.path!)
            ? 'image'
            : 'file-1',
    }
  return {
    chat: { label: ui.value.conversation, icon: 'chat' },
    paper: { label: isZh.value ? '论文' : 'Paper', icon: 'file-1' },
    figures: { label: ui.value.figures, icon: 'chart-line' },
    files: { label: ui.value.files, icon: 'folder-open' },
  }[panel.kind]
}
const panelOptions = computed(() =>
  (['paper', 'figures', 'files'] as const).map((kind) => ({
    content: panelPresentation({ kind }).label,
    value: kind,
  })),
)
function openPanel(kind: string, group: PanelGroup) {
  panelStore.open(workId.value, kind as PanelKind, undefined, group)
}
function rememberPanelScroll(id: string, event: Event) {
  const target = event.target as HTMLElement
  if (id === 'files' && !target.classList.contains('fm-body')) return
  if (isPanelVisible(id)) panelSession.value.scroll[id] = target.scrollTop
}
const workActionOptions = computed(() => [
  { content: ui.value.archive, value: 'export', onClick: () => exportWorkspace() },
  {
    content: ui.value.details,
    value: 'details',
    onClick: () => {
      detailsOpen.value = true
    },
  },
  {
    content: ui.value.delete,
    value: 'delete',
    theme: 'error' as const,
    disabled: isStreaming.value,
    onClick: () => confirmDeleteWork(),
  },
])
const chatFileInput = ref<HTMLInputElement | null>(null)
function handleComposerKeydown(_value: string | number, { e }: { e: KeyboardEvent }) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    if (!loading.value) void sendMessage()
  }
}
async function handleAttachmentChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) await uploadAttachment(file)
}
function openFiles() {
  panelStore.open(workId.value, 'files')
}
function handleMainPaperClick() {
  panelStore.open(workId.value, 'paper')
}
function handleWorkspaceFileSelect(path: string) {
  if (path === 'paper.md') handleMainPaperClick()
  else panelStore.open(workId.value, 'file', path)
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
async function downloadArtifact() {
  if (!primaryArtifact.value || !authStore.token) return
  downloadLoading.value = true
  try {
    const data = await workspaceFileAPI.readFile(
      authStore.token,
      workId.value,
      primaryArtifact.value.path,
    )
    let blob: Blob
    if (data.type === 'text')
      blob = new Blob([data.content || ''], { type: 'text/markdown;charset=utf-8' })
    else {
      const response = await fetch(
        `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}${data.download_url}`,
        { headers: { Authorization: `Bearer ${authStore.token}` } },
      )
      if (!response.ok) throw new Error('download')
      blob = await response.blob()
    }
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = data.filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    MessagePlugin.error(isZh.value ? '下载失败，请重试' : 'Download failed. Please retry.')
  } finally {
    downloadLoading.value = false
  }
}
async function pauseTask() {
  if (pausing.value || !authStore.token) return
  pausing.value = true
  try {
    const result = await chatAPI.pauseWork(workId.value)
    applyWorkStatus(result.work_status)
    isStreaming.value = false
    await loadChatHistory()
    await loadWorkspaceFiles()
  } catch {
    MessagePlugin.error(isZh.value ? '暂停失败，请重试' : 'Could not pause. Please retry.')
  } finally {
    pausing.value = false
  }
}
async function reconnectTask() {
  connectionState.value = 'reconnecting'
  isStreaming.value = false
  await checkAndResumeRunningTask()
  if (!isStreaming.value) {
    await loadChatHistory()
    if (authStore.token)
      currentWork.value = await workspaceAPI.getWork(authStore.token, workId.value)
    connectionState.value = 'idle'
  }
}
function trackChatScroll(event: Event) {
  const target = event.target as HTMLElement
  if (!target.classList.contains('chat-messages') || disposed || !isPanelVisible('chat')) return
  atBottom.value = target.scrollHeight - target.scrollTop - target.clientHeight < 100
  panelSession.value.followChat = atBottom.value
  panelSession.value.scroll.chat = target.scrollTop
}
function jumpToLatest() {
  atBottom.value = true
  panelSession.value.followChat = true
  scrollToBottom(true)
}
async function restorePanelScroll() {
  await nextTick()
  if (disposed) return
  if (isPanelVisible('chat')) {
    const scroller = document.querySelector<HTMLElement>('.chat-messages')
    if (scroller)
      scroller.scrollTop = panelSession.value.followChat
        ? scroller.scrollHeight
        : panelSession.value.scroll.chat || 0
  }
  if (isPanelVisible('figures') && figureScroller.value)
    figureScroller.value.scrollTop = panelSession.value.scroll.figures || 0
  if (isPanelVisible('files')) {
    const scroller = document.querySelector<HTMLElement>('.files-section .fm-body')
    if (scroller) scroller.scrollTop = panelSession.value.scroll.files || 0
  }
}
const pendingFigures = new Set<string>()
watch([() => isPanelVisible('figures'), figureFiles], async ([visible]) => {
  await restorePanelScroll()
  if (!visible) return
  await Promise.allSettled(
    figureFiles.value
      .filter((file) => !imageUrls.value[file.path] && !pendingFigures.has(file.path))
      .map(async (file) => {
        pendingFigures.add(file.path)
        try {
          const url = await getImageBlobUrl(file.path)
          if (disposed) URL.revokeObjectURL(url)
          else imageUrls.value[file.path] = url
        } finally {
          pendingFigures.delete(file.path)
        }
      }),
  )
})
watch(() => [panelSession.value.activeId, panelSession.value.secondaryActiveId], restorePanelScroll)
onMounted(() => {
  elapsedTimer = setInterval(() => {
    clockNow.value = Date.now()
  }, 1000)
})
onUnmounted(() => {
  if (elapsedTimer) clearInterval(elapsedTimer)
})

// 加载工作信息
const loadWork = async () => {
  if (!workId.value || !authStore.token) return

  loading.value = true
  try {
    const work = await workspaceAPI.getWork(authStore.token, workId.value)
    if (disposed) return
    currentWork.value = work
    historyStore.upsert(work)

    // 加载工作空间文件
    await Promise.all([loadWorkspaceFiles(), loadPlanContent(), loadResearchSnapshot()])
    if (disposed) return
    await loadChatHistory()
    if (disposed) return
    await checkAndResumeRunningTask()
    if (!disposed) await checkAndAutoSendFirstMessage()
  } catch (error) {
    console.error('加载工作信息失败:', error)
    if (!disposed) MessagePlugin.error('加载工作信息失败')
  } finally {
    loading.value = false
  }
}

// 当前恢复任务的消息ID
const reconnectMessageId = ref<string | null>(null)

// 检查并恢复正在进行的AI任务
const checkAndResumeRunningTask = async () => {
  if (!authStore.token || !workId.value) return

  try {
    // 查询任务状态
    const taskStatus = await chatAPI.getTaskStatus(authStore.token, workId.value)
    if (disposed) return

    if (taskStatus.has_task && taskStatus.status === 'running') {
      taskStartedAt.value = taskStatus.started_at ? taskStatus.started_at * 1000 : null

      // 设置流式状态
      isStreaming.value = true
      runningAction.value = taskStatus.action || 'run'

      // 创建AI回复消息框架
      const aiMessageId = `reconnect_${Date.now()}`
      reconnectMessageId.value = aiMessageId

      const aiMessage: ChatMessageDisplay = {
        id: aiMessageId,
        role: 'assistant' as const,
        content: '',
        datetime: taskStatus.started_at
          ? new Date(taskStatus.started_at * 1000).toLocaleString()
          : '',
        avatar: ASSISTANT_AVATAR,
        isStreaming: true,
        json_blocks: [],
        message_type: 'json_card' as const,
      }
      chatMessages.value.push(aiMessage)

      // 建立WebSocket连接恢复流式输出
      await setupWebSocketForReconnect()
    }
  } catch (error) {
    console.error('检查任务状态失败:', error)
  }
}

async function connectWorkSocket(messageId: string) {
  webSocketHandler.value?.disconnect()
  const handler = new WebSocketChatHandler(workId.value, authStore.token!)
  webSocketHandler.value = handler
  handler.onDisconnect(() => {
    if (!disposed) void handleConnectionLost()
  })
  handler.onReconnect((data) => {
    if (!disposed)
      connectionState.value = data.type === 'reconnect_complete' ? 'live' : 'reconnecting'
  })
  handler.onMessage((data) => {
    if (disposed) return
    if (data.type === 'start' || data.type === 'auth_success') {
      applyWorkStatus(data.work_status)
      if (data.action) runningAction.value = data.action
    } else handleStreamMessage(data, messageId)
  })
  await handler.connect()
  if (disposed) {
    handler.disconnect()
    return null
  }
  connectionState.value = 'live'
  return handler
}
const setupWebSocketForReconnect = async () => {
  try {
    if (reconnectMessageId.value) await connectWorkSocket(reconnectMessageId.value)
  } catch (error) {
    if (disposed) return
    console.error('恢复WebSocket连接失败:', error)
    isStreaming.value = false
    reconnectMessageId.value = null
    MessagePlugin.error('恢复AI任务失败')
  }
}

// 连接断开且自动重连用尽：问后端任务还在不在。任务已结束就以服务器保存的记录为准，不让界面一直停在"生成中"
const handleConnectionLost = async () => {
  if (disposed) return
  connectionState.value = 'lost'
  if (!isStreaming.value || !authStore.token || !workId.value) return
  try {
    const taskStatus = await chatAPI.getTaskStatus(authStore.token, workId.value)
    if (taskStatus.has_task && taskStatus.status === 'running') {
      MessagePlugin.warning('与服务器的连接已断开，任务仍在后台运行，刷新页面可继续查看进度')
      return
    }
  } catch (error) {
    MessagePlugin.error('与服务器的连接已断开，请检查网络后刷新页面')
    return
  }
  isStreaming.value = false
  reconnectMessageId.value = null
  await loadChatHistory()
  currentWork.value = await workspaceAPI.getWork(authStore.token, workId.value)
  loadWorkspaceFiles()
}

// 统一的流式消息处理函数
// 后端在开始、完成、失败时带上工作状态，页头和侧边栏随之更新
const applyWorkStatus = (status?: string) => {
  if (!status || !currentWork.value || currentWork.value.status === status) return
  currentWork.value.status = status
  window.dispatchEvent(
    new CustomEvent('work-status-updated', { detail: { workId: workId.value, status } }),
  )
}

const handleStreamMessage = (data: any, messageId: string) => {
  if (disposed) return
  applyWorkStatus(data.work_status)
  const messageIndex = chatMessages.value.findIndex((m) => m.id === messageId)
  if (messageIndex === -1) return

  const currentMessage = chatMessages.value[messageIndex]
  if (!currentMessage) return

  switch (data.type) {
    case 'content':
      chatMessages.value[messageIndex] = {
        ...currentMessage,
        content: currentMessage.content + data.content,
      }
      break

    case 'json_block':
      const block = data.block

      if (block?.type === 'research_state') {
        loadResearchSnapshot()
      } else if (block?.type === 'research_snapshot') {
        if (block.content?.schema_version === 2 && block.content.work_id === workId.value) {
          ++evidenceRequest
          researchSnapshot.value = block.content as ResearchSnapshot
        }
      } else if (block?.type === 'file_changed') {
        setTimeout(() => loadWorkspaceFiles(), 500)
      } else if (block?.type === 'plan_updated') {
        if (block.content && typeof block.content === 'object') {
          planData.value = block.content as PlanData
        } else {
          planData.value = markdownPlanToData(String(block.content || ''))
        }
      } else {
        chatMessages.value[messageIndex] = {
          ...currentMessage,
          json_blocks: [...(currentMessage.json_blocks || []), block],
          message_type: 'json_card' as const,
        }
      }
      break

    case 'complete':
      chatMessages.value[messageIndex] = {
        ...currentMessage,
        isStreaming: false,
      }
      isStreaming.value = false
      reconnectMessageId.value = null
      if (data.reload_history) loadChatHistory()
      connectionState.value = 'idle'
      // Refresh the authoritative evidence after research and after reconnect.
      loadResearchSnapshot()
      loadWorkspaceFiles()
      break

    case 'error':
      chatMessages.value[messageIndex] = {
        ...currentMessage,
        content: currentMessage.content || `错误: ${data.message}`,
        isStreaming: false,
      }
      isStreaming.value = false
      reconnectMessageId.value = null
      break
  }

  // 自动滚动
  scrollToBottom()
}

// 自动滚动到底部
const scrollToBottom = (force = false) => {
  if (!force && !atBottom.value) return
  nextTick(() => {
    const chatContainer = document.querySelector('.chat-messages')
    if (chatContainer && !disposed && isPanelVisible('chat')) {
      chatContainer.scrollTop = chatContainer.scrollHeight
    }
  })
}

// 加载聊天历史（使用新API）
const loadChatHistory = async () => {
  if (!authStore.token || !workId.value) return

  try {
    // 使用新的work聊天历史API
    const historyData = await chatAPI.getWorkChatHistory(authStore.token, workId.value)

    if (disposed) return
    // 转换消息格式
    chatMessages.value = historyData.messages.map((msg, index) => {
      return {
        id: msg.id || `msg_${index}`,
        role: msg.role as 'user' | 'assistant' | 'error' | 'model-change' | 'system',
        content: msg.content,
        datetime: msg.datetime || new Date(msg.timestamp).toLocaleString(),
        avatar: getAvatarByRole(msg.role),
        json_blocks: msg.json_blocks || [],
        message_type: msg.message_type || 'text',
        isStreaming: false,
      }
    })

    atBottom.value = panelSession.value.followChat
    await restorePanelScroll()
  } catch (error) {
    console.error('加载聊天历史失败:', error)
    if (!disposed) chatMessages.value = []
  }
}

// 加载工作空间文件（带防抖）
let lastFileUpdateTime = 0
const loadWorkspaceFiles = async () => {
  if (!workId.value || !authStore.token || disposed) return

  const now = Date.now()
  if (isFileRefreshing.value || now - lastFileUpdateTime < 1000) {
    return
  }

  try {
    isFileRefreshing.value = true
    lastFileUpdateTime = now

    const files = await workspaceFileAPI.listFiles(authStore.token, workId.value)
    if (!disposed) workspaceFiles.value = files
  } catch (error) {
    console.error('加载工作空间文件失败:', error)
    if (!disposed) MessagePlugin.error('加载工作空间文件失败')
  } finally {
    isFileRefreshing.value = false
  }
}

const loadPlanContent = async () => {
  if (!workId.value || !authStore.token) return
  try {
    const response = await workspaceFileAPI.readFile(authStore.token, workId.value, 'plan.json')
    if (!disposed) planData.value = response.content ? JSON.parse(response.content) : null
  } catch {
    try {
      const response = await workspaceFileAPI.readFile(authStore.token, workId.value, 'plan.md')
      if (!disposed) planData.value = markdownPlanToData(response.content || '')
    } catch {
      if (!disposed) planData.value = null
    }
  }
}

const normalizePlanStatus = (rawStatus: string): PlanItemStatus => {
  const text = rawStatus.toLowerCase()
  if (
    text.includes('❌') ||
    text.includes('阻塞') ||
    text.includes('blocked') ||
    text.includes('失败')
  )
    return 'blocked'
  if (
    text.includes('⬜') ||
    text.includes('待写') ||
    text.includes('pending') ||
    text.includes('todo')
  )
    return 'pending'
  if (text.includes('⏳') || text.includes('进行') || text.includes('progress'))
    return 'in_progress'
  if (text.includes('✅') || text.includes('完成') || text.includes('complete')) return 'completed'
  return 'pending'
}

const markdownPlanToData = (content: string): PlanData | null => {
  if (!content.trim()) return null
  const title =
    content
      .split('\n')
      .find((line) => line.trim().startsWith('#'))
      ?.replace(/^#+/, '')
      .trim() || '写作计划'
  const rows = content
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && !line.includes('---'))
    .map((line) =>
      line
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((cell) => cell.trim()),
    )
  const items = rows.slice(1).map((row, index) => {
    const order = Number.parseInt(row[0] || `${index + 1}`, 10) || index + 1
    const status = normalizePlanStatus(row[2] || '')
    return {
      id: `task-${order}`,
      order,
      title: row[1] || `任务 ${order}`,
      status,
      status_label: row[2]?.replace(/[✅⏳⬜❌]/g, '').trim() || undefined,
      description: row[3] || '',
      phase: 'write',
      depends_on: order > 1 ? [`task-${order - 1}`] : [],
      raw_status: row[2] || '',
    }
  })
  const stats = {
    total: items.length,
    completed: items.filter((item) => item.status === 'completed').length,
    in_progress: items.filter((item) => item.status === 'in_progress').length,
    blocked: items.filter((item) => item.status === 'blocked').length,
    pending: items.filter((item) => item.status === 'pending').length,
    progress_percent: items.length
      ? Math.round(
          (items.filter((item) => item.status === 'completed').length / items.length) * 100,
        )
      : 0,
  }
  const current_focus =
    items.find((item) => item.status === 'in_progress') ||
    items.find((item) => item.status === 'pending') ||
    items.find((item) => item.status === 'blocked') ||
    null
  return {
    version: 1,
    revision: 0,
    title,
    methodology: 'spec-driven',
    planning_mode: 'dynamic',
    phases: [
      { id: 'requirements', title: '需求澄清' },
      { id: 'design', title: '方案设计' },
      { id: 'tasks', title: '任务拆解' },
      { id: 'implement', title: '执行生成' },
      { id: 'verify', title: '验收检查' },
    ],
    items,
    stats,
    current_focus,
    next_actions: items
      .filter((item) => item.status === 'pending' || item.status === 'blocked')
      .slice(0, 3)
      .map((item) => ({ id: item.id, title: item.title })),
    source: 'frontend_markdown_fallback',
    source_markdown: content,
  }
}

// 处理文件刷新
const handleFileRefresh = async () => {
  console.log('手动刷新文件列表')
  await loadWorkspaceFiles()
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

// 导出工作空间
const exportWorkspace = async () => {
  if (!workId.value || !authStore.token || !currentWork.value) return

  try {
    exportLoading.value = true

    // 调用导出API
    const blob = await workspaceFileAPI.exportWorkspace(authStore.token, workId.value)

    // 创建下载链接
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `workspace_${workId.value}.zip`

    // 触发下载
    document.body.appendChild(link)
    link.click()

    // 清理
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)

    MessagePlugin.success('工作空间导出成功')
  } catch (error) {
    console.error('导出工作空间失败:', error)
    MessagePlugin.error('导出工作空间失败')
  } finally {
    exportLoading.value = false
  }
}

// Upload a selected attachment, then refresh the shared file list.
const uploadAttachment = async (file: File) => {
  if (!authStore.token || !workId.value) {
    MessagePlugin.error('请先登录并选择工作空间')
    return
  }

  try {
    MessagePlugin.info(`正在上传文件: ${file.name}`)

    // 使用attachmentAPI上传，保持与Home.vue一致
    const result = await attachmentAPI.uploadAttachment(authStore.token, workId.value, file)

    MessagePlugin.success(`文件上传成功: ${result.attachment.original_filename}`)

    // 刷新文件列表
    await loadWorkspaceFiles()

    // 添加上传成功的消息到聊天
    const uploadMessage: ChatMessageDisplay = {
      id: Date.now().toString(),
      role: 'user' as const,
      content: `已上传文件: ${result.attachment.original_filename} (${(result.attachment.file_size / 1024).toFixed(2)} KB)`,
      datetime: new Date().toLocaleString(),
      avatar: USER_AVATAR,
    }

    chatMessages.value.push(uploadMessage)

    // 自动滚动到底部
    scrollToBottom()
  } catch (error) {
    console.error('文件上传失败:', error)
    MessagePlugin.error(`文件上传失败: ${error instanceof Error ? error.message : '未知错误'}`)
  }
}

// 判断是否为图片文件
const isImageFile = (filePath: string): boolean => {
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']
  const lowerPath = filePath.toLowerCase()
  return imageExtensions.some((ext) => lowerPath.endsWith(ext))
}

// 获取图片的blob URL（带认证）
const getImageBlobUrl = async (filePath: string): Promise<string> => {
  try {
    const token = authStore.token
    if (!token) {
      throw new Error('未登录')
    }

    const url = workspaceFileAPI.getImageUrl(token, workId.value, filePath)

    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`)
    }

    const blob = await response.blob()
    return URL.createObjectURL(blob)
  } catch (error) {
    console.error('获取图片失败:', error)
    throw error
  }
}

// 根据角色获取头像（用站内图片，不依赖外部头像服务）
const USER_AVATAR = '/avatar-user.svg'
const ASSISTANT_AVATAR = '/logo.png'
const getAvatarByRole = (role: string) => {
  const avatars = {
    user: USER_AVATAR,
    assistant: ASSISTANT_AVATAR,
    system: ASSISTANT_AVATAR,
  }
  return avatars[role as keyof typeof avatars] || avatars.assistant
}

// 发送消息
const sendMessage = async (
  messageContent?: string,
  action: ResearchAction = messageAction.value,
) => {
  const content = messageContent || inputValue.value.trim()
  if (!content || isStreaming.value || disposed || !authStore.token) return

  // 添加用户消息
  const userMessage: ChatMessageDisplay = {
    id: Date.now().toString(),
    role: 'user' as const,
    content: content,
    datetime: new Date().toLocaleString(),
    avatar: USER_AVATAR,
  }

  atBottom.value = true
  chatMessages.value.push(userMessage)
  inputValue.value = ''

  panelStore.activate(workId.value, 'chat')
  const resolvedAction = action === 'auto' && questionScope.value ? 'ask' : action
  const scope = ['ask', 'revise_research'].includes(resolvedAction)
    ? questionScope.value || undefined
    : undefined
  if (resolvedAction !== 'ask') questionScope.value = null
  messageAction.value = 'auto'
  await sendRealMessage(content, resolvedAction, scope)
}

// 发送真实消息（WebSocket）
const sendRealMessage = async (message: string, action: ResearchAction, subproblemId?: string) => {
  if (!authStore.token || disposed) return

  isStreaming.value = true
  runningAction.value = action
  connectionState.value = 'reconnecting'
  taskStartedAt.value = Date.now()
  clockNow.value = Date.now()

  // 创建AI回复消息
  const aiMessageId = (Date.now() + 1).toString()
  const aiMessage: ChatMessageDisplay = {
    id: aiMessageId,
    role: 'assistant' as const,
    content: '',
    datetime: new Date().toLocaleString(),
    avatar: ASSISTANT_AVATAR,
    isStreaming: true,
  }

  chatMessages.value.push(aiMessage)

  try {
    // 使用WebSocket发送消息
    await sendMessageViaWebSocket(message, aiMessageId, action, subproblemId)
  } catch (error) {
    console.error('发送消息失败:', error)
    const messageIndex = chatMessages.value.findIndex((m) => m.id === aiMessageId)
    if (messageIndex > -1) {
      const msg = chatMessages.value[messageIndex]
      if (msg) {
        msg.content = '发送消息失败，请稍后重试'
        msg.isStreaming = false
      }
    }
    isStreaming.value = false
    if (!disposed) MessagePlugin.error('发送消息失败')
  }
}

const sendMessageViaWebSocket = async (
  message: string,
  messageId: string,
  action: ResearchAction,
  subproblemId?: string,
) => {
  const handler = await connectWorkSocket(messageId)
  const snapshot = researchSnapshot.value
  const revision =
    action === 'revise_research' && snapshot?.research_id && snapshot.research_revision
      ? {
          command_id: crypto.randomUUID(),
          research_id: snapshot.research_id,
          base_revision: snapshot.research_revision,
          scope: subproblemId ? [subproblemId] : undefined,
        }
      : undefined
  handler?.sendMessage(message, undefined, action, subproblemId, revision)
}

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

// 格式化日期
const formatDate = (dateString: string) => {
  return formatWorkDate(dateString)
}

// 获取状态文本
const getStatusText = (status: string) => {
  const texts: Record<string, string> = {
    created: '已创建',
    in_progress: '进行中',
    running: '进行中',
    completed: '已完成',
    paused: '已暂停',
    waiting: '待确认',
    cancelled: '已取消',
    failed: '失败',
  }
  return texts[status] || status
}

// 获取输出模式文本
const getOutputModeText = (mode?: string) => {
  const texts: Record<string, string> = {
    markdown: 'Markdown',
    word: 'Word (.docx)',
    latex: 'LaTeX',
  }
  return texts[mode || 'markdown'] || 'Markdown'
}

// 组件卸载时清理资源
onUnmounted(() => {
  disposed = true
  if (webSocketHandler.value) {
    webSocketHandler.value.disconnect()
    webSocketHandler.value = null
  }

  // 重置流式状态，确保输入框不被禁用
  isStreaming.value = false

  // 清理blob URLs以释放内存
  Object.values(imageUrls.value).forEach((url) => {
    if (url.startsWith('blob:')) {
      URL.revokeObjectURL(url)
    }
  })
  imageUrls.value = {}
})

// 监听聊天消息变化，自动滚动到底部
watch(
  chatMessages,
  (newMessages) => {
    if (newMessages.length > 0) {
      scrollToBottom()
    }
  },
  { deep: true },
)

// 组件挂载时加载工作信息
onMounted(() => {
  if (workId.value) {
    loadWork()
  }

  // 组件挂载完成后，如果有聊天消息，自动滚动到底部
  nextTick(() => {
    if (chatMessages.value.length > 0) {
      scrollToBottom()
    }
  })
})

// 检查并自动发送第一句话
const checkAndAutoSendFirstMessage = async () => {
  const pendingKey = `pendingQuestion:${workId.value}`
  const pendingQuestion =
    localStorage.getItem(pendingKey) || localStorage.getItem('pendingQuestion')
  if (pendingQuestion && currentWork.value?.title?.trim() === '') {
    localStorage.removeItem(pendingKey)
    localStorage.removeItem('pendingQuestion')
    try {
      generateWorkTitle(pendingQuestion).catch((err: Error) =>
        console.error('后台生成标题失败:', err),
      )
      await sendMessage(pendingQuestion, 'run')
    } catch (error) {
      console.error('自动发送第一句话失败:', error)
    }
  }
}

const generateWorkTitle = async (question: string) => {
  try {
    // 调用标题生成API，会自动更新数据库
    const response = await chatAPI.generateTitle(authStore.token!, workId.value!, question)

    if (disposed) return
    // 更新本地状态
    if (currentWork.value) {
      currentWork.value.title = response.title
    }

    // 通知侧边栏刷新工作列表
    // 通过触发一个自定义事件来通知父组件或侧边栏
    window.dispatchEvent(
      new CustomEvent('work-title-updated', {
        detail: {
          workId: workId.value,
          newTitle: response.title,
        },
      }),
    )

    console.log('标题已更新:', response.title)
  } catch (error) {
    console.error('生成标题失败:', error)
  }
}
</script>
<style scoped>
.work-page {
  display: flex;
  height: 100dvh;
  width: 100%;
  background: #fff;
  overflow: hidden;
}
.workspace-container {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}
.workspace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 12px 24px;
  min-height: 60px;
  box-sizing: border-box;
  flex-shrink: 0;
}
.work-heading,
.work-title-row {
  min-width: 0;
}
.work-title-row h1 {
  margin: 0;
  font-family: var(--td-font-family);
  font-size: 16px;
  font-weight: 500;
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.work-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.work-actions .t-button {
  height: 32px;
  font-size: 12px;
}
.work-actions .t-button--shape-square {
  width: 32px;
}
.progress-strip {
  padding: 0 24px 10px;
  flex-shrink: 0;
  border-bottom: 1px solid #eceeeb;
}
.progress-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
}
.progress-label {
  color: var(--td-text-color-secondary);
  font-size: 12px;
  margin-right: 6px;
  flex-shrink: 0;
}
.progress-summary strong {
  font-size: 12px;
  font-weight: 400;
  color: #626c63;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #aeb5ae;
  flex-shrink: 0;
}
.live-dot.running,
.live-dot.complete {
  background: var(--td-brand-color);
}
.live-dot.attention {
  background: var(--td-warning-color);
}
.elapsed,
.connection-state {
  color: #7d857e;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.connection-state {
  margin-left: auto;
}
.connection-state.lost {
  color: var(--td-error-color);
}
.phase-toggle {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #747c75;
  cursor: pointer;
  font-size: 12px;
  white-space: nowrap;
}
.phase-toggle:hover {
  background: #f3f5f2;
  color: #303830;
}
.phase-track {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(115px, 1fr));
  gap: 14px 12px;
  border-top: 1px solid #eceeeb;
  padding-top: 16px;
  margin: 10px 0 6px;
}
.phase-step {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
  color: #8d958e;
  font-size: 12px;
}
.phase-number {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border: 1px solid #d9dfd9;
  border-radius: 50%;
  font-size: 11px;
  flex-shrink: 0;
}
.phase-number > .t-icon {
  font-size: 12px;
}
.phase-step.is-completed {
  color: var(--td-brand-color);
}
.phase-step.is-completed .phase-number {
  border-color: var(--td-brand-color);
}
.phase-step.is-in_progress {
  color: var(--td-brand-color);
  font-weight: 500;
}
.phase-step.is-in_progress .phase-number {
  background: var(--td-brand-color);
  border-color: var(--td-brand-color);
  color: white;
}
.phase-step.is-blocked {
  color: var(--td-warning-color);
}
.recovery-banner {
  margin: 16px 24px 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid #e6ddc8;
  background: #fcf9f1;
  border-radius: 10px;
  flex-shrink: 0;
}
.recovery-banner > .t-icon {
  font-size: 22px;
  color: var(--td-warning-color);
  flex-shrink: 0;
}
.recovery-banner > div {
  flex: 1;
  min-width: 0;
}
.recovery-banner strong {
  font-size: 14px;
  font-weight: 500;
}
.recovery-banner p {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--td-text-color-secondary);
}
.recovery-banner > .t-button {
  flex-shrink: 0;
}
.confirmation-banner {
  border-color: #dae5db;
  background: #f6f9f5;
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
  background: #fff;
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
  align-items: flex-end;
  gap: 6px;
  padding: 8px;
  border: 1px solid #d9dfd9;
  border-radius: 26px;
  background: #fff;
  box-shadow: 0 2px 8px #00000004;
}
.work-composer:focus-within {
  border-color: #a1b4a6;
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
  width: 36px;
  height: 36px;
  min-width: 36px;
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
  border: 1px solid #dce2dc;
  border-radius: 18px;
  background: #fff;
  color: #5f6c62;
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
  border: 1px solid #e3e8e2;
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  overflow: hidden;
}
.figure-card:hover {
  border-color: #afc1b2;
  background: #fcfdfb;
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
  background: #f7f9f6;
  color: #8b968c;
  font-size: 30px;
}
.figure-card > span {
  font-size: 12px;
  color: #687469;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.work-details h2 {
  font-size: 20px;
  font-weight: 600;
  margin: 20px 0;
}
.work-details p {
  white-space: pre-wrap;
  font-size: 14px;
  line-height: 1.9;
  color: var(--td-text-color-secondary);
}
.work-details dl {
  margin-top: 24px;
  border-top: 1px solid #e4e8e2;
  padding-top: 16px;
}
.work-details dt {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  margin: 16px 0 6px;
}
.work-details dd {
  margin: 0;
  font-size: 14px;
}
@media (max-width: 768px) {
  .workspace-header {
    min-height: 56px;
    padding: 10px 16px;
    gap: 12px;
  }
  .work-title-row h1 {
    font-size: 14px;
  }
  .work-actions {
    gap: 6px;
  }
  .work-actions .t-button {
    padding-inline: 8px;
  }
  .work-actions .t-button--shape-square {
    padding: 0;
  }
  .progress-strip {
    padding: 0 16px 8px;
  }
  .progress-label,
  .elapsed {
    display: none;
  }
  .phase-track {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px 8px;
  }
  .phase-step {
    font-size: 11px;
    gap: 6px;
  }
  .recovery-banner {
    margin: 12px 16px 0;
    padding: 12px;
    flex-wrap: wrap;
  }
  .recovery-banner > .t-icon {
    align-self: start;
    margin-top: 2px;
  }
  .recovery-banner > div {
    min-width: calc(100% - 36px);
  }
  .recovery-banner > .t-button {
    margin-left: 34px;
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
@media (max-width: 380px) {
  .phase-track {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.message-action-select {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  color: #535c55;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  padding: 5px 3px;
  white-space: nowrap;
}
.question-scope {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 0;
  border-radius: 6px;
  padding: 4px 7px;
  background: #eef4ef;
  color: #286345;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
</style>
