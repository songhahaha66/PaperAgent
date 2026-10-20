import { apiClient } from '@/utils/apiClient'
import type { ResearchAction, ResearchSnapshot, ResearchRevisionCommand } from '@/types/research'

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
  // 获取工作的聊天历史（新API）
  async getWorkChatHistory(
    token: string,
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
    token: string,
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
    token: string,
    workId: string,
  ): Promise<{
    has_task: boolean
    task_id?: string
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
  private workId: string // 改为workId
  private token: string
  private baseUrl: string
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private reconnectDelay = 1000
  private messageQueue: any[] = []
  private isConnecting = false
  private reconnectTimer: number | null = null
  private heartbeatTimer: number | null = null
  private messageCallback: ((data: any) => void) | null = null
  private disconnectCallback: (() => void) | null = null
  private reconnectCallback: ((data: any) => void) | null = null
  private isReconnecting = false
  // 当前任务和已收到的最大序号：断线重连时带给后端，只补发断点之后的输出；重复的序号直接丢弃
  private taskId: string | null = null
  private lastSeq = 0

  constructor(workId: string, token: string) {
    // 参数改为workId
    this.workId = workId
    this.token = token
    this.baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
  }

  // 连接WebSocket
  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.isConnecting) {
        reject(new Error('连接已在进行中'))
        return
      }

      this.isConnecting = true
      this.clearReconnectTimer()

      try {
        // 构建WebSocket URL（使用workId）
        const wsUrl = `${this.baseUrl.replace('http', 'ws')}/api/chat/ws/${this.workId}`
        console.log('连接WebSocket:', wsUrl)

        this.ws = new WebSocket(wsUrl)

        this.ws.onopen = async () => {
          console.log('WebSocket连接已建立')
          this.isConnecting = false
          this.reconnectAttempts = 0

          // 绑定消息处理器（如果已设置回调）
          this.bindMessageHandler()

          // 发送认证信息
          try {
            this.ws!.send(
              JSON.stringify({ token: this.token, task_id: this.taskId, last_seq: this.lastSeq }),
            )

            // 发送队列中的消息
            while (this.messageQueue.length > 0) {
              const message = this.messageQueue.shift()
              this.ws!.send(JSON.stringify(message))
            }

            // 启动心跳检测
            this.startHeartbeat()

            resolve()
          } catch (error) {
            reject(error)
          }
        }

        this.ws.onerror = (error) => {
          console.error('WebSocket连接错误:', error)
          this.isConnecting = false
          reject(new Error('WebSocket连接失败'))
        }

        this.ws.onclose = (event) => {
          console.log('WebSocket连接已关闭:', event.code, event.reason)
          this.isConnecting = false
          this.stopHeartbeat()

          // 如果不是正常关闭，尝试重连
          if (event.code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.scheduleReconnect()
          } else {
            // 如果是正常关闭或达到最大重连次数，调用断开回调
            if (this.disconnectCallback) {
              this.disconnectCallback()
            }
          }
        }

        // 设置连接超时
        setTimeout(() => {
          if (this.isConnecting) {
            this.isConnecting = false
            if (this.ws) {
              this.ws.close()
            }
            reject(new Error('WebSocket连接超时'))
          }
        }, 10000)
      } catch (error) {
        this.isConnecting = false
        reject(error)
      }
    })
  }

  // 启动心跳检测
  private startHeartbeat() {
    this.heartbeatTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify({ type: 'ping' }))
        } catch (error) {
          console.error('心跳发送失败:', error)
          this.scheduleReconnect()
        }
      }
    }, 30000) // 30秒发送一次心跳
  }

  // 停止心跳检测
  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  // 安排重连
  private scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('达到最大重连次数，停止重连')
      return
    }

    this.reconnectAttempts++
    const delay = Math.min(this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), 10000)
    console.log(
      `安排重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})，延迟: ${delay}ms`,
    )

    this.reconnectTimer = setTimeout(() => {
      this.connect().catch((error) => {
        console.error('重连失败:', error)
      })
    }, delay)
  }

  // 清除重连定时器
  private clearReconnectTimer() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  // 发送消息
  sendMessage(
    content: string,
    model?: string,
    action: ResearchAction = 'auto',
    subproblemId?: string,
    revision?: ResearchRevisionCommand,
    scopeContext?: { research_id: string; base_revision: number },
  ) {
    const message = {
      problem: content,
      model,
      action,
      subproblem_id: subproblemId,
      revision,
      scope_context: scopeContext,
    }

    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      // 如果连接未建立，将消息加入队列
      this.messageQueue.push(message)
      console.log('WebSocket未连接，消息已加入队列')

      // 尝试连接
      if (!this.isConnecting) {
        this.connect().catch((error) => {
          console.error('自动连接失败:', error)
        })
      }
      return
    }

    try {
      this.ws.send(JSON.stringify(message))
    } catch (error) {
      console.error('发送消息失败:', error)
      // 发送失败，加入队列并尝试重连
      this.messageQueue.push(message)
      this.scheduleReconnect()
    }
  }

  // 绑定消息处理器到当前 WebSocket 实例
  private bindMessageHandler() {
    if (!this.ws) return

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        // 处理心跳响应
        if (data.type === 'pong') {
          return
        }

        // 新任务开始或恢复的是另一个任务时，序号从头算
        if (
          (data.type === 'start' || data.type === 'reconnect') &&
          data.task_id &&
          data.task_id !== this.taskId
        ) {
          this.taskId = data.task_id
          this.lastSeq = 0
        }
        if (typeof data.seq === 'number') {
          if (data.seq <= this.lastSeq) return
          this.lastSeq = data.seq
        }

        // 处理重连相关消息
        if (data.type === 'reconnect') {
          console.log('检测到正在进行的AI任务，开始恢复...')
          this.isReconnecting = true
          if (this.reconnectCallback) {
            this.reconnectCallback(data)
          }
          // 不 return，继续传递给 messageCallback
        }

        if (data.type === 'reconnect_complete') {
          console.log('历史输出恢复完成')
          this.isReconnecting = false
          if (this.reconnectCallback) {
            this.reconnectCallback(data)
          }
          // 不 return，继续传递给 messageCallback
        }

        if (this.messageCallback) {
          this.messageCallback(data)
        }
      } catch (error) {
        console.error('解析WebSocket消息失败:', error)
      }
    }
  }

  // 监听消息
  onMessage(callback: (data: any) => void) {
    this.messageCallback = callback
    // 如果 ws 已存在，立即绑定
    this.bindMessageHandler()
  }

  // 监听断开连接
  onDisconnect(callback: () => void) {
    this.disconnectCallback = callback
  }

  // 监听重连恢复事件
  onReconnect(callback: (data: any) => void) {
    this.reconnectCallback = callback
  }

  // 关闭连接
  disconnect() {
    this.clearReconnectTimer()
    this.stopHeartbeat()

    // 主动断开（换新连接、离开页面）不是连接丢失，不触发断开回调，也不再接收旧连接的消息
    this.disconnectCallback = null
    this.messageCallback = null
    if (this.ws) {
      this.ws.onmessage = null
      this.ws.close(1000, '用户主动断开')
      this.ws = null
    }
    this.isConnecting = false
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
  message_type?: 'text' | 'json_card'
}
