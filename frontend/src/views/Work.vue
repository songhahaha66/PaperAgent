<template>
  <div class="work-page" :class="{ 'is-mobile': isMobile }">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
    />

    <div class="main-content">
      <div class="workspace-header" v-if="currentWork">
        <div class="work-info">
          <div class="work-title-row">
            <h1>
              <span v-if="currentWork.title && currentWork.title.trim()">{{
                currentWork.title
              }}</span>
              <t-loading v-else size="small" text="生成中" />
            </h1>
            <t-tag :theme="getStatusTheme(currentWork.status)" variant="light">
              {{ getStatusText(currentWork.status) }}
            </t-tag>
            <p v-if="!isMobile">生成过程中请耐心等待！</p>
          </div>
          <p v-if="!isMobile">创建于 {{ formatDate(currentWork.created_at) }}</p>
        </div>
        <div v-if="!isMobile" class="work-actions">
          <t-button
            theme="primary"
            variant="outline"
            size="middle"
            @click="exportWorkspace"
            :loading="exportLoading"
          >
            <template #icon>
              <t-icon name="download" />
            </template>
            导出文件
          </t-button>
          <t-button theme="danger" variant="outline" size="middle" @click="deleteWork">
            <template #icon>
              <t-icon name="delete" />
            </template>
            删除
          </t-button>
        </div>
        <t-dropdown v-else :options="mobileWorkActionOptions" trigger="click">
          <t-button theme="default" variant="text" shape="square">
            <t-icon name="more" />
          </t-button>
        </t-dropdown>
      </div>

      <div class="workspace-header" v-else>
        <h1>论文生成工作区</h1>
        <p>正在加载工作信息...</p>
      </div>

      <div class="workspace-content" :class="{ 'is-mobile': isMobile }">
        <div class="chat-section" v-show="!isMobile || activeMobilePanel === 'chat'">
          <div class="chat-container">
            <div class="chat-messages-container">
              <JsonChatRenderer :messages="chatMessages" />
            </div>
            <div class="chat-bottom-section">
              <FileManager
                v-if="!isMobile"
                :work-id="workId"
                :loading="loading"
                :file-tree-data="workspaceFiles"
                :plan-data="planData"
                @file-select="handleWorkspaceFileSelect"
                @refresh="handleFileRefresh"
                @main-paper-click="handleMainPaperClick"
              />
              <WorkConfirmBar
                :visible="awaitingConfirmation"
                :issues="confirmationIssues"
                :disabled="isStreaming"
                @confirm="confirmCurrentDraft"
                @dismiss="awaitingConfirmation = false"
              />
              <div class="chat-input">
                <ChatSender
                  v-model="inputValue"
                  placeholder="请输入您的问题..."
                  @send="sendMessage"
                  @file-select="handleFileSelect"
                  :disabled="isStreaming"
                >
                  <template #suffix="{ renderPresets }">
                    <component :is="renderPresets([{ name: 'uploadAttachment' }])" />
                  </template>
                </ChatSender>
              </div>
            </div>
          </div>
        </div>

        <div
          v-if="isMobile"
          class="files-section"
          v-show="activeMobilePanel === 'files'"
        >
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

        <WorkPreviewPanel
          class="preview-section"
          v-show="!isMobile || activeMobilePanel === 'preview'"
          :is-mobile="isMobile"
          :show-main-paper="showMainPaper"
          :main-paper-content="mainPaperContent"
          :selected-file="selectedFile"
          :current-file-data="currentFileData"
          :current-file-content="currentFileContent"
          :image-urls="imageUrls"
          :current-work="currentWork"
          :work-id="workId"
          :token="authStore.token || ''"
          @close-main-paper="showMainPaper = false"
        />
      </div>

      <WorkMobileTabs v-if="isMobile" v-model="activeMobilePanel" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { MessagePlugin } from 'tdesign-vue-next';
import { ChatItem, ChatSender } from '@tdesign-vue-next/chat';

import { useAuthStore } from '@/stores/auth';
import { workspaceAPI, workspaceFileAPI, attachmentAPI, type Work, type FileInfo, type PlanData } from '@/api/workspace';
import { chatAPI, WebSocketChatHandler, type ChatMessage, type ChatSessionResponse, type ChatSessionCreateRequest } from '@/api/chat';
import Sidebar from '@/components/Sidebar.vue';
import FileManager from '@/components/FileManager.vue';
import { useBreakpoint } from '@/composables/useBreakpoint';
import { CONFIRM_DRAFT_MESSAGE, latestRunEvents, useAguiEvents } from '@/composables/useAguiEvents';
import { markdownPlanToData } from '@/composables/useWorkPlan';
import JsonChatRenderer from '@/components/JsonChatRenderer.vue';
import WorkConfirmBar from '@/components/WorkConfirmBar.vue';
import WorkMobileTabs from '@/components/WorkMobileTabs.vue';
import WorkPreviewPanel from '@/components/WorkPreviewPanel.vue';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const workId = computed(() => route.params.work_id as string);

const { isMobile } = useBreakpoint()

// 侧边栏折叠状态 - 手机端默认收起
const isSidebarCollapsed = ref(isMobile.value)

type MobilePanel = 'chat' | 'files' | 'preview'
const activeMobilePanel = ref<MobilePanel>('chat')

watch(isMobile, (mobile) => {
  if (mobile) {
    isSidebarCollapsed.value = true
  }
})

const mobileWorkActionOptions = [
  {
    content: '导出文件',
    value: 'export',
    onClick: () => exportWorkspace(),
  },
  {
    content: '删除',
    value: 'delete',
    theme: 'error',
    onClick: () => deleteWork(),
  },
]

// 当前工作信息
const currentWork = ref<Work | null>(null)

// 加载状态
const loading = ref(false)

// 定义聊天消息类型（使用API中的类型）
interface ChatMessageDisplay extends ChatMessage {
}

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

const planData = ref<PlanData | null>(null)
const awaitingConfirmation = ref(false)
const confirmationIssues = ref<import('@/composables/useAguiEvents').ValidationIssuePreview[]>([])

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

// 加载工作信息
const loadWork = async () => {
  if (!workId.value || !authStore.token) return

  loading.value = true
  try {
    const work = await workspaceAPI.getWork(authStore.token, workId.value)
    currentWork.value = work

    // 设置当前选中的历史工作
    activeHistoryId.value = work.id

    // 加载工作空间文件
    await loadWorkspaceFiles()

    // 加载写作计划
    await loadPlanContent()
    await initializeChatSession()

    // 检查并自动发送第一句话
    await checkAndAutoSendFirstMessage()
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

    await replayPersistedEvents()
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

const replayPersistedEvents = async () => {
  if (!authStore.token || !workId.value) return
  try {
    const result = await chatAPI.getRunEvents(authStore.token, workId.value, 0)
    // Only the last run decides whether we are still waiting for a confirmation.
    for (const event of latestRunEvents(result.events || [])) {
      handleAguiEvent(event, '')
    }
  } catch (error) {
    console.debug('回放运行事件失败', error)
  }
}

// 检查并恢复正在进行的AI任务
const checkAndResumeRunningTask = async () => {
  if (!authStore.token || !workId.value) return

  try {
    // 查询任务状态
    const taskStatus = await chatAPI.getTaskStatus(authStore.token, workId.value)
    
    if (taskStatus.has_task && taskStatus.status === 'running') {
      console.log('检测到正在进行的AI任务，准备恢复...', taskStatus)
      
      // 设置流式状态
      isStreaming.value = true
      
      // 创建AI回复消息框架
      const aiMessageId = `reconnect_${Date.now()}`
      reconnectMessageId.value = aiMessageId
      
      const aiMessage: ChatMessageDisplay = {
        id: aiMessageId,
        role: 'assistant' as const,
        content: '',
        datetime: new Date().toLocaleString(),
        avatar: 'https://api.dicebear.com/7.x/bottts/svg?seed=assistant&backgroundColor=0052d9',
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
    
    // 设置断开回调
    webSocketHandler.value.onDisconnect(() => {
      console.log('WebSocket连接断开')
      // 不立即重置 isStreaming，可能是临时断开
    })
    
    // 连接WebSocket
    await webSocketHandler.value.connect()
    console.log('WebSocket重连成功')
    
  } catch (error) {
    console.error('恢复WebSocket连接失败:', error)
    isStreaming.value = false
    reconnectMessageId.value = null
    MessagePlugin.error('恢复AI任务失败')
  }
}

const { handleAguiEvent, handleStreamMessage } = useAguiEvents({
  chatMessages,
  planData,
  loadWorkspaceFiles: () => {
    void loadWorkspaceFiles()
  },
  awaitingConfirmation,
  confirmationIssues,
  isStreaming,
  reconnectMessageId,
  scrollToBottom: () => scrollToBottom(),
})

const confirmCurrentDraft = () => {
  awaitingConfirmation.value = false
  void sendMessage(CONFIRM_DRAFT_MESSAGE)
}

// 重构后不再需要显式创建聊天会话，MainAgent会自动处理

// 自动滚动到底部
const scrollToBottom = () => {
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

    // 加载完聊天历史后自动滚动到底部
    scrollToBottom()
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
  if (isFileRefreshing.value || (now - lastFileUpdateTime < 1000)) {
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
  if (!workId.value || !authStore.token) return

  try {
    const response = await workspaceFileAPI.readFile(authStore.token, workId.value, 'paper.md')
    mainPaperContent.value = response.content || ''
    showMainPaper.value = true
    selectedFile.value = 'paper.md'
    if (isMobile.value) {
      activeMobilePanel.value = 'preview'
    }
  } catch (error) {
    console.error('获取论文内容失败:', error)
    MessagePlugin.error('获取论文内容失败')
  }
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
const handleFileSelect = async (fileInfo: {files: FileList, name: string}) => {
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
    const result = await attachmentAPI.uploadAttachment(
      authStore.token,
      workId.value,
      file
    )

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
      avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
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
  currentFileName.value = filePath
  selectedFile.value = filePath // 设置选中的文件
  showMainPaper.value = false
  if (isMobile.value) {
    activeMobilePanel.value = 'preview'
  }

  // 每次都从服务器重新获取文件内容，避免缓存问题
  try {
    console.log('从服务器获取文件内容:', filePath)
    const fileData = await workspaceFileAPI.readFile(authStore.token!, workId.value, filePath)
    console.log('API返回的文件数据:', fileData)

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
    console.error('获取文件内容失败:', error)
    currentFileContent.value = '文件读取失败'
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
  return filePath.toLowerCase().endsWith('.docx') || 
         filePath.toLowerCase().includes('wordprocessingml')
}

// 获取文件类型
const getFileType = (filePath: string): 'text' | 'image' | 'binary' => {
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff', '.tif']

  const textExtensions = [
    '.txt', '.md', '.py', '.js', '.ts', '.vue', '.html', '.css', '.scss', '.less',
    '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
    '.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.hxx',
    '.java', '.kt', '.scala', '.rs', '.go', '.php', '.rb', '.swift',
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
    '.sql', '.r', '.m', '.pl', '.lua', '.vim', '.dockerfile',
    '.gitignore', '.gitattributes', '.editorconfig', '.eslintrc', '.prettierrc',
    '.log', '.out', '.err', '.debug', '.trace'
  ]

  const binaryExtensions = [
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz',
    '.exe', '.msi', '.dmg', '.pkg', '.deb', '.rpm', '.apk',
    '.mp3', '.mp4', '.avi', '.mov', 'wmv', '.flv', '.mkv',
    '.ttf', '.otf', '.woff', '.woff2', '.eot',
    '.psd', '.ai', '.eps', '.sketch', '.fig'
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
  try { decoded = decodeURIComponent(filePath) } catch { /* keep original */ }
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
    try { decoded = decodeURIComponent(filePath) } catch { /* keep original */ }
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

// 根据角色获取头像
const getAvatarByRole = (role: string) => {
  const avatars = {
    user: 'https://tdesign.gtimg.com/site/avatar.jpg',
    assistant: 'https://api.dicebear.com/7.x/bottts/svg?seed=assistant&backgroundColor=0052d9',
    system: 'https://api.dicebear.com/7.x/bottts/svg?seed=system&backgroundColor=ed7b2f',
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
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
  }

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

  // 创建AI回复消息
  const aiMessageId = (Date.now() + 1).toString()
  const aiMessage: ChatMessageDisplay = {
    id: aiMessageId,
    role: 'assistant' as const,
    content: '',
    datetime: new Date().toLocaleString(),
    avatar: 'https://api.dicebear.com/7.x/bottts/svg?seed=assistant&backgroundColor=0052d9',
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

    // 设置断开连接回调
    webSocketHandler.value.onDisconnect(() => {
      console.log('WebSocket连接已断开')
    })

    // 设置消息监听器 - 使用统一的处理函数
    webSocketHandler.value.onMessage((data) => {
      // 忽略 start 和 auth_success 消息
      if (data.type === 'start' || data.type === 'auth_success') {
        console.log('收到消息:', data.type)
        return
      }
      handleStreamMessage(data, aiMessageId)
    })

    // 连接WebSocket
    await webSocketHandler.value.connect()
    console.log('WebSocket connected successfully')

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
  return new Date(dateString).toLocaleString()
}

// 获取状态主题
const getStatusTheme = (status: string) => {
  const themes: Record<string, 'default' | 'primary' | 'success' | 'warning' | 'danger'> = {
    created: 'default',
    in_progress: 'primary',
    completed: 'success',
    paused: 'warning',
    cancelled: 'danger',
  }
  return themes[status] || 'default'
}

// 获取状态文本
const getStatusText = (status: string) => {
  const texts: Record<string, string> = {
    created: '已创建',
    in_progress: '进行中',
    completed: '已完成',
    paused: '已暂停',
    cancelled: '已取消',
  }
  return texts[status] || status
}

// 获取输出模式文本
const getOutputModeText = (mode?: string) => {
  const texts: Record<string, string> = {
    markdown: 'Markdown',
    word: 'Word (.docx)',
    latex: 'LaTeX'
  }
  return texts[mode || 'markdown'] || 'Markdown'
}

// 获取输出模式图标
const getOutputModeIcon = (mode?: string) => {
  const icons: Record<string, string> = {
    markdown: 'file-1',
    word: 'file-word',
    latex: 'file-pdf'
  }
  return icons[mode || 'markdown'] || 'file-1'
}

// 获取输出模式主题
const getOutputModeTheme = (mode?: string) => {
  const themes: Record<string, 'default' | 'primary' | 'success' | 'warning' | 'danger'> = {
    markdown: 'primary',
    word: 'success',
    latex: 'warning'
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

      // 清理文件内容，避免不同work的文件内容混淆
      currentFileContent.value = ''
      selectedFile.value = null

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
  const pendingQuestion = localStorage.getItem('pendingQuestion')
  if (pendingQuestion && currentWork.value?.title?.trim() === '') {
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

<style>
/* 全局样式确保页面占满视口 */
html,
body {
  margin: 0;
  padding: 0;
  height: 100%;
  overflow: hidden;
}

#app {
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
}
</style>

<style scoped>
.work-page {
  display: flex;
  height: 100vh;
  height: 100dvh;
  width: 100vw;
  background: #f5f7fa;
  overflow: hidden;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.workspace-header {
  padding: 15px 30px;
  background: white;
  border-bottom: 1px solid #eee;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.work-info {
  flex: 1;
}

.work-info h1 {
  margin: 0 0 8px 0;
  color: #2c3e50;
  font-size: 1.5em;
}

.work-info p {
  margin: 0;
  color: #7f8c8d;
  font-size: 0.9em;
}

.work-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.work-actions {
  display: flex;
  gap: 8px;
}

.workspace-content {
  flex: 1;
  display: flex;
  padding: 0;
  overflow: hidden;
  height: calc(100vh - 120px); /* 固定高度，减去header高度 */
}

.chat-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #eee;
  padding: 20px;
  min-width: 300px;
  overflow: hidden;
  height: 100%; /* 确保占满父容器高度 */
}

.preview-section {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  background: #f9f9f9;
  height: 100%; /* 确保占满父容器高度 */
}

.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid #eee;
  border-radius: 8px;
  overflow: hidden;
  background: white;
  height: 100%; /* 确保占满父容器高度 */
}

.chat-messages-container {
  flex: 1;
  overflow: hidden;
  min-height: 0; /* 允许flex子项收缩 */
}

.chat-bottom-section {
  flex-shrink: 0; /* 防止底部区域被压缩 */
  display: flex;
  flex-direction: column;
  border-top: 1px solid #eee;
  background: white;
}

.chat-messages {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  background: #fafafa;
  min-height: 0; /* 允许flex子项收缩 */
}

.chat-input {
  padding: 16px;
  border-top: 1px solid #eee;
  background: white;
  flex-shrink: 0; /* 防止输入框被压缩 */
  min-height: 80px; /* 确保输入框有最小高度 */
}

/* 确保FileManager组件有合适的高度 */
.chat-bottom-section .file-manager {
  flex-shrink: 0; /* 防止文件管理器被压缩 */
  max-height: 300px; /* 限制文件管理器最大高度 */
  overflow-y: auto; /* 如果内容过多，允许滚动 */
}

.chat-message-wrapper {
  position: relative;
  margin-bottom: 8px;
}

.system-label {
  position: absolute;
  top: 8px;
  right: 16px;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  z-index: 1;
}

.system-label.brain {
  background: rgba(0, 82, 217, 0.1);
  color: #0052d9;
  border: 1px solid rgba(0, 82, 217, 0.2);
}

.system-label.code {
  background: rgba(0, 168, 112, 0.1);
  color: #00a870;
  border: 1px solid rgba(0, 168, 112, 0.2);
}

.system-label.writing {
  background: rgba(237, 123, 47, 0.1);
  color: #ed7b2f;
  border: 1px solid rgba(237, 123, 47, 0.2);
}

/* 对话分割线样式 */
.message-divider {
  position: relative;
  height: 40px;
  margin: 12px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
}

.message-divider:hover {
  height: 50px;
  margin: 6px 0;
}

.divider-line {
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background: #e0e0e0;
  transition: all 0.3s ease;
}

.message-divider:hover .divider-line {
  background: #c0c0c0;
  height: 2px;
}

.divider-icon {
  position: relative;
  width: 36px;
  height: 36px;
  background: #f5f5f5;
  border: 1px solid #e0e0e0;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transform: scale(0.8);
  transition: all 0.3s ease;
  z-index: 1;
  cursor: pointer;
}

.divider-icon.show {
  opacity: 1;
  transform: scale(1);
  background: #f0f0f0;
  border-color: #c0c0c0;
}

.divider-icon .t-icon {
  font-size: 16px;
  color: #666;
}

.message-divider:hover .divider-icon {
  opacity: 1;
  transform: scale(1);
  background: #e8e8e8;
  border-color: #b0b0b0;
}

/* 对话变灰效果 */
.message-dimmed {
  opacity: 0.4;
  filter: grayscale(0.6);
  transition: all 0.3s ease;
}

.message-dimmed .t-chat__message {
  opacity: 0.4;
}

.message-dimmed .system-label {
  opacity: 0.4;
}

.work-details {
  padding: 16px;
  background: #f0f2f5;
  border-radius: 8px;
  border: 1px solid #e0e0e0;
}

.work-details p {
  margin: 8px 0;
  color: #555;
  font-size: 14px;
}

.work-details strong {
  color: #333;
  font-weight: 600;
}

/* 文件预览样式 */
.file-preview {
  max-height: 600px;
  overflow-y: auto;
}

.code-preview {
  background-color: #f5f5f5;
  border-radius: 4px;
  padding: 16px;
  overflow-x: auto;
}

.code-preview pre {
  margin: 0;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.markdown-preview {
  padding: 16px;
  line-height: 1.6;
}

.image-preview {
  text-align: center;
  padding: 16px;
}

.image-preview img {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.text-preview {
  background-color: #f5f5f5;
  border-radius: 4px;
  padding: 16px;
  overflow-x: auto;
}

.text-preview pre {
  margin: 0;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.files-section {
  display: none;
}

.mobile-tab-bar {
  display: none;
}

.work-page.is-mobile {
  height: 100vh;
  height: 100dvh;
}

.work-page.is-mobile :deep(.mobile-expand-btn-container) {
  bottom: calc(72px + env(safe-area-inset-bottom, 0px));
}

.workspace-content.is-mobile {
  flex-direction: column;
  height: auto;
  flex: 1;
  min-height: 0;
}

.workspace-content.is-mobile .chat-section,
.workspace-content.is-mobile :deep(.preview-section),
.workspace-content.is-mobile .files-section {
  flex: 1;
  min-width: 0;
  min-height: 0;
  width: 100%;
  height: auto;
  padding: 8px;
  border-right: none;
}

.workspace-content.is-mobile .files-section {
  display: flex;
  flex-direction: column;
  background: #fff;
  overflow: hidden;
}

.workspace-content.is-mobile .file-preview {
  max-height: none;
}

.work-page.is-mobile .workspace-header {
  padding: 10px 12px;
}

.work-page.is-mobile .work-info h1 {
  font-size: 1.05em;
  margin: 0;
}

.work-page.is-mobile .mobile-tab-bar {
  display: flex;
  flex-shrink: 0;
  border-top: 1px solid #eee;
  background: #fff;
  padding-bottom: env(safe-area-inset-bottom, 0px);
}

.mobile-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 8px 0;
  border: none;
  background: none;
  color: #666;
  font-size: 12px;
}

.mobile-tab.active {
  color: #0052d9;
}

.mobile-tab .t-icon {
  font-size: 20px;
}
</style>
