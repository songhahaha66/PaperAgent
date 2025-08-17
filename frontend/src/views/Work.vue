<template>
  <div class="work-page">
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
            <h1>{{ currentWork.title }}</h1>
            <t-tag :theme="getStatusTheme(currentWork.status)" variant="light">
              {{ getStatusText(currentWork.status) }}
            </t-tag>
          </div>
          <p>创建于 {{ formatDate(currentWork.created_at) }}</p>
        </div>
        <div class="work-actions">
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
            <div class="chat-messages">
              <div 
                v-for="(message, index) in chatMessages" 
                :key="message.id" 
                :class="[
                  'chat-message-wrapper',
                  { 'message-dimmed': hoveredDivider !== null && index > hoveredDivider }
                ]"
              >
                <ChatItem
                  :role="message.role"
                  :content="message.content"
                  :datetime="message.datetime"
                  :avatar="getSystemAvatar(message)"
                  :actions="message.role === 'assistant' ? 'copy' : undefined"
                  @operation="(action) => {
                    if (action === 'copy') copyMessage(message.content)
                  }"
                />
                <div v-if="message.systemType" :class="['system-label', message.systemType]">
                  {{ getSystemName(message) }}
                </div>
                
                <!-- 对话分割线 -->
                <div 
                  v-if="index < chatMessages.length - 1" 
                  class="message-divider"
                >
                  <div class="divider-line"></div>
                  <div 
                    class="divider-icon" 
                    :class="{ 'show': hoveredDivider === index }"
                    @mouseenter="showDivider(index)"
                    @mouseleave="hideDivider"
                  >
                    <t-icon name="arrow-up" />
                  </div>
                </div>
              </div>
            </div>
            <FileManager 
              :file-tree-data="fileTreeData"
              @file-select="handleFileSelect"
            />
            <div class="chat-input">
              <ChatSender
                v-model="inputValue"
                placeholder="请输入您的问题..."
                @send="sendMessage"
                :disabled="isStreaming"
              />
            </div>
          </div>
        </div>
        
        <div class="preview-section">
          <div v-if="selectedFile && fileContents[selectedFile]">
            <t-card :title="`文件预览: ${selectedFile}`">
              <div class="file-preview">
                <div v-if="selectedFile.endsWith('.py')" class="code-preview">
                  <pre><code>{{ fileContents[selectedFile] }}</code></pre>
                </div>
                <div v-else-if="selectedFile.endsWith('.md')" class="markdown-preview">
                  <div v-html="renderMarkdown(fileContents[selectedFile])"></div>
                </div>
                <div v-else-if="selectedFile.endsWith('.txt') || selectedFile.endsWith('.log')" class="text-preview">
                  <pre>{{ fileContents[selectedFile] }}</pre>
                </div>
                <div v-else class="text-preview">
                  <pre>{{ fileContents[selectedFile] }}</pre>
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
                <p><strong>模板：</strong>{{ currentWork.template_id ? `模板ID: ${currentWork.template_id}` : '未选择模板' }}</p>
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
import { workspaceAPI, workspaceFileAPI, type Work, type FileInfo } from '@/api/workspace';
import { chatAPI, WebSocketChatHandler, type ChatMessage, type ChatSessionResponse, type ChatSessionCreateRequest } from '@/api/chat';
import Sidebar from '@/components/Sidebar.vue';
import FileManager from '@/components/FileManager.vue';

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const workId = computed(() => route.params.work_id as string);

// 侧边栏折叠状态
const isSidebarCollapsed = ref(false);

// 当前工作信息
const currentWork = ref<Work | null>(null);

// 加载状态
const loading = ref(false);

// 定义聊天消息类型（使用API中的类型）
interface ChatMessageDisplay extends ChatMessage {
  systemType?: 'brain' | 'code' | 'writing' // 更新为API中的系统类型
}

// 聊天消息数据
const chatMessages = ref<ChatMessageDisplay[]>([]);

// 输入框内容
const inputValue = ref('')

// 分割线悬停状态
const hoveredDivider = ref<number | null>(null)

// 文件管理器状态
const selectedFile = ref<string | null>(null)

// 文件树数据
const fileTreeData = ref([])

// 文件内容映射
const fileContents: Record<string, string> = {}

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null);

// 聊天相关状态
const currentChatSession = ref<ChatSessionResponse | null>(null);
const isStreaming = ref(false);
const webSocketHandler = ref<WebSocketChatHandler | null>(null);

// 根据消息内容判断系统类型
const getSystemTypeFromContent = (content: string): 'brain' | 'code' | 'writing' | undefined => {
  if (content.includes('<main_agent>')) {
    return 'brain';
  } else if (content.includes('<call_code_agent>') || content.includes('<ret_code_agent>') || 
             content.includes('<call_exec>') || content.includes('<ret_exec>') ||
             content.includes('<tool_call>') || content.includes('<tool_result>') ||
             content.includes('<execution_start>') || content.includes('<execution_complete>') ||
             content.includes('<tool_error>')) {
    return 'code';
  } else if (content.includes('<writemd_result>') || content.includes('<tree_result>')) {
    return 'writing';
  }
  return undefined;
};

// 加载工作信息
const loadWork = async () => {
  if (!workId.value || !authStore.token) return;
  
  loading.value = true;
  try {
    const work = await workspaceAPI.getWork(authStore.token, workId.value);
    currentWork.value = work;
    
    // 设置当前选中的历史工作
    activeHistoryId.value = work.id;
    
    // 加载工作空间文件
    await loadWorkspaceFiles();
    
    // 初始化聊天会话
    await initializeChatSession();
    
  } catch (error) {
    console.error('加载工作信息失败:', error);
    MessagePlugin.error('加载工作信息失败');
  } finally {
    loading.value = false;
  }
};

// 初始化聊天会话（重构后简化）
const initializeChatSession = async () => {
  if (!authStore.token || !workId.value) return;
  
  try {
    // 直接使用新API加载聊天历史
    await loadChatHistory();
    
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
      total_messages: chatMessages.value.length
    };
  } catch (error) {
    console.error('初始化聊天会话失败:', error);
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
      total_messages: 0
    };
  }
};

// 重构后不再需要显式创建聊天会话，MainAgent会自动处理

// 自动滚动到底部
const scrollToBottom = () => {
  nextTick(() => {
    const chatContainer = document.querySelector('.chat-messages');
    if (chatContainer) {
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }
  });
};

// 加载聊天历史（使用新API）
const loadChatHistory = async () => {
  if (!authStore.token || !workId.value) return;
  
  try {
    // 使用新的work聊天历史API
    const historyData = await chatAPI.getWorkChatHistory(authStore.token, workId.value);
    
    // 转换消息格式
    chatMessages.value = historyData.messages.map((msg, index) => {
      let systemType: 'brain' | 'code' | 'writing' | undefined = undefined;
      
      if (msg.role === 'assistant') {
        // 根据消息内容判断系统类型
        systemType = getSystemTypeFromContent(msg.content);
        
        // 如果没有明确的XML标签，默认为brain类型（中枢系统）
        if (!systemType) {
          systemType = 'brain';
        }
      }
      
      return {
        id: `msg_${index}`,
        role: msg.role as 'user' | 'assistant' | 'error' | 'model-change' | 'system',
        content: msg.content,
        datetime: new Date(msg.timestamp).toLocaleString(),
        avatar: msg.role === 'user' ? 'https://tdesign.gtimg.com/site/avatar.jpg' : getSystemAvatar({ systemType }),
        systemType: systemType,
        isStreaming: false
      };
    });
    
    // 加载完聊天历史后自动滚动到底部
    scrollToBottom();
    
  } catch (error) {
    console.error('加载聊天历史失败:', error);
    // 如果加载失败，初始化为空数组
    chatMessages.value = [];
  }
};

// 加载工作空间文件
const loadWorkspaceFiles = async () => {
  if (!workId.value || !authStore.token) return;
  
  try {
    const files = await workspaceFileAPI.listFiles(authStore.token, workId.value);
    
    // 更新文件树数据
    updateFileTreeData(files);
    
  } catch (error) {
    console.error('加载工作空间文件失败:', error);
    MessagePlugin.error('加载工作空间文件失败');
  }
};

// 更新文件树数据
const updateFileTreeData = (files: FileInfo[]) => {
  // 暂时保持默认结构，后续可根据需要实现
};



// 删除工作
const deleteWork = async () => {
  if (!workId.value || !authStore.token || !currentWork.value) return;
  
  try {
    await workspaceAPI.deleteWork(authStore.token, workId.value);
    MessagePlugin.success('工作已删除');
    
    // 跳转回首页
    router.push('/home');
    
  } catch (error) {
    console.error('删除工作失败:', error);
    MessagePlugin.error('删除工作失败');
  }
};

// 处理文件选择
const handleFileSelect = async (fileKey: string) => {
  if (!workId.value || !authStore.token) return;
  
  selectedFile.value = fileKey;
  
  try {
    const content = await workspaceFileAPI.readFile(authStore.token, workId.value, fileKey);
    fileContents[fileKey] = content;
  } catch (error) {
    console.error('读取文件失败:', error);
    fileContents[fileKey] = '文件读取失败';
  }
};

// 简单的Markdown渲染函数
const renderMarkdown = (text: string) => {
  // 先处理代码块，避免代码块中的标记被处理
  let codeBlocks: string[] = [];
  let codeBlockCounter = 0;
  
  // 提取代码块
  text = text.replace(/```[\s\S]*?```/g, (match) => {
    codeBlocks.push(match);
    return `{{CODE_BLOCK_${codeBlockCounter++}}}`;
  });
  
  // 处理行内代码
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  
  // 按照从h6到h1的顺序处理标题
  text = text
    .replace(/^###### (.*$)/gim, '<h6>$1</h6>')
    .replace(/^##### (.*$)/gim, '<h5>$1</h5>')
    .replace(/^#### (.*$)/gim, '<h4>$1</h4>')
    .replace(/^### (.*$)/gim, '<h3>$1</h3>')
    .replace(/^## (.*$)/gim, '<h2>$1</h2>')
    .replace(/^# (.*$)/gim, '<h1>$1</h1>');
  
  // 处理粗体和斜体
  text = text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>');
  
  // 处理链接
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  
  // 处理无序列表
  text = text.replace(/^[\*|\-|\+](.*)$/gim, '<li>$1</li>');
  text = text.replace(/(<li>.*<\/li>)/gims, '<ul>$1</ul>');
  
  // 处理有序列表
  text = text.replace(/^\d+\.(.*)$/gim, '<li>$1</li>');
  text = text.replace(/(<li>.*<\/li>)/gims, '<ol>$1</ol>');
  
  // 处理换行（但不在块级元素内部添加<br>）
  text = text.replace(/\n/g, '<br>');
  
  // 恢复代码块
  for (let i = 0; i < codeBlockCounter; i++) {
    // 简单地将代码块用pre标签包裹
    const codeContent = codeBlocks[i].replace(/```/g, '');
    text = text.replace(`{{CODE_BLOCK_${i}}}`, `<pre><code>${codeContent}</code></pre>`);
  }
  
  return text;
}

// 显示分割线
const showDivider = (index: number) => {
  hoveredDivider.value = index
}

// 隐藏分割线
const hideDivider = () => {
  hoveredDivider.value = null
}

// 获取系统头像
const getSystemAvatar = (message: ChatMessageDisplay | { systemType?: 'brain' | 'code' | 'writing' }) => {
  if (message.systemType) {
    const systemAvatars = {
      brain: 'https://api.dicebear.com/7.x/bottts/svg?seed=brain&backgroundColor=0052d9',   // 中枢系统头像 - 蓝色机器人
      code: 'https://api.dicebear.com/7.x/bottts/svg?seed=code&backgroundColor=00a870',    // 代码执行系统头像 - 绿色机器人
      writing: 'https://api.dicebear.com/7.x/bottts/svg?seed=writing&backgroundColor=ed7b2f' // 论文生成系统头像 - 橙色机器人
    }
    return systemAvatars[message.systemType]
  }
  // 如果没有系统类型，返回默认头像
  return 'https://tdesign.gtimg.com/site/avatar.jpg'
}

// 获取系统名称
const getSystemName = (message: ChatMessageDisplay) => {
  if (message.systemType) {
    const systemNames = {
      brain: '中枢系统',
      code: '代码执行',
      writing: '论文生成'
    }
    return systemNames[message.systemType]
  }
  return 'AI助手'
}

// 发送消息
const sendMessage = async (messageContent?: string) => {
  const content = messageContent || inputValue.value.trim()
  if (!content || isStreaming.value) return
  
  // 添加用户消息
  const userMessage: ChatMessageDisplay = {
    id: Date.now().toString(),
    role: 'user',
    content: content,
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
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
    role: 'assistant',
    content: '',
    datetime: new Date().toLocaleString(),
    avatar: getSystemAvatar({ systemType: 'brain' }), // 默认使用brain类型，后续会根据内容更新
    systemType: 'brain', // 默认使用brain类型，后续会根据内容更新
    isStreaming: true
  }
  
  chatMessages.value.push(aiMessage)
  
  // 强制Vue更新视图
  chatMessages.value = [...chatMessages.value]
  
  try {
    // 使用WebSocket发送消息
    await sendMessageViaWebSocket(message, aiMessageId);
    
  } catch (error) {
    console.error('发送消息失败:', error)
    const messageIndex = chatMessages.value.findIndex(m => m.id === aiMessageId)
    if (messageIndex > -1) {
      chatMessages.value[messageIndex].content = '发送消息失败，请稍后重试'
      chatMessages.value[messageIndex].isStreaming = false
      chatMessages.value[messageIndex].avatar = getSystemAvatar({ systemType: 'brain' })
    }
    isStreaming.value = false
    MessagePlugin.error('发送消息失败')
  }
}

// WebSocket方式发送消息
const sendMessageViaWebSocket = async (message: string, aiMessageId: string) => {
  try {
    console.log('Starting WebSocket connection for message:', { messageId: aiMessageId, message: message });
    
    // 创建WebSocket处理器（使用workId）
    webSocketHandler.value = new WebSocketChatHandler(
      workId.value!,
      authStore.token!
    );

    // 连接WebSocket
    await webSocketHandler.value.connect();
    console.log('WebSocket connected successfully');

    let fullContent = '';
    let currentSystemType: 'brain' | 'code' | 'writing' = 'brain'; // 默认从brain开始，后续根据内容更新
    let systemTypeChanged = false;

    // 设置消息监听器
    webSocketHandler.value.onMessage((data) => {
      switch (data.type) {
        case 'start':
          break;
        case 'content':
          // 内容更新 - 实时流式显示
          const messageIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (messageIndex > -1) {
            // 实时更新内容，实现流式显示效果
            fullContent += data.content;
            
            // 调试日志
            console.log('WebSocket content update:', {
              messageId: aiMessageId,
              contentLength: fullContent.length,
              currentSystemType,
              hasMainAgent: fullContent.includes('<main_agent>'),
              hasCodeAgent: fullContent.includes('<call_code_agent>') || fullContent.includes('<ret_code_agent>'),
              hasWriting: fullContent.includes('<writemd_result>') || fullContent.includes('<tree_result>')
            });
            
            // 根据累积的完整内容判断系统类型（而不是单个片段）
            let newSystemType = currentSystemType;
            if (fullContent.includes('<main_agent>')) {
              newSystemType = 'brain';
            } else if (fullContent.includes('<call_code_agent>') || fullContent.includes('<ret_code_agent>') ||
                       fullContent.includes('<call_exec>') || fullContent.includes('<ret_exec>') ||
                       fullContent.includes('<tool_call>') || fullContent.includes('<tool_result>') ||
                       fullContent.includes('<execution_start>') || fullContent.includes('<execution_complete>') ||
                       fullContent.includes('<tool_error>')) {
              newSystemType = 'code';
            } else if (fullContent.includes('<writemd_result>') || fullContent.includes('<tree_result>')) {
              newSystemType = 'writing';
            }
            
            // 如果系统类型发生变化，更新显示
            if (newSystemType !== currentSystemType) {
              console.log('System type changed:', { from: currentSystemType, to: newSystemType });
              currentSystemType = newSystemType;
              systemTypeChanged = true;
            }
            
            // 使用Vue的响应式更新，确保视图正确刷新
            const updatedMessage = {
              ...chatMessages.value[messageIndex],
              content: fullContent,
              systemType: currentSystemType,
              avatar: getSystemAvatar({ systemType: currentSystemType })
            };
            
            // 替换整个消息对象，确保Vue响应式更新
            chatMessages.value.splice(messageIndex, 1, updatedMessage);
            
            // 调试日志
            console.log('Message updated:', {
              messageId: aiMessageId,
              systemType: currentSystemType,
              contentLength: fullContent.length,
              avatar: getSystemAvatar({ systemType: currentSystemType })
            });
            
            // 自动滚动到底部
            nextTick(() => {
              const chatContainer = document.querySelector('.chat-messages');
              if (chatContainer) {
                chatContainer.scrollTop = chatContainer.scrollHeight;
              }
            });
          }
          break;
        case 'xml_open':
        case 'xml_close':
          break;
        case 'tool_call':
          // 工具调用开始通知
          const toolCallIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (toolCallIndex > -1) {
            console.log('Tool call notification:', { messageId: aiMessageId, content: data.content });
            const toolCallMessage = {
              ...chatMessages.value[toolCallIndex],
              content: chatMessages.value[toolCallIndex].content + `\n\n🔧 **工具调用**: ${data.content}`,
              systemType: 'code',
              avatar: getSystemAvatar({ systemType: 'code' })
            };
            // 使用splice确保Vue响应式更新
            chatMessages.value.splice(toolCallIndex, 1, toolCallMessage);
            console.log('Tool call message updated, system type set to code');
          }
          break;
        case 'tool_result':
          // 工具调用结果通知
          const toolResultIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (toolResultIndex > -1) {
            const toolResultMessage = {
              ...chatMessages.value[toolResultIndex],
              content: chatMessages.value[toolResultIndex].content + `\n\n✅ **工具执行结果**: ${data.content}`,
              systemType: 'code',
              avatar: getSystemAvatar({ systemType: 'code' })
            };
            // 使用splice确保Vue响应式更新
            chatMessages.value.splice(toolResultIndex, 1, toolResultMessage);
          }
          break;
        case 'execution_start':
          // 代码执行开始通知
          const execStartIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (execStartIndex > -1) {
            const execStartMessage = {
              ...chatMessages.value[execStartIndex],
              content: chatMessages.value[execStartIndex].content + `\n\n🚀 **代码执行**: ${data.content}`,
              systemType: 'code',
              avatar: getSystemAvatar({ systemType: 'code' })
            };
            // 使用splice确保Vue响应式更新
            chatMessages.value.splice(execStartIndex, 1, execStartMessage);
          }
          break;
        case 'execution_complete':
          // 代码执行完成通知
          const execCompleteIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (execCompleteIndex > -1) {
            const execCompleteMessage = {
              ...chatMessages.value[execCompleteIndex],
              content: chatMessages.value[execCompleteIndex].content + `\n\n✅ **执行完成**: ${data.content}`,
              systemType: 'code',
              avatar: getSystemAvatar({ systemType: 'code' })
            };
            // 使用splice确保Vue响应式更新
            chatMessages.value.splice(execCompleteIndex, 1, execCompleteMessage);
          }
          break;
        case 'tool_error':
          // 工具调用错误通知
          const toolErrorIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (toolErrorIndex > -1) {
            const toolErrorMessage = {
              ...chatMessages.value[toolErrorIndex],
              content: chatMessages.value[toolErrorIndex].content + `\n\n❌ **工具错误**: ${data.content}`,
              systemType: 'code',
              avatar: getSystemAvatar({ systemType: 'code' })
            };
            // 使用splice确保Vue响应式更新
            chatMessages.value.splice(toolErrorIndex, 1, toolErrorMessage);
          }
          break;
        case 'complete':
          // 完成消息
          const completeIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (completeIndex > -1) {
            const completeMessage = {
              ...chatMessages.value[completeIndex],
              isStreaming: false,
              content: fullContent,
              systemType: currentSystemType,
              avatar: getSystemAvatar({ systemType: currentSystemType })
            };
            // 使用splice确保Vue响应式更新
            chatMessages.value.splice(completeIndex, 1, completeMessage);
          }
          isStreaming.value = false;
          webSocketHandler.value = null;
          break;
        case 'error':
          // 错误消息
          const errorIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (errorIndex > -1) {
            const errorMessage = {
              ...chatMessages.value[errorIndex],
              content: `错误: ${data.message}`,
              isStreaming: false,
              avatar: getSystemAvatar({ systemType: currentSystemType }),
              systemType: currentSystemType
            };
            // 使用splice确保Vue响应式更新
            chatMessages.value.splice(errorIndex, 1, errorMessage);
          }
          isStreaming.value = false;
          webSocketHandler.value = null;
          MessagePlugin.error(`聊天失败: ${data.message}`);
          break;
      }
    });

    // 等待一下确保监听器设置完成
    await new Promise(resolve => setTimeout(resolve, 100));
    
    // 发送消息
    console.log('Sending message via WebSocket:', message);
    webSocketHandler.value.sendMessage(message);

  } catch (err) {
    console.error('WebSocket处理错误:', err);
    throw err;
  }
};





// 复制消息
const copyMessage = (content: string) => {
  navigator.clipboard.writeText(content)
  MessagePlugin.success('消息已复制到剪贴板！')
}



// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value;
};

// 新建工作
const createNewTask = () => {
  router.push('/home');
};

// 选择历史工作
const selectHistory = (id: number) => {
  // 侧边栏会处理跳转逻辑，这里只需要更新选中状态
  activeHistoryId.value = id;
};

// 格式化日期
const formatDate = (dateString: string) => {
  return new Date(dateString).toLocaleString();
};

// 获取状态主题
const getStatusTheme = (status: string) => {
  const themes: Record<string, 'default' | 'primary' | 'success' | 'warning' | 'danger'> = {
    'created': 'default',
    'in_progress': 'primary',
    'completed': 'success',
    'paused': 'warning',
    'cancelled': 'danger'
  };
  return themes[status] || 'default';
};

// 获取状态文本
const getStatusText = (status: string) => {
  const texts: Record<string, string> = {
    'created': '已创建',
    'in_progress': '进行中',
    'completed': '已完成',
    'paused': '已暂停',
    'cancelled': '已取消'
  };
  return texts[status] || status;
};

// 组件卸载时清理资源
onUnmounted(() => {
  if (webSocketHandler.value) {
    webSocketHandler.value.disconnect();
    webSocketHandler.value = null;
  }
});

// 监听路由变化
watch(() => route.params.work_id, (newWorkId) => {
  if (newWorkId) {
    loadWork();
    // 重新初始化聊天会话
    if (authStore.token) {
      initializeChatSession();
    }
  }
});

// 监听聊天消息变化，自动滚动到底部
watch(chatMessages, (newMessages) => {
  if (newMessages.length > 0) {
    scrollToBottom();
  }
}, { deep: true });

// 组件挂载时加载工作信息
onMounted(() => {
  if (workId.value) {
    loadWork();
    // 初始化聊天会话
    if (authStore.token) {
      initializeChatSession();
    }
  }
  
  // 组件挂载完成后，如果有聊天消息，自动滚动到底部
  nextTick(() => {
    if (chatMessages.value.length > 0) {
      scrollToBottom();
    }
  });
});
</script>

<style>
/* 全局样式确保页面占满视口 */
html, body {
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
  gap:10px;
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
  min-height: 0;
}

.chat-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #eee;
  padding: 20px;
  min-width: 300px;
  overflow: hidden;
}

.preview-section {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  background: #f9f9f9;
  min-height: 0;
}



.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid #eee;
  border-radius: 8px;
  overflow: hidden;
  background: white;
  min-height: 0;
}

.chat-messages {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  background: #fafafa;
  min-height: 0;
}

.chat-input {
  padding: 16px;
  border-top: 1px solid #eee;
  background: white;
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
</style>
