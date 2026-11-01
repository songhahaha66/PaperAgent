import type { ConversationSegment, ExecutionDetail } from '@/utils/conversationTimeline'
import { apiClient } from '@/utils/apiClient'
import type { ResearchAction, ResearchSnapshot, ResearchRevisionCommand } from '@/types/research'
import type { PaperContext } from '@/api/paper'

export interface ChatSessionResponse {
  id: number
  session_id: string
  work_id: string
  system_type: string
  title?: string
  status: string
  created_at: string
  updated_at: string
  created_by: number
  total_messages: number
}

// 聊天API
export const chatAPI = {
  async resolveExecutions(
    workId: string,
    replyId: string,
    segmentIds: string[],
  ): Promise<{ reply_id: string; executions: ExecutionDetail[] }> {
    const batches = Array.from({ length: Math.ceil(segmentIds.length / 100) }, (_, i) =>
      segmentIds.slice(i * 100, (i + 1) * 100),
    )
    const results = await Promise.all(
      batches.map((ids) =>
        apiClient.request<{ reply_id: string; executions: ExecutionDetail[] }>(
          `/api/chat/work/${workId}/execution-references`,
          {
            method: 'POST',
            body: JSON.stringify({ reply_id: replyId, segment_ids: ids }),
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    )
    return { reply_id: replyId, executions: results.flatMap((result) => result.executions) }
  },
  // 获取工作的聊天历史（新API）
  async getWorkChatHistory(
    workId: string,
  ): Promise<{ work_id: string; messages: any[]; context: any }> {
    const response = await apiClient.request<{ work_id: string; messages: any[]; context: any }>(
      `/api/chat/work/${workId}/history`,
      {
        method: 'GET',
      },
    )
    return response
  },

  // 生成工作标题并自动更新到数据库
  async generateTitle(
    workId: string,
    question: string,
  ): Promise<{ title: string; status: string; message: string }> {
    const response = await apiClient.request<{ title: string; status: string; message: string }>(
      `/api/chat/work/${workId}/generate-title`,
      {
        method: 'POST',
        body: JSON.stringify({ question }),
        headers: {
          'Content-Type': 'application/json',
        },
      },
    )
    return response
  },

  async getResearchSnapshot(workId: string): Promise<ResearchSnapshot> {
    return apiClient.request(`/api/chat/work/${workId}/research`, { method: 'GET' })
  },

  async pauseWork(workId: string): Promise<{ work_status: string }> {
    return apiClient.request(`/api/chat/work/${workId}/pause`, { method: 'POST' })
  },

  // 获取AI任务状态（用于断线重连）
  async getTaskStatus(
    workId: string,
  ): Promise<{
    has_task: boolean
    task_id?: string
    reply_id?: string
    status?: string
    message?: string
    started_at?: number
    output_count?: number
    action?: ResearchAction
  }> {
    const response = await apiClient.request<any>(`/api/chat/work/${workId}/task-status`, {
      method: 'GET',
    })
    return response
  },
}

// WebSocket聊天处理器
export class WebSocketChatHandler {
  private ws: WebSocket | null = null
  private baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private messageQueue: any[] = []
  private connecting: Promise<void> | null = null
  private rejectConnect: ((error: Error) => void) | null = null
  private authenticated = false
  private disposed = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private authTimer: ReturnType<typeof setTimeout> | null = null
  private lastReceivedAt = 0
  private messageCallback: ((data: any) => void) | null = null
  private disconnectCallback: (() => void) | null = null
  private reconnectCallback: ((data: any) => void) | null = null
  // Cursor belongs to a task, never to a browser/workspace globally.
  private taskId: string | null = null
  private lastSeq = 0

  constructor(
    private workId: string,
    private token: string,
  ) {}

  connect(): Promise<void> {
    if (this.disposed) return Promise.reject(new Error('连接已关闭'))
    if (this.connecting) return this.connecting
    if (this.authenticated && this.ws?.readyState === WebSocket.OPEN) return Promise.resolve()
    this.clearReconnectTimer()
    const promise = new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(`${this.baseUrl.replace(/^http/, 'ws')}/api/chat/ws/${this.workId}`)
      this.ws = ws
      this.authenticated = false
      this.rejectConnect = reject
      const current = () => !this.disposed && this.ws === ws
      ws.onopen = () => {
        if (!current()) return
        ws.send(JSON.stringify({ token: this.token, task_id: this.taskId, last_seq: this.lastSeq }))
      }
      ws.onmessage = (event) => {
        if (!current()) return
        try {
          const data = JSON.parse(event.data)
          this.lastReceivedAt = Date.now()
          if (data.type === 'pong') return
          if (!this.authenticated && data.type === 'error') {
            reject(new Error(data.message || '认证失败'))
            ws.close(1008, '认证失败')
            return
          }
          if (data.type === 'auth_success') {
            this.authenticated = true
            this.reconnectAttempts = 0
            this.rejectConnect = null
            if (this.authTimer) clearTimeout(this.authTimer)
            this.authTimer = null
            if (!data.task || !['pending', 'running'].includes(data.task.status)) {
              this.taskId = null
              this.lastSeq = 0
            }
            this.startHeartbeat(ws)
            this.messageCallback?.(data)
            // Commands are sent only after permission checks and subscription setup.
            for (const message of this.messageQueue.splice(0)) ws.send(JSON.stringify(message))
            resolve()
            return
          }
          if (
            (data.type === 'start' || data.type === 'reconnect') &&
            data.task_id !== this.taskId
          ) {
            this.taskId = data.task_id
            this.lastSeq = 0
          }
          if (
            data.type === 'reconnect' &&
            Number.isInteger(data.after_seq) &&
            data.after_seq >= 0
          ) {
            this.lastSeq = data.after_seq
          }
          if (data.task_id && this.taskId && data.task_id !== this.taskId) return
          if (typeof data.seq === 'number' && data.seq <= this.lastSeq) return
          if (data.type === 'reconnect' || data.type === 'reconnect_complete')
            this.reconnectCallback?.(data)
          this.messageCallback?.(data)
          if (typeof data.seq === 'number') this.lastSeq = data.seq
        } catch (error) {
          console.error('处理WebSocket消息失败:', error)
        }
      }
      ws.onerror = () => {
        if (!current()) return
        reject(new Error('WebSocket连接失败'))
        ws.close()
      }
      ws.onclose = (event) => {
        if (!current()) return
        this.authenticated = false
        this.stopTimers()
        reject(new Error('WebSocket连接已关闭'))
        this.rejectConnect = null
        if (
          event.code !== 1000 &&
          event.code !== 1008 &&
          this.reconnectAttempts < this.maxReconnectAttempts
        ) {
          this.scheduleReconnect()
        } else this.disconnectCallback?.()
      }
      this.authTimer = setTimeout(() => {
        if (!current() || this.authenticated) return
        reject(new Error('WebSocket认证超时'))
        ws.close(4000, '认证超时')
      }, 10000)
    })
    this.connecting = promise.finally(() => {
      this.connecting = null
    })
    return this.connecting
  }

  private startHeartbeat(ws: WebSocket) {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer)
    this.lastReceivedAt = Date.now()
    this.heartbeatTimer = setInterval(() => {
      if (this.ws !== ws || ws.readyState !== WebSocket.OPEN) return
      if (Date.now() - this.lastReceivedAt > 45000) {
        ws.close(4000, '心跳超时')
        return
      }
      ws.send(JSON.stringify({ type: 'ping' }))
    }, 15000)
  }

  private stopTimers() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer)
    if (this.authTimer) clearTimeout(this.authTimer)
    this.heartbeatTimer = this.authTimer = null
  }

  private scheduleReconnect() {
    if (this.disposed || this.reconnectTimer) return
    this.reconnectCallback?.({ type: 'connection_reconnecting' })
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts++, 10000)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      void this.connect().catch((error) => console.error('重连失败:', error))
    }, delay)
  }

  private clearReconnectTimer() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }

  sendMessage(
    content: string,
    model?: string,
    action: ResearchAction = 'auto',
    subproblemId?: string,
    revision?: ResearchRevisionCommand,
    scopeContext?: { research_id: string; base_revision: number },
    requestId?: string,
    sourceIssue?: { id: string; version: string },
    paperContext?: PaperContext,
  ) {
    if (this.disposed) return
    const message = {
      problem: content,
      model,
      action,
      subproblem_id: subproblemId,
      revision,
      scope_context: scopeContext,
      request_id: requestId,
      source_issue: sourceIssue,
      paper_context: paperContext,
    }
    if (!this.authenticated || this.ws?.readyState !== WebSocket.OPEN) {
      this.messageQueue.push(message)
      void this.connect().catch((error) => console.error('自动连接失败:', error))
      return
    }
    this.ws.send(JSON.stringify(message))
  }

  onMessage(callback: (data: any) => void) {
    this.messageCallback = callback
  }
  onDisconnect(callback: () => void) {
    this.disconnectCallback = callback
  }
  onReconnect(callback: (data: any) => void) {
    this.reconnectCallback = callback
  }

  disconnect() {
    this.disposed = true
    this.clearReconnectTimer()
    this.stopTimers()
    this.rejectConnect?.(new Error('连接已关闭'))
    this.rejectConnect = null
    this.disconnectCallback = this.messageCallback = this.reconnectCallback = null
    if (this.ws) {
      this.ws.onopen = this.ws.onmessage = this.ws.onerror = this.ws.onclose = null
      this.ws.close(1000, '用户主动断开')
      this.ws = null
    }
    this.messageQueue = []
  }
}

// 聊天消息类型
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system'
  content: string
  datetime: string
  avatar: string
  isLoading?: boolean
  isStreaming?: boolean
  paper_execution?: { id: string; research_id: string; state: string }
  json_blocks?: any[]
  message_type?: 'text' | 'json_card' | 'timeline'
  segments?: ConversationSegment[]
  metadata?: { reply_id?: string; chat_task_id?: string; revision?: number; status?: string; paper_context?: PaperContext }
}
