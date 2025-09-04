import { apiClient } from '@/utils/apiClient'

// 聊天会话相关接口
export interface ChatSessionCreateRequest {
  work_id: string
  system_type: 'brain' | 'code' | 'writing'
  title?: string
}

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

export interface ChatMessageResponse {
  id: number
  session_id: string
  role: string
  content: string
  tool_calls?: any
  tool_results?: any
  message_metadata?: any
  created_at: string
  json_blocks?: any[]
  message_type?: 'text' | 'json_card'
}

// 聊天API
export const chatAPI = {
  // 创建聊天会话
  async createChatSession(
    token: string,
    request: ChatSessionCreateRequest,
  ): Promise<ChatSessionResponse> {
    const response = await apiClient.request<ChatSessionResponse>('/api/chat/session/create', {
      method: 'POST',
      body: JSON.stringify(request),
      headers: {
        'Content-Type': 'application/json',
      },
    })
    return response
  },

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

  // 兼容旧的聊天历史接口
  async getChatHistory(
    token: string,
    sessionId: string,
    limit: number = 50,
  ): Promise<ChatMessageResponse[]> {
    // 尝试从sessionId推导workId，或者直接使用新接口
    const response = await apiClient.request<ChatMessageResponse[]>(
      `/api/chat/session/${sessionId}/history?limit=${limit}`,
      {
        method: 'GET',
      },
    )
    return response
  },

  // 简化的获取聊天会话（重构后一个work对应一个session）
  async getChatSessions(token: string, workId?: string): Promise<ChatSessionResponse[]> {
    if (!workId) {
      // 如果没有workId，返回空数组（新架构下需要指定work）
      return []
    }

    try {
      // 尝试获取工作的聊天记录来验证是否有session
      await this.getWorkChatHistory(token, workId)

      // 如果成功，返回一个虚拟的session对象（兼容旧接口）
      return [
        {
          id: 1,
          session_id: `${workId}_main_session`,
          work_id: workId,
          system_type: 'brain',
          title: '主会话',
          status: 'active',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          created_by: 0,
          total_messages: 0,
        },
      ]
    } catch (error) {
      // 如果获取失败，说明还没有session，返回空数组
      return []
    }
  },

  // 更新会话标题
  async updateSessionTitle(
    token: string,
    sessionId: string,
    title: string,
  ): Promise<{ status: string; message: string }> {
    const response = await apiClient.request<{ status: string; message: string }>(
      `/api/chat/session/${sessionId}/title?title=${encodeURIComponent(title)}`,
      {
        method: 'PUT',
      },
    )
    return response
  },

  // 删除聊天会话
  async deleteChatSession(
    token: string,
    sessionId: string,
  ): Promise<{ status: string; message: string }> {
    const response = await apiClient.request<{ status: string; message: string }>(
      `/api/chat/session/${sessionId}`,
      {
        method: 'DELETE',
      },
    )
    return response
  },

  // 重置聊天会话
  async resetChatSession(
    token: string,
    sessionId: string,
  ): Promise<{ status: string; message: string }> {
    const response = await apiClient.request<{ status: string; message: string }>(
      `/api/chat/session/${sessionId}/reset`,
      {
        method: 'POST',
      },
    )
    return response
  },

  // 获取会话上下文信息（主要用于中枢大脑模式）
  async getSessionContext(token: string, sessionId: string): Promise<any> {
    const response = await apiClient.request<any>(`/api/chat/session/${sessionId}/context`, {
      method: 'GET',
    })
    return response
  },

  // 生成工作标题
  async generateTitle(
    token: string,
    workId: string,
    question: string,
  ): Promise<{ title: string; status: string }> {
    const response = await apiClient.request<{ title: string; status: string }>(
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

  // 更新工作标题
  async updateWorkTitle(
    token: string,
    workId: string,
    title: string,
  ): Promise<{ status: string; message: string }> {
    const response = await apiClient.request<{ status: string; message: string }>(
      `/api/chat/work/${workId}/title`,
      {
        method: 'PUT',
        body: JSON.stringify({ title }),
        headers: {
          'Content-Type': 'application/json',
        },
      },
    )
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
  private maxReconnectAttempts = 5
  private reconnectDelay = 1000
  private messageQueue: any[] = []
  private isConnecting = false
  private reconnectTimer: number | null = null
  private heartbeatTimer: number | null = null
  private messageCallback: ((data: any) => void) | null = null
  private disconnectCallback: (() => void) | null = null
  private jsonBlockCallback: ((block: any) => void) | null = null

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

          // 发送认证信息
          try {
            this.ws!.send(JSON.stringify({ token: this.token }))

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
  sendMessage(content: string, model?: string) {
    const message = { problem: content, model }

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

  // 监听消息
  onMessage(callback: (data: any) => void) {
    this.messageCallback = callback

    if (!this.ws) return

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        // 处理心跳响应
        if (data.type === 'pong') {
          return
        }

        if (this.messageCallback) {
          this.messageCallback(data)
        }
      } catch (error) {
        console.error('解析WebSocket消息失败:', error)
      }
    }
  }

  // 监听JSON块消息
  onJsonBlock(callback: (block: any) => void) {
    this.jsonBlockCallback = callback
  }

  // 监听断开连接
  onDisconnect(callback: () => void) {
    this.disconnectCallback = callback
  }

  // 关闭连接
  disconnect() {
    this.clearReconnectTimer()
    this.stopHeartbeat()

    if (this.ws) {
      this.ws.close(1000, '用户主动断开')
      this.ws = null
    }
    this.isConnecting = false
    this.messageQueue = []

    // 调用断开回调
    if (this.disconnectCallback) {
      this.disconnectCallback()
    }
  }

  // 检查连接状态
  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  // 获取连接状态
  getConnectionState(): string {
    if (!this.ws) return 'disconnected'
    switch (this.ws.readyState) {
      case WebSocket.CONNECTING:
        return 'connecting'
      case WebSocket.OPEN:
        return 'connected'
      case WebSocket.CLOSING:
        return 'closing'
      case WebSocket.CLOSED:
        return 'closed'
      default:
        return 'unknown'
    }
  }

  // 强制重连
  async forceReconnect(): Promise<void> {
    console.log('强制重连...')
    this.disconnect()
    this.reconnectAttempts = 0
    await this.connect()
  }
}

// 聊天消息类型
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system'
  content: string
  datetime: string
  avatar: string
  systemType?: 'brain' | 'code' | 'writing'
  isLoading?: boolean
  isStreaming?: boolean
  json_blocks?: any[]
  message_type?: 'text' | 'json_card'
}

// 聊天状态管理
export interface ChatState {
  currentSession: ChatSessionResponse | null
  messages: ChatMessage[]
  isLoading: boolean
  isStreaming: boolean
  error: string | null
}

// 聊天操作类型
export type ChatAction =
  | { type: 'SET_SESSION'; payload: ChatSessionResponse }
  | { type: 'ADD_MESSAGE'; payload: ChatMessage }
  | { type: 'UPDATE_MESSAGE'; payload: { id: string; updates: Partial<ChatMessage> } }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_STREAMING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'CLEAR_MESSAGES' }
  | { type: 'RESET_STATE' }

// 聊天状态管理器
export class ChatStateManager {
  private state: ChatState = {
    currentSession: null,
    messages: [],
    isLoading: false,
    isStreaming: false,
    error: null,
  }

  private listeners: ((state: ChatState) => void)[] = []

  // 获取当前状态
  getState(): ChatState {
    return { ...this.state }
  }

  // 订阅状态变化
  subscribe(listener: (state: ChatState) => void) {
    this.listeners.push(listener)
    return () => {
      const index = this.listeners.indexOf(listener)
      if (index > -1) {
        this.listeners.splice(index, 1)
      }
    }
  }

  // 分发动作
  dispatch(action: ChatAction) {
    switch (action.type) {
      case 'SET_SESSION':
        this.state.currentSession = action.payload
        break
      case 'ADD_MESSAGE':
        this.state.messages.push(action.payload)
        break
      case 'UPDATE_MESSAGE':
        const messageIndex = this.state.messages.findIndex((m) => m.id === action.payload.id)
        if (messageIndex > -1) {
          this.state.messages[messageIndex] = {
            ...this.state.messages[messageIndex],
            ...action.payload.updates,
          }
        }
        break
      case 'SET_LOADING':
        this.state.isLoading = action.payload
        break
      case 'SET_STREAMING':
        this.state.isStreaming = action.payload
        break
      case 'SET_ERROR':
        this.state.error = action.payload
        break
      case 'CLEAR_MESSAGES':
        this.state.messages = []
        break
      case 'RESET_STATE':
        this.state = {
          currentSession: null,
          messages: [],
          isLoading: false,
          isStreaming: false,
          error: null,
        }
        break
    }

    // 通知所有监听器
    this.listeners.forEach((listener) => listener({ ...this.state }))
  }
}
