import { ref, onUnmounted, onMounted } from 'vue';
import { chatAPI } from '@/api/chat';
import { useAuthStore } from '@/stores/auth';

export interface RealtimeMessage {
  id: string;
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system';
  content: string;
  datetime: string;
  avatar: string;
  systemType?: 'brain' | 'code' | 'writing';
  isLoading?: boolean;
  isStreaming?: boolean;
  json_blocks?: any[];
  message_type?: 'text' | 'json_card';
  is_temp?: boolean;
  last_modified?: number;
}

export interface RealtimePollingState {
  isPolling: boolean;
  lastModified: number;
  tempMessage: RealtimeMessage | null;
  isGenerating: boolean;
  isComplete: boolean;
  error: string | null;
}

export function useRealtimePolling(workId: string) {
  const authStore = useAuthStore();
  
  // 轮询状态
  const state = ref<RealtimePollingState>({
    isPolling: false,
    lastModified: 0,
    tempMessage: null,
    isGenerating: false,
    isComplete: false,
    error: null
  });

  // 轮询定时器
  let pollingTimer: number | null = null;
  const pollingInterval = 500; // 500ms轮询间隔

  // 开始轮询
  const startPolling = async () => {
    if (state.value.isPolling) return;
    
    state.value.isPolling = true;
    state.value.error = null;
    console.log('开始实时轮询...');
    
    // 立即执行一次轮询
    await pollMessages();
    
    // 设置定时轮询
    pollingTimer = setInterval(pollMessages, pollingInterval);
  };

  // 停止轮询
  const stopPolling = () => {
    if (!state.value.isPolling) return;
    
    state.value.isPolling = false;
    if (pollingTimer) {
      clearInterval(pollingTimer);
      pollingTimer = null;
    }
    console.log('停止实时轮询');
  };

  // 轮询获取最新消息
  const pollMessages = async () => {
    if (!authStore.token || !workId) return;
    
    try {
      const response = await chatAPI.getRealtimeMessages(
        authStore.token,
        workId,
        state.value.lastModified
      );
      
      // 检查是否有更新
      if (response.has_updates) {
        console.log('检测到消息更新:', response);
        
        // 更新状态
        state.value.lastModified = response.last_modified;
        state.value.isGenerating = response.is_generating;
        state.value.isComplete = response.is_complete;
        
        // 处理临时消息
        if (response.temp_message) {
          state.value.tempMessage = response.temp_message;
        }
        
        // 如果生成完成，停止轮询
        if (response.is_complete) {
          console.log('生成完成，停止轮询');
          stopPolling();
          // 延迟清理临时消息，让用户看到最终结果
          setTimeout(() => {
            state.value.tempMessage = null;
          }, 2000);
        }
      }
      
    } catch (error) {
      console.error('轮询消息失败:', error);
      state.value.error = error instanceof Error ? error.message : '轮询失败';
      
      // 发生错误时继续轮询，但增加间隔
      if (state.value.isPolling) {
        setTimeout(pollMessages, pollingInterval * 2);
      }
    }
  };

  // 重置状态
  const resetState = () => {
    state.value = {
      isPolling: false,
      lastModified: 0,
      tempMessage: null,
      isGenerating: false,
      isComplete: false,
      error: null
    };
  };

  // 组件卸载时清理
  onUnmounted(() => {
    stopPolling();
  });

  return {
    state: state.value,
    startPolling,
    stopPolling,
    pollMessages,
    resetState
  };
}
