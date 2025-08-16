import { apiClient } from '@/utils/apiClient';

// 聊天会话相关接口
export interface ChatSessionCreateRequest {
  work_id: string;
  system_type: 'brain' | 'code' | 'writing';
  title?: string;
}

export interface ChatSessionResponse {
  id: number;
  session_id: string;
  work_id: string;
  system_type: string;
  title?: string;
  status: string;
  created_at: string;
  updated_at: string;
  created_by: number;
  total_messages: number;
}

export interface ChatMessageResponse {
  id: number;
  session_id: string;
  role: string;
  content: string;
  tool_calls?: any;
  tool_results?: any;
  message_metadata?: any;
  created_at: string;
}

export interface ChatStreamRequest {
  problem: string;
  model?: string;
}

// 聊天API
export const chatAPI = {
  // 创建聊天会话
  async createChatSession(
    token: string, 
    request: ChatSessionCreateRequest
  ): Promise<ChatSessionResponse> {
    const response = await apiClient.request<ChatSessionResponse>('/api/chat/session/create', {
      method: 'POST',
      body: JSON.stringify(request),
      headers: { 
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
    });
    return response;
  },

  // 流式聊天（返回ReadableStream）
  async chatStream(
    token: string,
    sessionId: string,
    request: ChatStreamRequest
  ): Promise<ReadableStream<Uint8Array>> {
    // 获取API基础URL，如果没有配置则使用默认值
    const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
    
    const response = await fetch(`${baseUrl}/api/chat/session/${sessionId}/stream`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(request)
    });

    if (!response.ok) {
      throw new Error(`聊天流式接口失败: ${response.status}`);
    }

    return response.body!;
  },

  // 获取聊天历史
  async getChatHistory(
    token: string,
    sessionId: string,
    limit: number = 50
  ): Promise<ChatMessageResponse[]> {
    const params = new URLSearchParams();
    params.append('limit', limit.toString());

    const response = await apiClient.request<ChatMessageResponse[]>(
      `/api/chat/session/${sessionId}/history?${params.toString()}`,
      {
        method: 'GET',
        headers: { Authorization: `Bearer ${token}` }
      }
    );
    return response;
  },

  // 获取聊天会话列表
  async getChatSessions(
    token: string,
    workId?: string
  ): Promise<ChatSessionResponse[]> {
    const params = workId ? `?work_id=${encodeURIComponent(workId)}` : '';
    
    const response = await apiClient.request<ChatSessionResponse[]>(
      `/api/chat/sessions${params}`,
      {
        method: 'GET',
        headers: { Authorization: `Bearer ${token}` }
      }
    );
    return response;
  },

  // 更新会话标题
  async updateSessionTitle(
    token: string,
    sessionId: string,
    title: string
  ): Promise<{ status: string; message: string }> {
    const response = await apiClient.request<{ status: string; message: string }>(
      `/api/chat/session/${sessionId}/title?title=${encodeURIComponent(title)}`,
      {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}` }
      }
    );
    return response;
  },

  // 删除聊天会话
  async deleteChatSession(
    token: string,
    sessionId: string
  ): Promise<{ status: string; message: string }> {
    const response = await apiClient.request<{ status: string; message: string }>(
      `/api/chat/session/${sessionId}`,
      {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` }
      }
    );
    return response;
  },

  // 重置聊天会话
  async resetChatSession(
    token: string,
    sessionId: string
  ): Promise<{ status: string; message: string }> {
    const response = await apiClient.request<{ status: string; message: string }>(
      `/api/chat/session/${sessionId}/reset`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      }
    );
    return response;
  }
};

// 流式聊天工具函数
export class ChatStreamHandler {
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private decoder = new TextDecoder();
  private buffer = '';

  constructor(private stream: ReadableStream<Uint8Array>) {}

  // 开始读取流
  async startReading(
    onMessage: (content: string) => void,
    onComplete: () => void,
    onError: (error: string) => void
  ) {
    try {
      this.reader = this.stream.getReader();
      
      while (true) {
        const { done, value } = await this.reader.read();
        
        if (done) {
          onComplete();
          break;
        }

        // 解码并直接处理数据（后端返回plain text）
        const chunk = this.decoder.decode(value, { stream: true });
        if (chunk.trim()) {
          onMessage(chunk);
        }
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : '未知错误');
    } finally {
      if (this.reader) {
        this.reader.releaseLock();
      }
    }
  }

  // 停止读取
  stop() {
    if (this.reader) {
      this.reader.cancel();
      this.reader.releaseLock();
    }
  }
}

// WebSocket聊天处理器
export class WebSocketChatHandler {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private token: string;
  private baseUrl: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 3;
  private reconnectDelay = 1000;
  private messageQueue: any[] = [];
  private isConnecting = false;

  constructor(sessionId: string, token: string) {
    this.sessionId = sessionId;
    this.token = token;
    this.baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
  }

  // 连接WebSocket
  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.isConnecting) {
        reject(new Error('连接已在进行中'));
        return;
      }

      this.isConnecting = true;
      
      try {
        // 构建WebSocket URL
        const wsUrl = `${this.baseUrl.replace('http', 'ws')}/api/chat/ws/${this.sessionId}`;
        console.log('连接WebSocket:', wsUrl);
        
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = async () => {
          console.log('WebSocket连接已建立');
          this.isConnecting = false;
          this.reconnectAttempts = 0;
          
          // 发送认证信息
          try {
            this.ws!.send(JSON.stringify({ token: this.token }));
            
            // 发送队列中的消息
            while (this.messageQueue.length > 0) {
              const message = this.messageQueue.shift();
              this.ws!.send(JSON.stringify(message));
            }
            
            resolve();
          } catch (error) {
            reject(error);
          }
        };

        this.ws.onerror = (error) => {
          console.error('WebSocket连接错误:', error);
          this.isConnecting = false;
          reject(new Error('WebSocket连接失败'));
        };

        this.ws.onclose = (event) => {
          console.log('WebSocket连接已关闭:', event.code, event.reason);
          this.isConnecting = false;
          
          // 如果不是正常关闭，尝试重连
          if (event.code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.attemptReconnect();
          }
        };

        // 设置连接超时
        setTimeout(() => {
          if (this.isConnecting) {
            this.isConnecting = false;
            if (this.ws) {
              this.ws.close();
            }
            reject(new Error('WebSocket连接超时'));
          }
        }, 10000);

      } catch (error) {
        this.isConnecting = false;
        reject(error);
      }
    });
  }

  // 尝试重连
  private attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('达到最大重连次数，停止重连');
      return;
    }

    this.reconnectAttempts++;
    console.log(`尝试重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
    
    setTimeout(() => {
      this.connect().catch(error => {
        console.error('重连失败:', error);
      });
    }, this.reconnectDelay * this.reconnectAttempts);
  }

  // 发送消息
  sendMessage(problem: string, model?: string) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      // 如果连接未建立，将消息加入队列
      const message = { problem, model };
      this.messageQueue.push(message);
      console.log('WebSocket未连接，消息已加入队列');
      return;
    }

    const message = {
      problem,
      model
    };

    this.ws.send(JSON.stringify(message));
  }

  // 监听消息
  onMessage(callback: (data: any) => void) {
    if (!this.ws) return;

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        callback(data);
      } catch (error) {
        console.error('解析WebSocket消息失败:', error);
      }
    };
  }

  // 关闭连接
  disconnect() {
    if (this.ws) {
      this.ws.close(1000, '用户主动断开');
      this.ws = null;
    }
    this.isConnecting = false;
    this.messageQueue = [];
  }

  // 检查连接状态
  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  // 获取连接状态
  getConnectionState(): string {
    if (!this.ws) return 'disconnected';
    switch (this.ws.readyState) {
      case WebSocket.CONNECTING: return 'connecting';
      case WebSocket.OPEN: return 'connected';
      case WebSocket.CLOSING: return 'closing';
      case WebSocket.CLOSED: return 'closed';
      default: return 'unknown';
    }
  }
}

// 聊天消息类型
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system';
  content: string;
  datetime: string;
  avatar: string;
  systemType?: 'brain' | 'code' | 'writing';
  isLoading?: boolean;
  isStreaming?: boolean;
}

// 聊天状态管理
export interface ChatState {
  currentSession: ChatSessionResponse | null;
  messages: ChatMessage[];
  isLoading: boolean;
  isStreaming: boolean;
  error: string | null;
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
  | { type: 'RESET_STATE' };

// 聊天状态管理器
export class ChatStateManager {
  private state: ChatState = {
    currentSession: null,
    messages: [],
    isLoading: false,
    isStreaming: false,
    error: null
  };

  private listeners: ((state: ChatState) => void)[] = [];

  // 获取当前状态
  getState(): ChatState {
    return { ...this.state };
  }

  // 订阅状态变化
  subscribe(listener: (state: ChatState) => void) {
    this.listeners.push(listener);
    return () => {
      const index = this.listeners.indexOf(listener);
      if (index > -1) {
        this.listeners.splice(index, 1);
      }
    };
  }

  // 分发动作
  dispatch(action: ChatAction) {
    switch (action.type) {
      case 'SET_SESSION':
        this.state.currentSession = action.payload;
        break;
      case 'ADD_MESSAGE':
        this.state.messages.push(action.payload);
        break;
      case 'UPDATE_MESSAGE':
        const messageIndex = this.state.messages.findIndex(m => m.id === action.payload.id);
        if (messageIndex > -1) {
          this.state.messages[messageIndex] = { 
            ...this.state.messages[messageIndex], 
            ...action.payload.updates 
          };
        }
        break;
      case 'SET_LOADING':
        this.state.isLoading = action.payload;
        break;
      case 'SET_STREAMING':
        this.state.isStreaming = action.payload;
        break;
      case 'SET_ERROR':
        this.state.error = action.payload;
        break;
      case 'CLEAR_MESSAGES':
        this.state.messages = [];
        break;
      case 'RESET_STATE':
        this.state = {
          currentSession: null,
          messages: [],
          isLoading: false,
          isStreaming: false,
          error: null
        };
        break;
    }

    // 通知所有监听器
    this.listeners.forEach(listener => listener({ ...this.state }));
  }
}
