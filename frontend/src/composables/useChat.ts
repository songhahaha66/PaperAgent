import { ref, computed, onUnmounted } from 'vue';
import { MessagePlugin } from 'tdesign-vue-next';
import { useAuthStore } from '@/stores/auth';
import { 
  chatAPI, 
  ChatStateManager,
  WebSocketChatHandler,
  type ChatSessionCreateRequest,
  type ChatMessage,
  type ChatSessionResponse
} from '@/api/chat';

export function useChat() {
  const authStore = useAuthStore();
  const chatStateManager = new ChatStateManager();
  
  // 响应式状态
  const currentSession = ref<ChatSessionResponse | null>(null);
  const messages = ref<ChatMessage[]>([]);
  const isLoading = ref(false);
  const isStreaming = ref(false);
  const error = ref<string | null>(null);
  
  // WebSocket处理器
  let currentWebSocketHandler: WebSocketChatHandler | null = null;

  // 订阅状态变化
  const unsubscribe = chatStateManager.subscribe((state) => {
    currentSession.value = state.currentSession;
    messages.value = state.messages;
    isLoading.value = state.isLoading;
    isStreaming.value = state.isStreaming;
    error.value = state.error;
  });

  // 创建聊天会话
  const createSession = async (request: ChatSessionCreateRequest): Promise<ChatSessionResponse | null> => {
    if (!authStore.token) {
      MessagePlugin.error('请先登录');
      return null;
    }

    try {
      chatStateManager.dispatch({ type: 'SET_LOADING', payload: true });
      chatStateManager.dispatch({ type: 'SET_ERROR', payload: null });

      const session = await chatAPI.createChatSession(authStore.token, request);
      chatStateManager.dispatch({ type: 'SET_SESSION', payload: session });
      
      // 加载聊天历史
      await loadChatHistory(session.session_id);
      
      MessagePlugin.success('聊天会话创建成功');
      return session;
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '创建会话失败';
      chatStateManager.dispatch({ type: 'SET_ERROR', payload: errorMsg });
      MessagePlugin.error(errorMsg);
      return null;
    } finally {
      chatStateManager.dispatch({ type: 'SET_LOADING', payload: false });
    }
  };

  // 加载聊天历史
  const loadChatHistory = async (sessionId: string, limit: number = 50) => {
    if (!authStore.token) return;

    try {
      const history = await chatAPI.getChatHistory(authStore.token, sessionId, limit);
      
      // 转换历史消息格式
      const chatMessages: ChatMessage[] = history.map(msg => ({
        id: msg.id.toString(),
        role: msg.role as 'user' | 'assistant' | 'system',
        content: msg.content,
        datetime: new Date(msg.created_at).toLocaleString(),
        avatar: getAvatarForRole(msg.role),
        systemType: getSystemTypeFromSession(currentSession.value?.system_type)
      }));

      // 清空现有消息并添加历史消息
      chatStateManager.dispatch({ type: 'CLEAR_MESSAGES' });
      chatMessages.forEach(msg => {
        chatStateManager.dispatch({ type: 'ADD_MESSAGE', payload: msg });
      });
    } catch (err) {
      console.error('加载聊天历史失败:', err);
    }
  };

  // 发送消息并获取流式响应
  const sendMessage = async (content: string, model?: string) => {
    if (!currentSession.value || !authStore.token) {
      MessagePlugin.error('请先创建聊天会话');
      return;
    }

    if (!content.trim()) return;

    // 添加用户消息
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: content.trim(),
      datetime: new Date().toLocaleString(),
      avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
    };

    chatStateManager.dispatch({ type: 'ADD_MESSAGE', payload: userMessage });

    // 添加AI回复占位消息
    const aiMessageId = (Date.now() + 1).toString();
    const aiMessage: ChatMessage = {
      id: aiMessageId,
      role: 'assistant',
      content: '',
      datetime: new Date().toLocaleString(),
      avatar: getAvatarForRole('assistant'),
      systemType: getSystemTypeFromSession(currentSession.value.system_type),
      isStreaming: true
    };

    chatStateManager.dispatch({ type: 'ADD_MESSAGE', payload: aiMessage });

    try {
      // 开始流式聊天
      chatStateManager.dispatch({ type: 'SET_STREAMING', payload: true });
      chatStateManager.dispatch({ type: 'SET_ERROR', payload: null });

      // 使用WebSocket
      await sendMessageViaWebSocket(content, aiMessageId, model);

    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '发送消息失败';
      chatStateManager.dispatch({
        type: 'UPDATE_MESSAGE',
        payload: {
          id: aiMessageId,
          updates: {
            content: `错误: ${errorMsg}`,
            isStreaming: false
          }
        }
      });
      chatStateManager.dispatch({ type: 'SET_STREAMING', payload: false });
      chatStateManager.dispatch({ type: 'SET_ERROR', payload: errorMsg });
      MessagePlugin.error(errorMsg);
    }
  };

  // WebSocket方式发送消息
  const sendMessageViaWebSocket = async (content: string, aiMessageId: string, model?: string) => {
    try {
      // 创建WebSocket处理器
      currentWebSocketHandler = new WebSocketChatHandler(
        currentSession.value!.session_id,
        authStore.token!
      );

      // 连接WebSocket
      await currentWebSocketHandler.connect();

      let fullContent = '';

      // 设置消息监听器
      currentWebSocketHandler.onMessage((data) => {
        switch (data.type) {
          case 'start':
            // 开始消息，可以显示加载状态
            break;
          case 'content':
            // 内容更新
            fullContent += data.content;
            chatStateManager.dispatch({
              type: 'UPDATE_MESSAGE',
              payload: {
                id: aiMessageId,
                updates: {
                  content: fullContent,
                  isStreaming: true
                }
              }
            });
            break;
          case 'xml_open':
          case 'xml_close':
            // XML标签，可以用于格式化显示
            break;
          case 'complete':
            // 完成消息
            chatStateManager.dispatch({
              type: 'UPDATE_MESSAGE',
              payload: {
                id: aiMessageId,
                updates: {
                  isStreaming: false
                }
              }
            });
            chatStateManager.dispatch({ type: 'SET_STREAMING', payload: false });
            currentWebSocketHandler = null;
            break;
          case 'error':
            // 错误消息
            chatStateManager.dispatch({
              type: 'UPDATE_MESSAGE',
              payload: {
                id: aiMessageId,
                updates: {
                  content: `错误: ${data.message}`,
                  isStreaming: false
                }
              }
            });
            chatStateManager.dispatch({ type: 'SET_STREAMING', payload: false });
            chatStateManager.dispatch({ type: 'SET_ERROR', payload: data.message });
            currentWebSocketHandler = null;
            MessagePlugin.error(`聊天失败: ${data.message}`);
            break;
        }
      });

      // 发送消息
      currentWebSocketHandler.sendMessage(content, model);

    } catch (err) {
      throw err;
    }
  };



  // 停止WebSocket传输
  const stopStreaming = () => {
    if (currentWebSocketHandler) {
      currentWebSocketHandler.disconnect();
      currentWebSocketHandler = null;
      chatStateManager.dispatch({ type: 'SET_STREAMING', payload: false });
    }
  };

  // 复制消息
  const copyMessage = (content: string) => {
    navigator.clipboard.writeText(content);
    MessagePlugin.success('消息已复制到剪贴板！');
  };

  // 重新生成消息
  const regenerateMessage = async (messageId: string) => {
    const message = messages.value.find(m => m.id === messageId);
    if (!message || message.role !== 'assistant') return;

    // 找到上一条用户消息
    const userMessageIndex = messages.value.findIndex(m => m.id === messageId);
    let userMessage: ChatMessage | null = null;
    
    for (let i = userMessageIndex - 1; i >= 0; i--) {
      if (messages.value[i].role === 'user') {
        userMessage = messages.value[i];
        break;
      }
    }

    if (!userMessage) {
      MessagePlugin.error('找不到对应的用户消息');
      return;
    }

    // 删除当前AI消息
    const messageIndex = messages.value.findIndex(m => m.id === messageId);
    if (messageIndex > -1) {
      messages.value.splice(messageIndex, 1);
    }

    // 重新发送消息
    await sendMessage(userMessage.content);
  };

  // 重置聊天状态
  const resetChat = () => {
    chatStateManager.dispatch({ type: 'RESET_STATE' });
    if (currentWebSocketHandler) {
      currentWebSocketHandler.disconnect();
      currentWebSocketHandler = null;
    }
  };

  // 获取角色头像
  const getAvatarForRole = (role: string): string => {
    const avatars = {
      user: 'https://tdesign.gtimg.com/site/avatar.jpg',
      assistant: 'https://api.dicebear.com/7.x/bottts/svg?seed=assistant&backgroundColor=0052d9',
      system: 'https://api.dicebear.com/7.x/bottts/svg?seed=system&backgroundColor=ed7b2f'
    };
    return avatars[role as keyof typeof avatars] || avatars.assistant;
  };

  // 从会话类型获取系统类型
  const getSystemTypeFromSession = (systemType?: string): 'brain' | 'code' | 'writing' | undefined => {
    if (!systemType) return undefined;
    
    const typeMap: Record<string, 'brain' | 'code' | 'writing'> = {
      'brain': 'brain',
      'code': 'code',
      'writing': 'writing'
    };
    
    return typeMap[systemType];
  };

  // 获取系统名称
  const getSystemName = (systemType?: 'brain' | 'code' | 'writing'): string => {
    const names = {
      brain: '中枢系统',
      code: '代码执行',
      writing: '论文生成'
    };
    return systemType ? names[systemType] : 'AI助手';
  };

  // 获取系统头像
  const getSystemAvatar = (systemType?: 'brain' | 'code' | 'writing'): string => {
    const avatars = {
      brain: 'https://api.dicebear.com/7.x/bottts/svg?seed=brain&backgroundColor=0052d9',
      code: 'https://api.dicebear.com/7.x/bottts/svg?seed=code&backgroundColor=00a870',
      writing: 'https://api.dicebear.com/7.x/bottts/svg?seed=writing&backgroundColor=ed7b2f'
    };
    return systemType ? avatars[systemType] : avatars.brain;
  };

  // 计算属性
  const hasMessages = computed(() => messages.value.length > 0);
  const lastMessage = computed(() => messages.value[messages.value.length - 1]);
  const userMessageCount = computed(() => messages.value.filter(m => m.role === 'user').length);
  const assistantMessageCount = computed(() => messages.value.filter(m => m.role === 'assistant').length);

  // 清理资源
  onUnmounted(() => {
    unsubscribe();
    if (currentStreamHandler) {
      currentStreamHandler.stop();
    }
    if (currentWebSocketHandler) {
      currentWebSocketHandler.disconnect();
    }
  });

  return {
    // 状态
    currentSession,
    messages,
    isLoading,
    isStreaming,
    error,
    
    // 计算属性
    hasMessages,
    lastMessage,
    userMessageCount,
    assistantMessageCount,
    
    // 方法
    createSession,
    sendMessage,
    stopStreaming,
    copyMessage,
    regenerateMessage,
    resetChat,
    loadChatHistory,
    getSystemName,
    getSystemAvatar
  };
}
