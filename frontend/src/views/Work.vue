<template>
  <div class="work-page">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
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
                attention: canResume,
                complete: currentWork?.status === 'completed' && !canResume,
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
      <div v-if="canResume" class="recovery-banner" role="status">
        <t-icon name="pause-circle" />
        <div>
          <strong>{{ currentWork?.status === 'failed' ? ui.failedTitle : ui.pausedTitle }}</strong>
          <p>{{ currentWork?.status === 'failed' ? ui.failedHint : ui.pausedHint }}</p>
        </div>
        <t-button theme="primary" size="small" @click="sendMessage('继续')"
          >{{ ui.resume }}<template #suffix><t-icon name="arrow-right" /></template
        ></t-button>
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
        <t-button theme="primary" size="small" @click="sendMessage('确认')">{{
          isZh ? '确认并执行' : 'Confirm & run'
        }}</t-button>
      </div>
      <div class="workspace-content">
        <section class="chat-section" v-show="!isMobile || activeMobilePanel === 'chat'">
          <div class="panel-header">
            <span><t-icon name="chat" />{{ ui.conversation }}</span
            ><span class="panel-hint">{{ isStreaming ? ui.working : ui.saved }}</span>
          </div>
          <div class="chat-container">
            <div class="chat-messages-container" @scroll.capture="trackChatScroll">
              <JsonChatRenderer :messages="chatMessages" /><button
                v-if="!atBottom && isStreaming"
                class="jump-latest"
                @click="jumpToLatest"
              >
                <t-icon name="arrow-down" />{{ isZh ? '查看最新进展' : 'Latest progress' }}
              </button>
            </div>
            <div class="chat-input">
              <ChatSender
                v-model="inputValue"
                :textarea-props="{
                  placeholder: ui.inputHint,
                  autosize: { minRows: 2, maxRows: 6 },
                }"
                @send="sendMessage"
                @file-select="handleFileSelect"
                :disabled="isStreaming"
              >
                <template #suffix>
                  <input ref="chatFileInput" type="file" hidden @change="handleAttachmentChange" />
                  <t-button
                    theme="default"
                    variant="text"
                    shape="circle"
                    :disabled="isStreaming"
                    :aria-label="isZh ? '添加附件' : 'Add attachment'"
                    @click="chatFileInput?.click()"
                    ><t-icon name="attach"
                  /></t-button>
                  <t-button
                    theme="primary"
                    shape="circle"
                    :disabled="isStreaming || !inputValue.trim()"
                    :aria-label="isZh ? '发送消息' : 'Send message'"
                    @click="sendMessage()"
                    ><t-icon name="arrow-up"
                  /></t-button>
                </template>
              </ChatSender>
              <div class="input-note">
                <span>{{
                  isZh
                    ? 'AI 生成内容，请核验关键数据与结论。'
                    : 'Verify key data and conclusions generated by AI.'
                }}</span
                ><t-button
                  v-if="isStreaming"
                  size="small"
                  theme="default"
                  variant="text"
                  :loading="pausing"
                  @click="pauseTask"
                  ><template #icon><t-icon name="pause" /></template>{{ ui.pause }}</t-button
                >
              </div>
            </div>
          </div>
        </section>
        <section v-if="isMobile" class="files-section" v-show="activeMobilePanel === 'files'">
          <FileManager
            fill-height
            :work-id="workId"
            :loading="loading"
            :file-tree-data="workspaceFiles"
            :plan-data="planData"
            @file-select="handleWorkspaceFileSelect"
            @refresh="handleFileRefresh"
            @main-paper-click="handleMainPaperClick"
          />
        </section>
        <section class="preview-section" v-show="!isMobile || activeMobilePanel === 'preview'">
          <t-tabs v-model="resourceTab" class="resource-tabs" :aria-label="ui.preview">
            <t-tab-panel v-for="tab in resourceTabs" :key="tab.value" :value="tab.value">
              <template #label
                ><span class="resource-tab-label"
                  ><t-icon :name="tab.icon" />{{ tab.label
                  }}<span v-if="tab.value === 'figures' && figureFiles.length">{{
                    figureFiles.length
                  }}</span></span
                ></template
              >
            </t-tab-panel>
          </t-tabs>
          <div v-if="resourceTab === 'files'" class="resource-files">
            <FileManager
              fill-height
              :work-id="workId"
              :loading="loading"
              :file-tree-data="workspaceFiles"
              :plan-data="planData"
              @file-select="handleWorkspaceFileSelect"
              @refresh="handleFileRefresh"
              @main-paper-click="handleMainPaperClick"
            />
          </div>
          <div v-else-if="resourceTab === 'figures'" class="figures-panel">
            <div v-if="!figureFiles.length" class="studio-empty">
              <t-icon name="chart-line" size="40px" />
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
          </div>
          <div v-else class="preview-body">
            <div v-if="mainPaperLoading" class="preview-loading" role="status">
              <t-loading :text="isZh ? '正在打开论文' : 'Opening paper'" />
            </div>
            <div v-else-if="showMainPaper && mainPaperContent" class="paper-preview">
              <div class="document-caption">
                <span>{{ isZh ? '正文预览 · paper.md' : 'Text preview · paper.md' }}</span>
                <div class="document-actions">
                  <t-button
                    v-if="
                      currentWork?.output_mode === 'latex' &&
                      workspaceFiles.some((f) => f.path === 'paper.tex')
                    "
                    size="small"
                    theme="default"
                    variant="text"
                    @click="handleWorkspaceFileSelect('paper.tex')"
                    >{{ isZh ? 'LaTeX 源码' : 'LaTeX source' }}</t-button
                  >
                  <button class="text-action" @click="handleMainPaperClick">
                    <t-icon name="refresh" />{{ isZh ? '刷新正文' : 'Refresh' }}
                  </button>
                </div>
              </div>
              <MarkdownRenderer :content="mainPaperContent" :work-id="workId" base-path="" />
            </div>
            <div v-else-if="selectedFile" class="selected-preview">
              <div class="document-caption">
                <span :title="selectedFile">{{ selectedFile }}</span
                ><button
                  class="text-action"
                  :aria-label="isZh ? '关闭文件预览' : 'Close file preview'"
                  @click="closeFilePreview"
                >
                  <t-icon name="close" />
                </button>
              </div>
              <div v-if="previewError" class="studio-empty">
                <t-icon name="file-1" size="32px" />
                <h2>{{ isZh ? '文件暂时无法打开' : 'Could not open file' }}</h2>
                <t-button theme="default" @click="handleWorkspaceFileSelect(selectedFile!)">{{
                  isZh ? '重试' : 'Retry'
                }}</t-button>
              </div>
              <div v-else-if="!currentFileData" class="studio-empty">
                <t-loading :text="ui.loading" />
              </div>
              <div
                v-else-if="currentFileData.type === 'text' && !currentFileContent.trim()"
                class="studio-empty"
              >
                <t-icon name="file-1" size="40px" />
                <h2>{{ ui.emptyFile }}</h2>
                <p>{{ ui.emptyFileHint }}</p>
              </div>
              <div v-else-if="currentFileData.type === 'text'" class="text-preview">
                <CodeHighlight
                  v-if="selectedFile.endsWith('.py') || selectedFile.endsWith('.tex')"
                  :code="currentFileContent"
                  :language="selectedFile.endsWith('.tex') ? 'latex' : 'python'"
                /><MarkdownRenderer
                  v-else-if="selectedFile.endsWith('.md')"
                  :content="currentFileContent"
                  :work-id="workId"
                  :base-path="selectedFile.substring(0, selectedFile.lastIndexOf('/'))"
                />
                <pre v-else>{{ currentFileContent }}</pre>
              </div>
              <div v-else-if="currentFileData.type === 'image'" class="image-preview">
                <img
                  v-if="imageUrls[selectedFile]"
                  :src="imageUrls[selectedFile]"
                  :alt="selectedFile"
                /><t-loading v-else />
              </div>
              <DocxViewer
                v-else-if="isDocxFile(selectedFile)"
                :file-info="currentFileData"
                :work-id="workId"
                :token="authStore.token || ''"
              /><BinaryFileViewer
                v-else
                :file-info="currentFileData"
                :work-id="workId"
                :token="authStore.token || ''"
              />
            </div>
            <div v-else class="preview-placeholder">
              <div class="studio-empty">
                <t-icon name="file-1" size="48px" />
                <h2>{{ isZh ? '论文预览' : 'Paper preview' }}</h2>
                <p>
                  {{
                    isStreaming || canResume
                      ? ui.previewEmptyHint
                      : isZh
                        ? '从文件中选择正文、图表或其他文件进行预览。'
                        : 'Select a paper, figure, or another file to preview.'
                  }}
                </p>
                <div class="preview-empty-actions">
                  <t-button
                    v-if="paperAvailable"
                    theme="primary"
                    variant="base"
                    @click="handleMainPaperClick"
                    >{{ ui.viewResults }}</t-button
                  ><t-button theme="default" variant="text" @click="openFiles"
                    >{{ ui.files }}<template #suffix><t-icon name="chevron-right" /></template
                  ></t-button>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
      <nav v-if="isMobile" class="mobile-tab-bar" :aria-label="ui.workspace">
        <button
          v-for="tab in mobileTabs"
          :key="tab.value"
          :aria-current="activeMobilePanel === tab.value ? 'page' : undefined"
          :class="{ active: activeMobilePanel === tab.value }"
          @click="activeMobilePanel = tab.value"
        >
          <t-icon :name="tab.icon" /><span>{{ tab.label }}</span>
        </button>
      </nav>
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
import { ChatItem, ChatSender } from '@tdesign-vue-next/chat'

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
import {
  chatAPI,
  WebSocketChatHandler,
  type ChatMessage,
  type ChatSessionResponse,
} from '@/api/chat'
import Sidebar from '@/components/Sidebar.vue'
import FileManager from '@/components/FileManager.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import JsonChatRenderer from '@/components/JsonChatRenderer.vue'
import CodeHighlight from '@/components/CodeHighlight.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import BinaryFileViewer from '@/components/BinaryFileViewer.vue'
import DocxViewer from '@/components/DocxViewer.vue'

import { useStudioCopy } from '@/lang/studio'
import { formatWorkDate } from '@/utils/dates'
import { asText, derivePhases, deriveSubproblems, type PipelineBlock } from '@/utils/paperPipeline'
const { ui, isZh } = useStudioCopy()
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const historyStore = useWorkHistoryStore()
const workId = computed(() => route.params.work_id as string)

const { isMobile } = useBreakpoint()

// 侧边栏折叠状态 - 手机端默认收起
const descriptionExpanded = ref(false)
const isSidebarCollapsed = ref(isMobile.value)

type MobilePanel = 'chat' | 'files' | 'preview'
const activeMobilePanel = ref<MobilePanel>('chat')

watch(isMobile, (mobile) => {
  if (mobile) {
    isSidebarCollapsed.value = true
    if (resourceTab.value === 'files') {
      resourceTab.value = 'paper'
      activeMobilePanel.value = 'files'
    }
  }
})

// 当前工作信息
const currentWork = ref<Work | null>(null)

// 加载状态
const loading = ref(false)

// 定义聊天消息类型（使用API中的类型）
interface ChatMessageDisplay extends ChatMessage {}

// 聊天消息数据
const chatMessages = ref<ChatMessageDisplay[]>([])

// 输入框内容
const inputValue = ref('')

// 分割线悬停状态
const hoveredDivider = ref<number | null>(null)

// 文件管理器状态
const selectedFile = ref<string | null>(null)

// 文件内容映射
const currentFileContent = ref('')
const currentFileName = ref('')
const currentFileData = ref<any>(null) // 存储完整的文件响应数据

// 图片URL缓存
const imageUrls = ref<Record<string, string>>({})

// 主要论文内容
const mainPaperContent = ref<string>('')
const showMainPaper = ref(false)
const mainPaperLoading = ref(false)
const previewError = ref(false)
let previewRequestId = 0

const planData = ref<PlanData | null>(null)

// 导出状态
const exportLoading = ref(false)

// 工作空间文件列表
const workspaceFiles = ref<FileInfo[]>([])

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null)

// 聊天相关状态
const currentChatSession = ref<ChatSessionResponse | null>(null)
const isStreaming = ref(false)
const webSocketHandler = ref<WebSocketChatHandler | null>(null)
const isFileRefreshing = ref(false) // 防止重复刷新文件列表

// Stable controls stay outside the scrolling transcript.
const resourceTab = ref<'paper' | 'figures' | 'files'>('paper')
const phasesExpanded = ref(false)
const completedPhaseCount = computed(
  () => visiblePhases.value.filter((phase) => phase.status === 'completed').length,
)
const detailsOpen = ref(false),
  pausing = ref(false),
  downloadLoading = ref(false)
const connectionState = ref<'idle' | 'live' | 'reconnecting' | 'lost'>('idle')
const atBottom = ref(true),
  taskStartedAt = ref<number | null>(null),
  clockNow = ref(Date.now())
let elapsedTimer: ReturnType<typeof setInterval> | undefined
const latestBlocks = computed<PipelineBlock[]>(
  () => [...chatMessages.value].reverse().find((m) => m.role === 'assistant')?.json_blocks || [],
)
const latestSubproblems = computed(() => deriveSubproblems(latestBlocks.value))
const canResume = computed(
  () =>
    !isStreaming.value &&
    ((!!currentWork.value &&
      ['paused', 'cancelled', 'failed'].includes(currentWork.value.status)) ||
      latestSubproblems.value.some((s) => s.status === 'failed')),
)
const visiblePhases = computed(() =>
  derivePhases(latestBlocks.value, {
    done: currentWork.value?.status === 'completed' && !canResume.value,
  }),
)
const stageText = computed(() => {
  if (canResume.value) return ui.value.needsAttention
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
const resourceTabs = computed(() => [
  { value: 'paper' as const, label: ui.value.preview, icon: 'file-1' },
  { value: 'figures' as const, label: ui.value.figures, icon: 'chart-line' },
  ...(!isMobile.value
    ? [{ value: 'files' as const, label: ui.value.files, icon: 'folder-open' }]
    : []),
])
const mobileTabs = computed(() => [
  { value: 'chat' as const, label: ui.value.conversation, icon: 'chat' },
  { value: 'files' as const, label: ui.value.files, icon: 'folder-open' },
  { value: 'preview' as const, label: ui.value.preview, icon: 'browse' },
])
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
async function handleAttachmentChange(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) await handleFileSelect({ files: input.files, name: 'uploadAttachment' })
  input.value = ''
}
function closeFilePreview() {
  selectedFile.value = null
  currentFileData.value = null
  previewError.value = false
  previewRequestId++
}
function openFiles() {
  if (isMobile.value) activeMobilePanel.value = 'files'
  else resourceTab.value = 'files'
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
  if (target.classList.contains('chat-messages'))
    atBottom.value = target.scrollHeight - target.scrollTop - target.clientHeight < 100
}
function jumpToLatest() {
  atBottom.value = true
  scrollToBottom(true)
}
watch([activeMobilePanel, resourceTab, paperAvailable], () => {
  if (
    isMobile.value &&
    activeMobilePanel.value === 'preview' &&
    resourceTab.value === 'paper' &&
    paperAvailable.value &&
    !selectedFile.value &&
    !mainPaperLoading.value
  ) {
    void handleMainPaperClick()
  }
})
watch(resourceTab, async (tab) => {
  if (tab !== 'figures') return
  const currentId = workId.value
  await Promise.allSettled(
    figureFiles.value
      .filter((f) => !imageUrls.value[f.path])
      .map(async (f) => {
        const url = await getImageBlobUrl(f.path)
        if (currentId === workId.value) imageUrls.value[f.path] = url
        else URL.revokeObjectURL(url)
      }),
  )
})
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
    currentWork.value = work
    historyStore.upsert(work)

    // 设置当前选中的历史工作
    activeHistoryId.value = work.id

    // 加载工作空间文件
    await loadWorkspaceFiles()

    // 加载写作计划
    await loadPlanContent()
    await initializeChatSession()

    // 检查并自动发送第一句话
    await checkAndAutoSendFirstMessage()
    if (
      !isMobile.value &&
      work.status === 'completed' &&
      paperAvailable.value &&
      !selectedFile.value
    ) {
      await handleMainPaperClick()
    }
  } catch (error) {
    console.error('加载工作信息失败:', error)
    MessagePlugin.error('加载工作信息失败')
  } finally {
    loading.value = false
  }
}

// 初始化聊天会话（重构后简化）
const initializeChatSession = async () => {
  if (!authStore.token || !workId.value) return

  try {
    // 直接使用新API加载聊天历史
    await loadChatHistory()

    // 创建虚拟的session对象（兼容旧逻辑）
    currentChatSession.value = {
      id: 1,
      session_id: `${workId.value}_main_session`,
      work_id: workId.value,
      system_type: 'brain',
      title: '主会话',
      status: 'active',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      created_by: 0,
      total_messages: chatMessages.value.length,
    }

    // 检查是否有正在进行的AI任务（断线重连场景）
    await checkAndResumeRunningTask()
  } catch (error) {
    console.error('初始化聊天会话失败:', error)
    // 如果加载失败，创建空的session
    currentChatSession.value = {
      id: 1,
      session_id: `${workId.value}_main_session`,
      work_id: workId.value,
      system_type: 'brain',
      title: '主会话',
      status: 'active',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      created_by: 0,
      total_messages: 0,
    }
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

    if (taskStatus.has_task && taskStatus.status === 'running') {
      taskStartedAt.value = taskStatus.started_at ? taskStatus.started_at * 1000 : null

      // 设置流式状态
      isStreaming.value = true

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

// 为重连建立WebSocket连接
const setupWebSocketForReconnect = async () => {
  // 如果已有连接，先断开
  if (webSocketHandler.value) {
    webSocketHandler.value.disconnect()
    webSocketHandler.value = null
  }

  try {
    webSocketHandler.value = new WebSocketChatHandler(workId.value!, authStore.token!)

    // 设置重连回调
    webSocketHandler.value.onReconnect((data) => {
      console.log('重连事件:', data)
      connectionState.value = data.type === 'reconnect_complete' ? 'live' : 'reconnecting'
      if (data.type === 'reconnect') {
        MessagePlugin.info('正在恢复AI任务输出...')
      } else if (data.type === 'reconnect_complete') {
        MessagePlugin.success('历史输出恢复完成，继续接收...')
      }
    })

    // 设置消息监听 - 使用统一的消息处理
    webSocketHandler.value.onMessage((data) => {
      if (reconnectMessageId.value) {
        handleStreamMessage(data, reconnectMessageId.value)
      }
    })

    // 设置断开回调（重连用尽或被关闭）
    webSocketHandler.value.onDisconnect(() => handleConnectionLost())

    // 连接WebSocket
    await webSocketHandler.value.connect()
    connectionState.value = 'live'
  } catch (error) {
    console.error('恢复WebSocket连接失败:', error)
    isStreaming.value = false
    reconnectMessageId.value = null
    MessagePlugin.error('恢复AI任务失败')
  }
}

// 连接断开且自动重连用尽：问后端任务还在不在。任务已结束就以服务器保存的记录为准，不让界面一直停在"生成中"
const handleConnectionLost = async () => {
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

      if (block?.type === 'file_changed') {
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
      // 刷新文件列表
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

// 重构后不再需要显式创建聊天会话，MainAgent会自动处理

// 自动滚动到底部
const scrollToBottom = (force = false) => {
  if (!force && !atBottom.value) return
  nextTick(() => {
    const chatContainer = document.querySelector('.chat-messages')
    if (chatContainer) {
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

    // Open saved history at the latest message; streaming respects manual scrolling.
    atBottom.value = true
    scrollToBottom(true)
  } catch (error) {
    console.error('加载聊天历史失败:', error)
    // 如果加载失败，初始化为空数组
    chatMessages.value = []
  }
}

// 加载工作空间文件（带防抖）
let lastFileUpdateTime = 0
const loadWorkspaceFiles = async () => {
  if (!workId.value || !authStore.token) return

  const now = Date.now()
  if (isFileRefreshing.value || now - lastFileUpdateTime < 1000) {
    return
  }

  try {
    isFileRefreshing.value = true
    loading.value = true
    lastFileUpdateTime = now

    const files = await workspaceFileAPI.listFiles(authStore.token, workId.value)
    console.log('Loaded workspace files:', files)
    workspaceFiles.value = files
  } catch (error) {
    console.error('加载工作空间文件失败:', error)
    MessagePlugin.error('加载工作空间文件失败')
    workspaceFiles.value = []
  } finally {
    loading.value = false
    isFileRefreshing.value = false
  }
}

// 处理主要论文点击
const handleMainPaperClick = async () => {
  if (!workId.value || !authStore.token || mainPaperLoading.value) return
  resourceTab.value = 'paper'
  if (isMobile.value) activeMobilePanel.value = 'preview'
  const currentId = workId.value
  const requestId = ++previewRequestId
  mainPaperLoading.value = true
  previewError.value = false
  // Read the paper text; the header downloads the typeset PDF/Word.
  try {
    const response = await workspaceFileAPI.readFile(authStore.token, currentId, 'paper.md')
    if (requestId !== previewRequestId || currentId !== workId.value) return
    if (response.content?.trim()) {
      mainPaperContent.value = response.content
      showMainPaper.value = true
      selectedFile.value = 'paper.md'
      return
    }
  } catch {
    // Fall back to the formatted document when Markdown isn't available.
  } finally {
    if (requestId === previewRequestId) mainPaperLoading.value = false
  }
  if (requestId !== previewRequestId || currentId !== workId.value) return
  if (primaryArtifact.value) await handleWorkspaceFileSelect(primaryArtifact.value.path)
}

const loadPlanContent = async () => {
  if (!workId.value || !authStore.token) return
  try {
    const response = await workspaceFileAPI.readFile(authStore.token, workId.value, 'plan.json')
    planData.value = response.content ? JSON.parse(response.content) : null
  } catch {
    try {
      const response = await workspaceFileAPI.readFile(authStore.token, workId.value, 'plan.md')
      planData.value = markdownPlanToData(response.content || '')
    } catch {
      planData.value = null
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

// 处理ChatSender文件上传
const handleFileSelect = async (fileInfo: { files: FileList; name: string }) => {
  console.log('ChatSender文件选择:', fileInfo)

  const { files, name } = fileInfo
  if (!files || files.length === 0) return

  const file = files[0] // 取第一个文件
  if (!file) return

  console.log('上传文件:', file.name, file.size, file.type)

  if (!authStore.token || !workId.value) {
    MessagePlugin.error('请先登录并选择工作空间')
    return
  }

  try {
    MessagePlugin.info(`正在上传文件: ${file.name}`)

    // 使用attachmentAPI上传，保持与Home.vue一致
    const result = await attachmentAPI.uploadAttachment(authStore.token, workId.value, file)

    console.log('附件上传成功:', result)
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

// 处理FileManager文件选择
const handleWorkspaceFileSelect = async (filePath: string) => {
  console.log('文件被选中:', filePath)
  resourceTab.value = 'paper'
  const currentId = workId.value
  const requestId = ++previewRequestId
  mainPaperLoading.value = false
  previewError.value = false
  currentFileData.value = null
  currentFileName.value = filePath
  selectedFile.value = filePath // 设置选中的文件
  showMainPaper.value = false
  if (isMobile.value) {
    activeMobilePanel.value = 'preview'
  }

  // 每次都从服务器重新获取文件内容，避免缓存问题
  try {
    console.log('从服务器获取文件内容:', filePath)
    const fileData = await workspaceFileAPI.readFile(authStore.token!, currentId, filePath)
    if (requestId !== previewRequestId || currentId !== workId.value) return

    // 存储完整的文件数据
    currentFileData.value = fileData

    // 根据文件类型处理响应
    if (fileData.type === 'image') {
      console.log('图片文件，处理base64内容')
      currentFileContent.value = fileData.content || ''

      // 直接使用base64内容创建图片URL，不需要额外的blob URL
      const fileExtension = filePath.split('.').pop()?.toLowerCase()
      const mimeType = `image/${fileExtension === 'jpg' ? 'jpeg' : fileExtension}`
      imageUrls.value[filePath] = `data:${mimeType};base64,${fileData.content || ''}`
      console.log('图片base64 URL创建成功:', imageUrls.value[filePath])
    } else if (fileData.type === 'text') {
      console.log('文本文件，设置内容')
      currentFileContent.value = fileData.content || ''
      console.log('文件内容获取成功，长度:', (fileData.content || '').length)
    } else if (fileData.type === 'binary') {
      console.log('二进制文件，设置元数据')
      currentFileContent.value = 'BINARY_FILE'
      console.log('二进制文件信息:', fileData)
    }
  } catch (error) {
    if (requestId !== previewRequestId || currentId !== workId.value) return
    console.error('获取文件内容失败:', error)
    previewError.value = true
    currentFileContent.value = ''
    currentFileData.value = null
    MessagePlugin.error('加载文件失败')
  }
}

// 文件下载统一由 BinaryFileViewer 组件处理

// 格式化文件大小
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 判断是否为图片文件
const isImageFile = (filePath: string): boolean => {
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']
  const lowerPath = filePath.toLowerCase()
  return imageExtensions.some((ext) => lowerPath.endsWith(ext))
}

// 判断是否为DOCX文件
const isDocxFile = (filePath: string | null): boolean => {
  if (!filePath) return false
  return (
    filePath.toLowerCase().endsWith('.docx') || filePath.toLowerCase().includes('wordprocessingml')
  )
}

// 获取文件类型
const getFileType = (filePath: string): 'text' | 'image' | 'binary' => {
  const imageExtensions = [
    '.jpg',
    '.jpeg',
    '.png',
    '.gif',
    '.bmp',
    '.svg',
    '.webp',
    '.ico',
    '.tiff',
    '.tif',
  ]

  const textExtensions = [
    '.txt',
    '.md',
    '.py',
    '.js',
    '.ts',
    '.vue',
    '.html',
    '.css',
    '.scss',
    '.less',
    '.json',
    '.xml',
    '.yaml',
    '.yml',
    '.toml',
    '.ini',
    '.cfg',
    '.conf',
    '.c',
    '.cpp',
    '.cc',
    '.cxx',
    '.h',
    '.hpp',
    '.hxx',
    '.java',
    '.kt',
    '.scala',
    '.rs',
    '.go',
    '.php',
    '.rb',
    '.swift',
    '.sh',
    '.bash',
    '.zsh',
    '.fish',
    '.ps1',
    '.bat',
    '.cmd',
    '.sql',
    '.r',
    '.m',
    '.pl',
    '.lua',
    '.vim',
    '.dockerfile',
    '.gitignore',
    '.gitattributes',
    '.editorconfig',
    '.eslintrc',
    '.prettierrc',
    '.log',
    '.out',
    '.err',
    '.debug',
    '.trace',
  ]

  const binaryExtensions = [
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.ppt',
    '.pptx',
    '.zip',
    '.rar',
    '.7z',
    '.tar',
    '.gz',
    '.bz2',
    '.xz',
    '.exe',
    '.msi',
    '.dmg',
    '.pkg',
    '.deb',
    '.rpm',
    '.apk',
    '.mp3',
    '.mp4',
    '.avi',
    '.mov',
    'wmv',
    '.flv',
    '.mkv',
    '.ttf',
    '.otf',
    '.woff',
    '.woff2',
    '.eot',
    '.psd',
    '.ai',
    '.eps',
    '.sketch',
    '.fig',
  ]

  const ext = filePath.toLowerCase().substring(filePath.lastIndexOf('.'))

  if (imageExtensions.includes(ext)) {
    return 'image'
  } else if (textExtensions.includes(ext)) {
    return 'text'
  } else if (binaryExtensions.includes(ext)) {
    return 'binary'
  } else {
    // 未知扩展名，默认为二进制文件
    return 'binary'
  }
}

// 获取图片URL（使用新的图片API）
const getImageUrl = (filePath: string): string => {
  let decoded = filePath
  try {
    decoded = decodeURIComponent(filePath)
  } catch {
    /* keep original */
  }
  return `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/api/workspace/${workId.value}/images/${encodeURIComponent(decoded)}`
}

// 获取图片的blob URL（带认证）
const getImageBlobUrl = async (filePath: string): Promise<string> => {
  try {
    const token = authStore.token
    if (!token) {
      throw new Error('未登录')
    }

    let decoded = filePath
    try {
      decoded = decodeURIComponent(filePath)
    } catch {
      /* keep original */
    }
    const url = `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/api/workspace/${workId.value}/images/${encodeURIComponent(decoded)}`

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

// 显示分割线
const showDivider = (index: number) => {
  hoveredDivider.value = index
}

// 隐藏分割线
const hideDivider = () => {
  hoveredDivider.value = null
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
const sendMessage = async (messageContent?: string) => {
  const content = messageContent || inputValue.value.trim()
  if (!content || isStreaming.value) return

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

  // 发送真实消息
  if (currentChatSession.value && authStore.token) {
    await sendRealMessage(content)
  } else {
    MessagePlugin.error('聊天会话未初始化，请刷新页面重试')
  }
}

// 发送真实消息（WebSocket）
const sendRealMessage = async (message: string) => {
  if (!currentChatSession.value || !authStore.token) return

  isStreaming.value = true
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

  // 强制Vue更新视图
  chatMessages.value = [...chatMessages.value]

  try {
    // 使用WebSocket发送消息
    await sendMessageViaWebSocket(message, aiMessageId)
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
    MessagePlugin.error('发送消息失败')
  }
}

// WebSocket方式发送消息
const sendMessageViaWebSocket = async (message: string, aiMessageId: string) => {
  try {
    console.log('Starting WebSocket connection for message:', aiMessageId)

    // 如果已有连接，先断开
    if (webSocketHandler.value) {
      webSocketHandler.value.disconnect()
      webSocketHandler.value = null
    }

    // 创建WebSocket处理器
    webSocketHandler.value = new WebSocketChatHandler(workId.value!, authStore.token!)

    // 设置断开连接回调（重连用尽或被关闭）
    webSocketHandler.value.onDisconnect(() => handleConnectionLost())

    webSocketHandler.value.onReconnect((data) => {
      connectionState.value = data.type === 'reconnect_complete' ? 'live' : 'reconnecting'
    })
    // 设置消息监听器 - 使用统一的处理函数
    webSocketHandler.value.onMessage((data) => {
      // 忽略 start 和 auth_success 消息
      if (data.type === 'start' || data.type === 'auth_success') {
        applyWorkStatus(data.work_status)
        return
      }
      handleStreamMessage(data, aiMessageId)
    })

    // 连接WebSocket
    await webSocketHandler.value.connect()
    connectionState.value = 'live'

    // 等待一下确保监听器设置完成
    await new Promise((resolve) => setTimeout(resolve, 100))

    // 发送消息
    console.log('Sending message via WebSocket:', message)
    webSocketHandler.value.sendMessage(message)
  } catch (err) {
    console.error('WebSocket处理错误:', err)
    throw err
  }
}

// 复制消息
const copyMessage = (content: string) => {
  navigator.clipboard.writeText(content)
  MessagePlugin.success('消息已复制到剪贴板！')
}

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

// 新建工作
const createNewTask = () => {
  router.push('/home')
}

// 选择历史工作
const selectHistory = (id: number) => {
  // 侧边栏会处理跳转逻辑，这里只需要更新选中状态
  activeHistoryId.value = id
}

// 格式化日期
const formatDate = (dateString: string) => {
  return formatWorkDate(dateString)
}

// 获取状态主题
const getStatusTheme = (status: string) => {
  const themes: Record<string, 'default' | 'primary' | 'success' | 'warning' | 'danger'> = {
    created: 'default',
    in_progress: 'primary',
    running: 'primary',
    completed: 'success',
    paused: 'warning',
    waiting: 'warning',
    cancelled: 'danger',
    failed: 'danger',
  }
  return themes[status] || 'default'
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

// 获取输出模式图标
const getOutputModeIcon = (mode?: string) => {
  const icons: Record<string, string> = {
    markdown: 'file-1',
    word: 'file-word',
    latex: 'file-pdf',
  }
  return icons[mode || 'markdown'] || 'file-1'
}

// 获取输出模式主题
const getOutputModeTheme = (mode?: string) => {
  const themes: Record<string, 'default' | 'primary' | 'success' | 'warning' | 'danger'> = {
    markdown: 'primary',
    word: 'success',
    latex: 'warning',
  }
  return themes[mode || 'markdown'] || 'primary'
}

// 组件卸载时清理资源
onUnmounted(() => {
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

  // 清理当前文件内容
  currentFileContent.value = ''
  selectedFile.value = null
})

// 监听路由变化
watch(
  () => route.params.work_id,
  (newWorkId) => {
    if (newWorkId) {
      // 重置流式状态，确保输入框不被禁用
      isStreaming.value = false

      // 清理之前的WebSocket连接
      if (webSocketHandler.value) {
        webSocketHandler.value.disconnect()
        webSocketHandler.value = null
      }

      // Invalidate reads from the previous work before resetting its preview.
      previewRequestId++
      mainPaperLoading.value = false
      previewError.value = false
      phasesExpanded.value = false
      activeMobilePanel.value = 'chat'
      lastFileUpdateTime = 0
      // 清理文件内容，避免不同work的文件内容混淆
      currentFileContent.value = ''
      selectedFile.value = null
      currentFileData.value = null
      showMainPaper.value = false
      mainPaperContent.value = ''
      resourceTab.value = 'paper'
      connectionState.value = 'idle'
      taskStartedAt.value = null
      atBottom.value = true

      // 清理图片URL缓存，避免不同work的图片混淆
      Object.values(imageUrls.value).forEach((url) => {
        if (url.startsWith('blob:')) {
          URL.revokeObjectURL(url)
        }
      })
      imageUrls.value = {}

      // loadWork 内部已经会调用 initializeChatSession，不需要重复调用
      loadWork()
    }
  },
)

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
    // loadWork 内部已经会调用 initializeChatSession，不需要重复调用
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
      await sendMessage(pendingQuestion)
    } catch (error) {
      console.error('自动发送第一句话失败:', error)
    }
  }
}

const generateWorkTitle = async (question: string) => {
  try {
    // 调用标题生成API，会自动更新数据库
    const response = await chatAPI.generateTitle(authStore.token!, workId.value!, question)

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
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.workspace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-shrink: 0;
  background: #fff;
  border-bottom: 1px solid var(--td-component-stroke);
}
.work-heading {
  min-width: 0;
}
.work-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.work-title-row h1 {
  font-family: var(--td-font-family);
  font-size: 20px;
  font-weight: 600;
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.5;
}
.work-title-row .t-tag {
  flex-shrink: 0;
}
.work-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}
.progress-strip {
  flex-shrink: 0;
  border-bottom: 1px solid var(--td-component-stroke);
}
.progress-summary {
  display: flex;
  gap: 9px;
  align-items: center;
  min-height: 20px;
}
.progress-summary strong {
  font-size: 13px;
  font-weight: 500;
  max-width: 65%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--td-text-color-placeholder);
  flex-shrink: 0;
}
.live-dot.running {
  background: var(--td-success-color);
}
.live-dot.attention {
  background: var(--td-warning-color);
}
.elapsed {
  color: var(--td-text-color-secondary);
  margin-left: 8px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.connection-state {
  margin-left: auto;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}
.connection-state.lost {
  color: var(--td-error-color);
}
.phase-track {
  align-items: center;
  overflow-x: auto;
  padding-bottom: 2px;
  scrollbar-width: thin;
}
.phase-step {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  flex: 1;
}
.phase-number {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border: 1px solid var(--td-component-border);
  border-radius: 50%;
  font-size: 11px;
  flex-shrink: 0;
}
.phase-number > .t-icon {
  font-size: 12px;
}
.phase-step:last-child {
  flex: 0;
}
.phase-step.is-completed {
  color: var(--td-success-color);
}
.phase-step.is-completed .phase-number {
  border-color: var(--td-success-color);
}
.phase-step.is-in_progress {
  color: var(--td-brand-color);
  font-weight: 500;
}
.phase-step.is-in_progress .phase-number {
  background: var(--td-brand-color);
  border-color: var(--td-brand-color);
  color: #fff;
}
.phase-step.is-blocked {
  color: var(--td-warning-color);
}
.recovery-banner {
  margin: 16px 20px 0;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #eadbbd;
  background: var(--td-warning-color-light);
  padding: 14px 16px;
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
  font-size: 12px;
  color: var(--td-text-color-secondary);
  margin: 6px 0 0;
  line-height: 1.8;
}
.recovery-banner .t-button {
  flex-shrink: 0;
}
.confirmation-banner {
  background: var(--td-brand-color-light);
  border-color: var(--td-component-stroke);
}
.workspace-content {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.chat-section {
  width: 48%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  overflow: hidden;
  border-right: 1px solid var(--td-component-stroke);
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--td-component-stroke);
  box-sizing: border-box;
  flex-shrink: 0;
}
.panel-header > span:first-child {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 500;
}
.panel-header .t-icon {
  color: var(--td-text-color-secondary);
  font-size: 16px;
}
.panel-hint {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}
.chat-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.chat-messages-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
}
.chat-messages-container :deep(.json-chat-renderer) {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.chat-messages-container :deep(.chat-messages) {
  height: 100%;
  overflow-y: auto;
  padding: 20px 16px;
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
  font-size: 14px;
  line-height: 1.85;
}
.chat-messages-container :deep(.markdown-body h1) {
  font-size: 22px;
}
.chat-messages-container :deep(.markdown-body h2) {
  font-size: 19px;
}
.chat-messages-container :deep(.markdown-body h3) {
  font-size: 16px;
}
.chat-input {
  background: #fff;
  flex-shrink: 0;
}
.input-note {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 36px;
  gap: 10px;
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}
.input-note .t-button {
  font-size: 12px;
  padding: 0 4px;
}
.jump-latest {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--td-component-border);
  background: #fff;
  box-shadow: var(--td-shadow-1);
  color: var(--td-text-color-secondary);
  font-size: 12px;
  padding: 8px 14px;
  cursor: pointer;
  white-space: nowrap;
}
.preview-section {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  overflow: hidden;
}
.preview-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.paper-preview,
.selected-preview {
  min-height: 100%;
  box-sizing: border-box;
  background: #fff;
}
.document-caption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  font-size: 12px;
  border-bottom: 1px solid var(--td-component-stroke);
}
.document-caption > span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.text-action {
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--td-brand-color);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  white-space: nowrap;
}
.text-preview pre {
  font-size: 13px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
}
.image-preview {
  padding: 10px 0;
}
.image-preview img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: auto;
}
.resource-files,
.files-section {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.resource-files :deep(.file-manager) {
  border: 0;
  border-radius: 0;
  height: 100%;
}
.figures-panel {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 20px;
  background: #fff;
}
.figure-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.figure-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  border: 1px solid var(--td-component-stroke);
  background: #fff;
  cursor: pointer;
  text-align: left;
  overflow: hidden;
}
.figure-card img {
  width: 100%;
  height: 150px;
  object-fit: contain;
}
.figure-card > span {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.figure-placeholder {
  display: grid;
  place-items: center;
  height: 150px;
  background: #fafafa;
  color: var(--td-text-color-placeholder);
  font-size: 30px;
}
.mobile-tab-bar {
  display: none;
}
.work-details h2 {
  font-family: var(--td-font-family);
  font-size: 20px;
  margin: 20px 0;
}
.work-details p {
  white-space: pre-wrap;
  line-height: 1.9;
  font-size: 14px;
  color: var(--td-text-color-secondary);
}
.work-details dl {
  margin-top: 24px;
  border-top: 1px solid var(--td-component-stroke);
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
.preview-body :deep(.markdown-body) {
  font-size: 14px;
  line-height: 1.9;
}
.preview-body :deep(.markdown-body h1) {
  font-size: 24px;
}
.preview-body :deep(.markdown-body h2) {
  font-size: 20px;
}
.preview-placeholder {
  height: 100%;
  display: flex;
  justify-content: center;
}
.preview-placeholder h2 {
  font-size: 18px;
}
.preview-empty-actions {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-top: 8px;
}
@media (max-width: 1200px) {
  .phase-step {
    flex: none;
    font-size: 11px;
  }
  .recovery-banner {
    margin: 12px 16px 0;
  }
}
@media (max-width: 768px) {
  .work-title-row {
    gap: 7px;
  }
  .work-title-row h1 {
    max-width: 100%;
  }
  .work-title-row .t-tag {
    font-size: 10px;
    height: 20px;
    padding: 0 5px;
  }
  .work-actions .t-button {
    padding: 0 8px;
  }
  .work-actions .t-button--shape-square {
    padding: 0;
  }
  .progress-summary {
    gap: 7px;
  }
  .progress-summary strong {
    font-size: 12px;
    max-width: 70%;
  }
  .connection-state {
    font-size: 11px;
  }
  .elapsed {
    display: none;
  }
  .phase-step {
    flex: none;
  }
  .phase-number {
    width: 18px;
    height: 18px;
    font-size: 10px;
  }
  .workspace-content {
    min-height: 0;
  }
  .chat-section,
  .preview-section,
  .files-section {
    width: 100%;
    flex: 1;
    min-height: 0;
    border: 0;
  }
  .chat-messages-container :deep(.chat-messages) {
    padding: 14px 12px;
  }
  .chat-messages-container :deep(.markdown-body) {
    font-size: 14px;
  }
  .panel-header > span:first-child {
    font-size: 13px;
  }
  .panel-hint {
    font-size: 11px;
  }
  .input-note {
    min-height: 32px;
    font-size: 10px;
  }
  .recovery-banner {
    margin: 12px;
    padding: 12px;
    gap: 9px;
    flex-wrap: wrap;
  }
  .recovery-banner > .t-icon {
    font-size: 20px;
    align-self: start;
    margin-top: 2px;
  }
  .recovery-banner > div {
    min-width: calc(100% - 35px);
  }
  .recovery-banner strong {
    font-size: 13px;
  }
  .recovery-banner p {
    font-size: 12px;
    line-height: 1.7;
  }
  .recovery-banner > .t-button {
    margin-left: 29px;
    height: 30px;
    font-size: 12px;
  }
  .mobile-tab-bar {
    display: flex;
    flex-shrink: 0;
    justify-content: space-around;
    align-items: center;
    background: #fff;
    border-top: 1px solid var(--td-component-stroke);
  }
  .mobile-tab-bar button {
    background: transparent;
    border: 0;
    cursor: pointer;
    color: var(--td-text-color-secondary);
    display: flex;
    align-items: center;
    flex: 1;
    white-space: nowrap;
  }
  .figure-grid {
    gap: 12px;
  }
  .figure-card img,
  .figure-placeholder {
    height: 120px;
  }
  .figures-panel {
    padding: 16px;
  }
  .preview-body :deep(.markdown-body) {
    font-size: 14px;
  }
  .preview-placeholder {
    padding-top: 40px;
  }
  .studio-empty {
    padding: 24px 12px;
  }
}
.document-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.document-actions > .t-button {
  font-size: 12px;
  padding: 0 4px;
}
@media (max-width: 768px) {
  .document-caption {
    gap: 8px;
    flex-wrap: wrap;
  }
  .document-actions {
    gap: 12px;
  }
}

.workspace-header,
.progress-strip,
.panel-header,
.resource-tabs {
  border-color: #ededed;
}
.chat-input {
  border-top: 0;
  padding: 12px 18px 0;
}
.chat-input :deep(.t-chat__sender__textarea) {
  border-radius: 24px;
  border-color: #d9dedd;
  background: #fcfcfc;
  box-shadow: 0 3px 14px #00000007;
  overflow: hidden;
}
.chat-input :deep(.t-chat__sender__textarea:focus-within) {
  border-color: #c8ceca;
}
.chat-input :deep(.t-chat__sender__textarea textarea) {
  font-size: 14px;
  border-radius: 0;
  padding: 14px 16px;
  line-height: 1.8;
}
.chat-input :deep(.t-chat__sender__textarea textarea::placeholder) {
  font-size: 13px;
}
.chat-input :deep(.t-chat__sender__suffix .t-button) {
  width: 32px;
  height: 32px;
  min-width: 32px;
}
.chat-input :deep(.t-chat__sender__footer) {
  padding: 0 12px 10px;
}
.input-note {
  padding: 1px 5px;
}
.recovery-banner {
  border-radius: 13px;
}
.jump-latest {
  border-radius: 18px;
}
@media (max-width: 768px) {
  .chat-input {
    padding: 10px 12px 0;
  }
  .chat-input :deep(.t-chat__sender__textarea) {
    border-radius: 22px;
  }
  .chat-input :deep(.t-chat__sender__textarea textarea) {
    font-size: 16px;
  }
  .chat-input :deep(.t-chat__sender__textarea textarea::placeholder) {
    font-size: 13px;
  }
}
/* Shared workspace geometry: a single 52px toolbar baseline on both panes. */
.workspace-header {
  min-height: 76px;
  padding: 18px 24px;
  box-sizing: border-box;
}
.work-actions .t-button {
  height: 36px;
}
.work-actions .t-button--shape-square {
  width: 36px;
}
.progress-strip {
  padding: 10px 24px;
}
.progress-label {
  color: var(--td-text-color-secondary);
  font-size: 12px;
  margin-right: 6px;
  flex-shrink: 0;
}
.live-dot.complete {
  background: var(--td-brand-color);
}
.phase-toggle {
  margin-left: auto;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  border: 0;
  border-radius: 6px;
  padding: 0 6px;
  color: var(--td-text-color-secondary);
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.phase-toggle:hover {
  background: #f2f4f2;
  color: var(--td-text-color-primary);
}
.phase-track {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(115px, 1fr));
  gap: 14px 12px;
  overflow: visible;
  padding: 10px 0 6px;
  margin-top: 8px;
  border-top: 1px solid #ededed;
}
.phase-step,
.phase-step:last-child {
  font-size: 12px;
  gap: 8px;
}
.panel-header {
  height: 52px;
  min-height: 52px;
  padding: 0 20px;
}
.resource-tabs {
  flex-shrink: 0;
  height: 52px;
  padding: 0 20px;
  border-bottom: 1px solid #ededed;
  box-sizing: border-box;
}
.resource-tabs :deep(.t-tabs__header),
.resource-tabs :deep(.t-tabs__nav),
.resource-tabs :deep(.t-tabs__nav-container),
.resource-tabs :deep(.t-tabs__nav-scroll) {
  height: 51px;
  min-width: 0;
}
.resource-tabs :deep(.t-tabs__nav-container.t-is-top::after) {
  display: none;
}
.resource-tabs :deep(.t-tabs__nav-item) {
  height: 51px;
  padding: 0 16px;
  font-size: 14px;
}
.resource-tabs :deep(.t-tabs__nav-item-wrapper) {
  margin: 0;
  padding: 0;
}
.resource-tabs :deep(.t-tabs__nav-item:first-child) {
  padding-left: 0;
}
.resource-tabs :deep(.t-tabs__content) {
  display: none;
}
.resource-tab-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.resource-tab-label > span {
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 5px;
  background: #f0f2f0;
  color: var(--td-text-color-secondary);
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  box-sizing: border-box;
}
.preview-body {
  background: #fff;
  padding: 24px;
}
.paper-preview,
.selected-preview {
  max-width: 840px;
  margin: 0 auto;
  border: 1px solid #e3e6e3;
  border-radius: 12px;
  padding: 24px 28px 36px;
}
.document-caption {
  min-height: 32px;
  margin-bottom: 24px;
  padding-bottom: 16px;
  color: var(--td-text-color-secondary);
}
.preview-loading {
  min-height: 200px;
  display: grid;
  place-items: center;
}
.preview-placeholder {
  min-height: 240px;
  align-items: center;
  padding: 0;
}
.preview-placeholder .studio-empty {
  min-height: 0;
  max-width: 360px;
}
.preview-placeholder .studio-empty > .t-icon {
  font-size: 32px;
  color: #727b75;
}
.figure-card {
  border-radius: 10px;
  border-color: #e3e6e3;
  padding: 14px;
}
.figure-card:hover {
  border-color: #b8c3bc;
  background: #fcfdfc;
}
@media (max-width: 768px) {
  .workspace-header {
    min-height: 64px;
    padding: 14px 16px;
    gap: 12px;
  }
  .work-title-row {
    flex-wrap: nowrap;
  }
  .work-title-row h1 {
    font-size: 15px;
    line-height: 1.5;
  }
  .work-actions {
    gap: 8px;
  }
  .work-actions .t-button {
    height: 32px;
    font-size: 12px;
  }
  .work-actions .t-button--shape-square {
    width: 32px;
  }
  .progress-strip {
    padding: 8px 16px;
  }
  .progress-label {
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
  .panel-header,
  .resource-tabs {
    padding: 0 16px;
  }
  .resource-tabs :deep(.t-tabs__nav-item) {
    font-size: 13px;
  }
  .preview-body {
    padding: 16px;
  }
  .paper-preview,
  .selected-preview {
    padding: 16px 18px 24px;
    border-radius: 10px;
  }
  .document-caption {
    margin-bottom: 20px;
  }
  .mobile-tab-bar {
    gap: 8px;
    padding: 8px 16px max(8px, env(safe-area-inset-bottom));
    min-height: 56px;
    box-sizing: border-box;
    border-color: #e3e6e3;
  }
  .mobile-tab-bar button {
    flex-direction: row;
    justify-content: center;
    gap: 7px;
    min-height: 40px;
    font-size: 12px;
    border-radius: 9px;
    padding: 8px 10px;
  }
  .mobile-tab-bar button.active {
    background: #eef3ef;
    color: var(--td-brand-color);
    font-weight: 500;
  }
  .mobile-tab-bar button > .t-icon {
    font-size: 18px;
  }
}
@media (max-width: 380px) {
  .phase-track {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .mobile-tab-bar {
    padding-inline: 8px;
    gap: 2px;
  }
  .mobile-tab-bar button {
    font-size: 11px;
    padding-inline: 4px;
  }
}
</style>
