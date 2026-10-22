import { createWorkspaceRefresh } from '@/composables/workspaceRefresh'
import { ref, shallowRef, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { useAuthStore } from '@/stores/auth'
import { workspaceAPI } from '@/api/workspace'
import { chatAPI, WebSocketChatHandler, type ChatMessage } from '@/api/chat'
import type { ResearchAction, ResearchSnapshot } from '@/types/research'
import { applyReplyUpdate } from '@/utils/conversationTimeline'
import { researchHeadline } from '@/utils/researchProgress'
import { useStudioCopy } from '@/lang/studio'
import type { useWorkSession } from '@/composables/useWorkSession'
import type { useWorkLayout } from '@/composables/useWorkLayout'

export function useWorkStream(options: {
  session: Pick<
    ReturnType<typeof useWorkSession>,
    | 'workId'
    | 'currentWork'
    | 'researchSnapshot'
    | 'applyWorkStatus'
    | 'applyResearchSnapshot'
    | 'loadResearchSnapshot'
    | 'invalidateSnapshot'
    | 'isDisposed'
  >
  layout: Pick<
    ReturnType<typeof useWorkLayout>,
    | 'inputValue'
    | 'atBottom'
    | 'panelSession'
    | 'restorePanelScroll'
    | 'scrollToBottom'
    | 'activateChat'
  >
  refreshFiles: () => Promise<void>
}) {
  const {
    workId,
    currentWork,
    researchSnapshot,
    applyWorkStatus,
    applyResearchSnapshot,
    loadResearchSnapshot,
    invalidateSnapshot,
    isDisposed,
  } = options.session
  const { inputValue, atBottom, panelSession, restorePanelScroll, scrollToBottom, activateChat } =
    options.layout
  const refresh = createWorkspaceRefresh({
    research: loadResearchSnapshot,
    files: options.refreshFiles,
    history: () => readChatHistory(true),
    work: readCurrentWork,
  }, isDisposed)
  const refreshWorkspace = () => refresh.request(['research', 'files', 'history'])
  const loadChatHistory = (throwOnError = false) => refresh.request(['history']).catch((error) => {
    if (throwOnError) throw error
  })
  const refreshCurrentWork = () => refresh.request(['work'])
  const authStore = useAuthStore()
  const { ui, isZh } = useStudioCopy()
  // 定义聊天消息类型（使用API中的类型）
  interface ChatMessageDisplay extends ChatMessage {}

  // 聊天消息数据
  const chatMessages = ref<ChatMessageDisplay[]>([])

  // 聊天相关状态
  const isStreaming = ref(false)
  const runningAction = ref<ResearchAction>('auto')
  const questionScope = ref<string | null>(null)
  let scopedVersion: { research_id: string; base_revision: number } | undefined
  function askSubproblem(id: string) {
    questionScope.value = id
    const snapshot = researchSnapshot.value
    scopedVersion =
      snapshot?.research_id && snapshot.research_revision
        ? { research_id: snapshot.research_id, base_revision: snapshot.research_revision }
        : undefined
    activateChat()
    nextTick(() =>
      document.querySelector<HTMLTextAreaElement>('.work-message-input textarea')?.focus(),
    )
  }
  const webSocketHandler = shallowRef<WebSocketChatHandler | null>(null)
  const pausing = ref(false)
  const connectionState = ref<'idle' | 'live' | 'reconnecting' | 'lost'>('idle')
  const taskStartedAt = ref<number | null>(null),
    clockNow = ref(Date.now())
  let elapsedTimer: ReturnType<typeof setInterval> | undefined
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
    () =>
      needsRecovery.value && !!researchSnapshot.value?.research_id && !recoveryUnavailable.value,
  )
  const recoveryHint = computed(() =>
    recoveryUnavailable.value
      ? ui.value.restartHint
      : currentWork.value?.status === 'failed'
        ? ui.value.failedHint
        : ui.value.pausedHint,
  )
  function prepareNewResearch() {
    questionScope.value = null
    activateChat()
    if (inputValue.value.trim()) void sendMessage(inputValue.value, 'run')
    else
      MessagePlugin.info(
        isZh.value
          ? '请先输入新研究目标，再选择重新研究。'
          : 'Enter a new research goal, then choose Run research again.',
      )
  }
  const stageText = computed(() =>
    isStreaming.value && runningAction.value === 'ask'
      ? isZh.value
        ? '正在分析已有结果'
        : 'Reviewing existing results'
      : researchHeadline(
          researchSnapshot.value,
          currentWork.value?.status || 'created',
          isZh.value,
        ),
  )
  const elapsedText = computed(() => {
    if (!isStreaming.value || !taskStartedAt.value) return ''
    const seconds = Math.max(0, Math.floor((clockNow.value - taskStartedAt.value) / 1000))
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
  })
  async function pauseTask() {
    if (isDisposed() || pausing.value || !authStore.token) return
    pausing.value = true
    try {
      const result = await chatAPI.pauseWork(workId.value)
      if (isDisposed()) return
      applyWorkStatus(result.work_status)
      isStreaming.value = false
      await loadChatHistory()
      await refresh.request(['files', 'research'])
    } catch {
      MessagePlugin.error(isZh.value ? '暂停失败，请重试' : 'Could not pause. Please retry.')
    } finally {
      pausing.value = false
    }
  }
  async function reconnectTask() {
    if (isDisposed()) return
    connectionState.value = 'reconnecting'
    isStreaming.value = false
    await checkAndResumeRunningTask()
    if (isDisposed()) return
    if (!isStreaming.value) {
      await loadChatHistory()
      if (authStore.token) await refreshCurrentWork()
      connectionState.value = 'idle'
    }
    await refresh.request(['research'])
  }
  // 当前恢复任务的消息ID
  const reconnectMessageId = ref<string | null>(null)

  // 检查并恢复正在进行的AI任务
  const checkAndResumeRunningTask = async () => {
    if (isDisposed() || !authStore.token || !workId.value) return

    try {
      // 查询任务状态
      const taskStatus = await chatAPI.getTaskStatus(authStore.token, workId.value)
      if (isDisposed()) return

      if (taskStatus.has_task && taskStatus.status === 'running') {
        taskStartedAt.value = taskStatus.started_at ? taskStatus.started_at * 1000 : null

        // 设置流式状态
        isStreaming.value = true
        runningAction.value = taskStatus.action || 'run'

        // Bind the durable reply; replay revisions safely ignore already-loaded deltas.
        const aiMessageId = taskStatus.reply_id || `reconnect_${Date.now()}`
        reconnectMessageId.value = aiMessageId
        const existing = chatMessages.value.find((m) => m.id === aiMessageId)
        if (existing) existing.isStreaming = true
        else
          chatMessages.value.push({
            id: aiMessageId,
            role: 'assistant',
            content: '',
            datetime: taskStatus.started_at
              ? new Date(taskStatus.started_at * 1000).toLocaleString()
              : '',
            avatar: ASSISTANT_AVATAR,
            isStreaming: true,
            segments: [],
            message_type: 'timeline',
          })

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
      if (!isDisposed() && webSocketHandler.value === handler) void handleConnectionLost()
    })
    handler.onReconnect((data) => {
      if (!isDisposed() && webSocketHandler.value === handler)
        connectionState.value = data.type === 'reconnect_complete' ? 'live' : 'reconnecting'
    })
    handler.onMessage((data) => {
      if (isDisposed() || webSocketHandler.value !== handler) return
      if (data.type === 'start' || data.type === 'reconnect') {
        if (data.reply_id) {
          const message = chatMessages.value.find((m) => m.id === messageId)
          const durable = chatMessages.value.find((m) => m.id === data.reply_id)
          if (message && !durable) message.id = data.reply_id
          else if (message && durable && message !== durable)
            chatMessages.value = chatMessages.value.filter((m) => m !== message)
          messageId = data.reply_id
          reconnectMessageId.value = messageId
        }
      }
      if (data.type === 'start' || data.type === 'auth_success') {
        applyWorkStatus(data.work_status)
        if (data.action) runningAction.value = data.action
        if (data.type === 'start') {
          invalidateSnapshot()
          if (data.action === 'run') researchSnapshot.value = null
        }
      } else handleStreamMessage(data, messageId)
    })
    await handler.connect()
    if (isDisposed() || webSocketHandler.value !== handler) {
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
      if (isDisposed()) return
      console.error('恢复WebSocket连接失败:', error)
      isStreaming.value = false
      reconnectMessageId.value = null
      MessagePlugin.error('恢复AI任务失败')
    }
  }

  // 连接断开且自动重连用尽：问后端任务还在不在。任务已结束就以服务器保存的记录为准，不让界面一直停在"生成中"
  const handleConnectionLost = async () => {
    if (isDisposed()) return
    connectionState.value = 'lost'
    if (!isStreaming.value || !authStore.token || !workId.value) return
    try {
      const taskStatus = await chatAPI.getTaskStatus(authStore.token, workId.value)
      if (isDisposed()) return
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
    await refreshCurrentWork()
    await refresh.request(['files', 'research'])
  }

  const handleStreamMessage = (data: any, messageId: string) => {
    if (isDisposed()) return
    applyWorkStatus(data.work_status)
    // Workspace notifications do not depend on a chat placeholder surviving a history reload.
    if (data.type === 'json_block') {
      const block = data.block
      if (block?.type === 'research_state' || block?.type === 'research_activity') {
        scheduleResearchRefresh()
        return
      }
      if (block?.type === 'file_changed') {
        scheduleFileRefresh()
        return
      }
      if (block?.type === 'research_snapshot') {
        if (block.content?.schema_version === 5 && block.content.work_id === workId.value) {
          invalidateSnapshot()
          applyResearchSnapshot(block.content as ResearchSnapshot)
        }
        return
      }
    }
    const messageIndex = chatMessages.value.findIndex(
      (m) => m.id === (data.update?.reply_id || data.reply_id || messageId),
    )
    if (messageIndex === -1) return

    const currentMessage = chatMessages.value[messageIndex]
    if (!currentMessage) return

    if (data.type === 'reply_update') {
      chatMessages.value[messageIndex] = {
        ...applyReplyUpdate(currentMessage, data.update),
        message_type: 'timeline',
        isStreaming: data.update.status === 'running',
      }
      scrollToBottom()
      return
    }

    switch (data.type) {
      case 'content':
        chatMessages.value[messageIndex] = {
          ...currentMessage,
          content: currentMessage.content + data.content,
        }
        break

      case 'json_block':
        const block = data.block

        if (block?.type === 'plan_updated') {
          // Plans are projected from the research snapshot, never phase events.
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
        void loadChatHistory()
        connectionState.value = 'idle'
        // Refresh the authoritative evidence after research and after reconnect.
        refresh.schedule(['research', 'files'], 0)
        break

      case 'error':
        chatMessages.value[messageIndex] = {
          ...currentMessage,
          content: currentMessage.content || `错误: ${data.message}`,
          isStreaming: false,
        }
        isStreaming.value = false
        reconnectMessageId.value = null
        void loadChatHistory()
        refresh.schedule(['research'], 0)
        break
    }

    // 自动滚动
    scrollToBottom()
  }

  let historyGeneration = 0
  let paperHistoryTimer: ReturnType<typeof setTimeout> | undefined
  function schedulePaperHistory() {
    if (!chatMessages.value.some((m) => m.paper_execution && m.isStreaming) || isDisposed()) return
    if (paperHistoryTimer) clearTimeout(paperHistoryTimer)
    paperHistoryTimer = setTimeout(() => {
      if (isStreaming.value) schedulePaperHistory()
      else void loadChatHistory()
    }, 1500)
  }
  // Read one display history; comment messages are projected by the server.
  async function readChatHistory(throwOnError = false) {
    if (isDisposed() || !authStore.token || !workId.value) return

    const generation = ++historyGeneration
    const requestedWork = workId.value
    if (paperHistoryTimer) clearTimeout(paperHistoryTimer)
    try {
      // 使用新的work聊天历史API
      const historyData = await chatAPI.getWorkChatHistory(authStore.token, workId.value)

      if (isDisposed() || requestedWork !== workId.value || generation !== historyGeneration) return
      const hadPendingPaper = chatMessages.value.some((m) => m.paper_execution && m.isStreaming)
      // 转换消息格式
      const live = new Map(
        chatMessages.value
          .filter((m) => m.isStreaming)
          .map((m) => [m.id, m]),
      )
      chatMessages.value = historyData.messages.map((msg, index) => {
        const newer = live.get(msg.id)
        if (newer && (newer.metadata?.revision || 0) > (msg.metadata?.revision || 0)) return newer
        return {
          id: msg.id || `msg_${index}`,
          role: msg.role as 'user' | 'assistant' | 'error' | 'model-change' | 'system',
          content: msg.content,
          datetime: msg.datetime || new Date(msg.timestamp).toLocaleString(),
          avatar: getAvatarByRole(msg.role),
          json_blocks: msg.json_blocks || [],
          segments: msg.segments,
          metadata: msg.metadata,
          message_type: msg.message_type || 'text',
          paper_execution: msg.paper_execution,
          isStreaming:
            msg.role === 'assistant' &&
            (['pending', 'running'].includes(msg.paper_execution?.state) ||
              msg.metadata?.status === 'running'),
        }
      })

      for (const message of live.values()) {
        if (!chatMessages.value.some((m) => m.id === message.id)) chatMessages.value.push(message)
      }
      schedulePaperHistory()
      if (hadPendingPaper && !chatMessages.value.some((m) => m.paper_execution && m.isStreaming)) {
        await refresh.request(['files', 'research'])
      }
      atBottom.value = panelSession.value.followChat
      await restorePanelScroll()
    } catch (error) {
      console.error('加载聊天历史失败:', error)
      if (isDisposed() || requestedWork !== workId.value || generation !== historyGeneration) return
      schedulePaperHistory()
      if (throwOnError) throw error
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
    action: ResearchAction = 'auto',
    sourceIssue?: { id: string; version: string },
  ) => {
    const content = messageContent || inputValue.value.trim()
    if (!content || isStreaming.value || isDisposed() || !authStore.token) return

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

    activateChat()
    const resolvedAction = action
    const scope = ['auto', 'ask', 'revise_research'].includes(resolvedAction)
      ? questionScope.value || undefined
      : undefined
    questionScope.value = null
    await sendRealMessage(content, resolvedAction, scope, sourceIssue)
  }

  // 发送真实消息（WebSocket）
  const sendRealMessage = async (
    message: string,
    action: ResearchAction,
    subproblemId?: string,
    sourceIssue?: { id: string; version: string },
  ) => {
    if (!authStore.token || isDisposed()) return

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
      await sendMessageViaWebSocket(message, aiMessageId, action, subproblemId, sourceIssue)
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
      if (!isDisposed()) MessagePlugin.error('发送消息失败')
    }
  }

  const sendMessageViaWebSocket = async (
    message: string,
    messageId: string,
    action: ResearchAction,
    subproblemId?: string,
    sourceIssue?: { id: string; version: string },
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
            ...(sourceIssue ? { source_issue: sourceIssue } : {}),
          }
        : undefined
    handler?.sendMessage(
      message,
      undefined,
      action,
      subproblemId,
      revision,
      subproblemId && action === 'auto' ? scopedVersion : undefined,
    )
    scopedVersion = undefined
  }

  function scheduleResearchRefresh() { refresh.schedule(['research']) }
  function scheduleFileRefresh() { refresh.schedule(['files'], 500) }
  function appendAttachment(filename: string, size: number) {
    if (isDisposed()) return
    chatMessages.value.push({
      id: Date.now().toString(),
      role: 'user',
      content: `已上传文件: ${filename} (${(size / 1024).toFixed(2)} KB)`,
      datetime: new Date().toLocaleString(),
      avatar: USER_AVATAR,
    })
    scrollToBottom()
  }
  onMounted(() => {
    elapsedTimer = setInterval(() => {
      clockNow.value = Date.now()
      if (
        clockNow.value % 5000 < 1000 &&
        (isStreaming.value ||
          ['in_progress', 'running', 'processing'].includes(currentWork.value?.status || ''))
      )
        refresh.schedule(['research'])
    }, 1000)
  })
  onUnmounted(() => {
    if (paperHistoryTimer) clearTimeout(paperHistoryTimer)
    if (elapsedTimer) clearInterval(elapsedTimer)
    refresh.dispose()
    webSocketHandler.value?.disconnect()
    webSocketHandler.value = null
    isStreaming.value = false
  })
  watch(
    chatMessages,
    (messages) => {
      if (messages.length) scrollToBottom()
    },
    { deep: true },
  )

  async function readCurrentWork() {
    if (!authStore.token || isDisposed()) return
    const requestedWork = workId.value
    const work = await workspaceAPI.getWork(authStore.token, requestedWork)
    if (!isDisposed() && requestedWork === workId.value) currentWork.value = work
  }

  return {
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
    loadChatHistory,
    refreshWorkspace,
    refreshResources: refresh.request,
    checkAndResumeRunningTask,
    sendMessage,
    handleStreamMessage,
    appendAttachment,
  }
}
