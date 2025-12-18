<template>
  <!-- 手机端提示界面 -->
  <MobileWarning v-if="isMobile" />

  <!-- 正常工作界面 -->
  <div v-else class="work-page">
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
            <p>生成过程中请耐心等待！</p>
          </div>
          <p>创建于 {{ formatDate(currentWork.created_at) }}</p>
        </div>
        <div class="work-actions">
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
      </div>

      <div class="workspace-header" v-else>
        <h1>论文生成工作区</h1>
        <p>正在加载工作信息...</p>
      </div>

      <div class="workspace-content">
        <div class="chat-section">
          <div class="chat-container">
            <div class="chat-messages-container">
              <JsonChatRenderer :messages="chatMessages" />
            </div>
            <div class="chat-bottom-section">
              <FileManager
                :work-id="workId"
                :loading="loading"
                :file-tree-data="workspaceFiles"
                @file-select="handleWorkspaceFileSelect"
                @refresh="handleFileRefresh"
                @main-paper-click="handleMainPaperClick"
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

        <div class="preview-section">
          <!-- 主要论文显示 -->
          <div v-if="showMainPaper && mainPaperContent">
            <t-card title="主要论文">
              <template #actions>
                <t-button size="small" variant="text" @click="showMainPaper = false">
                  <template #icon>
                    <t-icon name="close" />
                  </template>
                </t-button>
              </template>
              <div class="paper-preview">
                <MarkdownRenderer
                  :content="mainPaperContent"
                  :work-id="workId"
                  :base-path="'papers'"
                />
              </div>
            </t-card>
          </div>
          <!-- 普通文件预览 -->
          <div v-else-if="selectedFile">
            <t-card :title="`文件预览: ${selectedFile}`">
              <div class="file-preview">
                <!-- 加载状态 -->
                <div v-if="!currentFileData" class="loading-container">
                </div>
                <!-- 文本文件预览 -->
                <div v-else-if="currentFileData.type === 'text'" class="text-preview">
                  <CodeHighlight v-if="selectedFile.endsWith('.py')" :code="currentFileContent" language="python" />
                  <MarkdownRenderer
                    v-else-if="selectedFile.endsWith('.md')"
                    :content="currentFileContent"
                    :work-id="workId"
                    :base-path="selectedFile.substring(0, selectedFile.lastIndexOf('/'))"
                  />
                  <pre v-else>{{ currentFileContent }}</pre>
                </div>
                <!-- 图片文件预览 -->
                <div v-else-if="currentFileData.type === 'image'" class="image-preview">
                  <img
                    v-if="imageUrls[selectedFile]"
                    :src="imageUrls[selectedFile]"
                    :alt="selectedFile"
                    style="max-width: 100%; height: auto"
                  />
                  <div v-else class="loading-image">正在加载图片...</div>
                </div>
                <!-- 二进制文件信息 -->
                <div v-else-if="currentFileData.type === 'binary'" class="binary-preview">
                  <!-- DOCX文件使用DocxViewer预览 -->
                  <DocxViewer
                    v-if="isDocxFile(selectedFile)"
                    :file-info="currentFileData"
                    :work-id="workId"
                    :token="authStore.token || ''"
                  />
                  <!-- 其他二进制文件使用BinaryFileViewer -->
                  <BinaryFileViewer
                    v-else
                    :file-info="currentFileData"
                    :work-id="workId"
                    :token="authStore.token || ''"
                  />
                </div>
                <!-- 未知文件类型 - 使用 BinaryFileViewer 统一处理 -->
                <div v-else-if="currentFileData" class="binary-preview">
                  <!-- DOCX文件使用DocxViewer预览 -->
                  <DocxViewer
                    v-if="isDocxFile(selectedFile)"
                    :file-info="currentFileData"
                    :work-id="workId"
                    :token="authStore.token || ''"
                  />
                  <!-- 其他文件使用BinaryFileViewer -->
                  <BinaryFileViewer
                    v-else
                    :file-info="currentFileData"
                    :work-id="workId"
                    :token="authStore.token || ''"
                  />
                </div>
                <div v-else class="no-preview">
                  <t-icon name="file" size="48px" />
                  <p>文件信息加载中...</p>
                </div>
              </div>
            </t-card>
          </div>

          <div v-else-if="currentWork">
            <t-card title="工作信息">
              <div class="work-details">
                <p><strong>标题：</strong>{{ currentWork.title }}</p>
                <p><strong>描述：</strong>{{ currentWork.description || '暂无描述' }}</p>
                <p><strong>标签：</strong>{{ currentWork.tags || '无标签' }}</p>
                <p><strong>状态：</strong>{{ getStatusText(currentWork.status) }}</p>
                <p>
                  <strong>输出格式：</strong>
                  <t-tag :theme="getOutputModeTheme(currentWork.output_mode)" variant="light" size="small">
                    <template #icon>
                      <t-icon :name="getOutputModeIcon(currentWork.output_mode)" />
                    </template>
                    {{ getOutputModeText(currentWork.output_mode) }}
                  </t-tag>
                </p>
                <p>
                  <strong>模板：</strong
                  >{{
                    currentWork.template_id ? `模板ID: ${currentWork.template_id}` : '未选择模板'
                  }}
                </p>
              </div>
            </t-card>
          </div>

          <div v-else>
            <t-card title="论文展示区">
              <div class="pdf-info">
                <p>与AI对话生成论文内容后，将在此处预览生成的论文。</p>
                <p>在左侧文件管理器中点击文件可查看具体内容。</p>
              </div>
            </t-card>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { MessagePlugin } from 'tdesign-vue-next';
import { ChatItem, ChatSender } from '@tdesign-vue-next/chat';

import { useAuthStore } from '@/stores/auth';
import { workspaceAPI, workspaceFileAPI, attachmentAPI, type Work, type FileInfo } from '@/api/workspace';
import { chatAPI, WebSocketChatHandler, type ChatMessage, type ChatSessionResponse, type ChatSessionCreateRequest } from '@/api/chat';
import Sidebar from '@/components/Sidebar.vue';
import MobileWarning from '@/components/MobileWarning.vue';
import FileManager from '@/components/FileManager.vue';
import JsonChatRenderer from '@/components/JsonChatRenderer.vue';
import CodeHighlight from '@/components/CodeHighlight.vue';
import MarkdownRenderer from '@/components/MarkdownRenderer.vue';
import BinaryFileViewer from '@/components/BinaryFileViewer.vue';
import DocxViewer from '@/components/DocxViewer.vue';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const workId = computed(() => route.params.work_id as string);

// 侧边栏折叠状态 - 手机端默认收起
const isSidebarCollapsed = ref(window.innerWidth <= 768)

// 检测是否为手机端
const isMobile = ref(window.innerWidth <= 768)

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
const fileRefreshTimer = ref<number | null>(null)
const lastFileUpdateTime = ref<number>(0)

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

    // 初始化聊天会话
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

// 统一的流式消息处理函数
const handleStreamMessage = (data: any, messageId: string) => {
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
      chatMessages.value[messageIndex] = {
        ...currentMessage,
        json_blocks: [...(currentMessage.json_blocks || []), block],
        message_type: 'json_card' as const,
      }
      
      // 检查是否是文件操作相关的JSON块
      if (block?.type && (
        block.type.includes('save_and_execute') ||
        block.type.includes('edit_code_file') ||
        block.type.includes('execute_file') ||
        block.type.includes('code_agent')
      )) {
        // 延迟刷新文件列表
        setTimeout(() => loadWorkspaceFiles(), 1000)
      }
      break

    case 'complete':
      chatMessages.value[messageIndex] = {
        ...currentMessage,
        isStreaming: false,
      }
      isStreaming.value = false
      reconnectMessageId.value = null
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

// 加载工作空间文件
const loadWorkspaceFiles = async () => {
  if (!workId.value || !authStore.token) return

  try {
    // 设置加载状态
    loading.value = true

    const files = await workspaceFileAPI.listFiles(authStore.token, workId.value)
    console.log('Loaded workspace files:', files)
    workspaceFiles.value = files
  } catch (error) {
    console.error('加载工作空间文件失败:', error)
    MessagePlugin.error('加载工作空间文件失败')
    workspaceFiles.value = []
  } finally {
    // 确保加载状态被重置
    loading.value = false
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
  } catch (error) {
    console.error('获取论文内容失败:', error)
    MessagePlugin.error('获取论文内容失败')
  }
}


// 处理文件刷新
const handleFileRefresh = async () => {
  console.log('手动刷新文件列表')
  await loadWorkspaceFiles()
}

  
// 启动文件刷新定时器
const startFileRefreshTimer = () => {
  // 清除之前的定时器
  if (fileRefreshTimer.value) {
    clearTimeout(fileRefreshTimer.value)
  }

  // 设置新的定时器，3秒后刷新文件列表
  fileRefreshTimer.value = setTimeout(() => {
    console.log('自动刷新文件列表')
    fileRefreshTimer.value = null
  }, 3000)
}

// 停止文件刷新定时器
const stopFileRefreshTimer = () => {
  if (fileRefreshTimer.value) {
    clearTimeout(fileRefreshTimer.value)
    fileRefreshTimer.value = null
  }
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
  // 使用新的图片API端点
  return `${import.meta.env.VITE_API_BASE_URL || ''}/api/workspace/${workId.value}/images/${encodeURIComponent(filePath)}`
}

// 获取图片的blob URL（带认证）
const getImageBlobUrl = async (filePath: string): Promise<string> => {
  try {
    const token = authStore.token
    if (!token) {
      throw new Error('未登录')
    }

    const url = `${import.meta.env.VITE_API_BASE_URL || ''}/api/workspace/${workId.value}/images/${encodeURIComponent(filePath)}`

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

      // 停止文件刷新定时器
      stopFileRefreshTimer()

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

      loadWork()
      // 重新初始化聊天会话
      if (authStore.token) {
        initializeChatSession()
      }
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
    loadWork()
    // 初始化聊天会话
    if (authStore.token) {
      initializeChatSession()
    }
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
  // 检查是否有待发送的问题，且当前工作标题为空或空格
  const pendingQuestion = localStorage.getItem('pendingQuestion')
  if (pendingQuestion && currentWork.value?.title?.trim() === '') {
    try {
      // 并行执行：前端模拟发送消息 + 生成标题
      // 不等待完成，让两个操作独立进行
      simulateSendFirstMessage(pendingQuestion) // 立即开始，不等待

      // 在后台异步生成标题，不阻塞主流程
      ;(async () => {
        try {
          await generateWorkTitle(pendingQuestion)
        } catch (error) {
          console.error('后台生成标题失败:', error)
        }
      })()

      // 清除localStorage
      localStorage.removeItem('pendingQuestion')
    } catch (error) {
      console.error('自动发送第一句话失败:', error)
    }
  }
}

// 真正发送第一句话给AI
const simulateSendFirstMessage = (content: string) => {
  // 直接调用 sendMessage，复用统一的消息发送逻辑
  sendMessage(content)
}

// 生成工作标题并自动更新到数据库
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
  overflow: hidden;
}
</style>

<style scoped>
.work-page {
  display: flex;
  height: 100vh;
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
</style>
