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
                @send="() => sendMessage()"
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
              <div class="pdf-container">
                <iframe 
                  src="/main.pdf" 
                  width="100%" 
                  height="600px"
                  style="border: none; border-radius: 8px;"
                  title="论文PDF预览"
                ></iframe>
              </div>
              <div class="pdf-info">
                <p>正在展示：main.pdf</p>
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
import { Tree, Collapse, CollapsePanel } from 'tdesign-vue-next';
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
const fileTreeData = ref([
  {
    value: 'generated_code',
    label: '生成的代码',
    children: [
      { value: 'main.py', label: 'main.py', isLeaf: true },
      { value: 'requirements.txt', label: 'requirements.txt', isLeaf: true },
      { value: 'data', label: '数据文件', isLeaf: true }
    ]
  },
  {
    value: 'execution_results',
    label: '执行结果',
    children: [
      { value: 'output.log', label: 'output.log', isLeaf: true },
      { value: 'plots', label: '图表', isLeaf: true },
      { value: 'data_output', label: '数据输出', isLeaf: true }
    ]
  },
  {
    value: 'paper_drafts',
    label: '论文草稿',
    children: [
      { value: 'outline.md', label: '大纲', isLeaf: true },
      { value: 'sections', label: '章节', isLeaf: true },
      { value: 'final_paper.md', label: '最终论文', isLeaf: true }
    ]
  },
  {
    value: 'resources',
    label: '相关资源',
    children: [
      { value: 'references', label: '参考文献', isLeaf: true },
      { value: 'images', label: '图片', isLeaf: true }
    ]
  }
])

// 文件内容映射
const fileContents: Record<string, string> = {}

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null);

// 聊天相关状态
const currentChatSession = ref<ChatSessionResponse | null>(null);
const isStreaming = ref(false);
const webSocketHandler = ref<WebSocketChatHandler | null>(null);

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

// 初始化聊天会话
const initializeChatSession = async () => {
  if (!authStore.token || !workId.value) return;
  
  console.log('初始化聊天会话，workId:', workId.value);
  console.log('认证token:', authStore.token ? '已设置' : '未设置');
  
  try {
    // 获取工作的聊天会话列表
    const sessions = await chatAPI.getChatSessions(authStore.token, workId.value);
    console.log('获取到的聊天会话:', sessions);
    
    if (sessions.length > 0) {
      // 使用第一个会话（最新的）
      currentChatSession.value = sessions[0];
      console.log('设置当前聊天会话:', currentChatSession.value);
      await loadChatHistory();
    } else {
      // 创建新的聊天会话
      console.log('创建新的聊天会话');
      await createChatSession('brain'); // 默认使用中枢系统
    }
  } catch (error) {
    console.error('初始化聊天会话失败:', error);
    // 不显示错误，继续使用模拟聊天
  }
};

// 创建聊天会话
const createChatSession = async (systemType: 'brain' | 'code' | 'writing' = 'brain') => {
  if (!authStore.token || !workId.value) return;
  
  try {
    const request: ChatSessionCreateRequest = {
      work_id: workId.value,
      system_type: systemType,
      title: `${currentWork.value?.title || '工作'} - 聊天会话`
    };
    
    const session = await chatAPI.createChatSession(authStore.token, request);
    currentChatSession.value = session;
    
    // 清空现有消息
    chatMessages.value = [];
    
    MessagePlugin.success('聊天会话创建成功');
  } catch (error) {
    console.error('创建聊天会话失败:', error);
    MessagePlugin.error('创建聊天会话失败');
  }
};

// 加载聊天历史
const loadChatHistory = async () => {
  if (!authStore.token || !currentChatSession.value) return;
  
  try {
    const messages = await chatAPI.getChatHistory(authStore.token, currentChatSession.value.session_id);
    
    // 转换消息格式
    chatMessages.value = messages.map(msg => {
      // 根据消息内容判断系统类型
      let systemType: 'brain' | 'code' | 'writing' | undefined = undefined;
      
      if (msg.role === 'assistant') {
        // 只有AI消息才判断系统类型
        if (msg.content.includes('<main_agent>')) {
          systemType = 'brain';
        } else if (msg.content.includes('<call_code_agent>') || msg.content.includes('<ret_code_agent>')) {
          systemType = 'code';
        } else if (msg.content.includes('<call_exec>') || msg.content.includes('<ret_exec>')) {
          systemType = 'code';
        } else if (msg.content.includes('<writemd_result>') || msg.content.includes('<tree_result>')) {
          systemType = 'writing';
        } else {
          // 如果没有明确的标签，使用会话的默认系统类型
          systemType = currentChatSession.value?.system_type as 'brain' | 'code' | 'writing';
        }
      }
      
      return {
        id: msg.id.toString(),
        role: msg.role as 'user' | 'assistant' | 'error' | 'model-change' | 'system',
        content: msg.content,
        datetime: new Date(msg.created_at).toLocaleString(),
        avatar: msg.role === 'user' ? 'https://tdesign.gtimg.com/site/avatar.jpg' : getSystemAvatar({ systemType }),
        systemType: systemType,
        isStreaming: false // 确保所有消息都不是流式传输
      };
    });
    
  } catch (error) {
    console.error('加载聊天历史失败:', error);
    // 不显示错误，使用空消息列表
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
    // 不显示错误，保持默认文件树
  }
};

// 更新文件树数据
const updateFileTreeData = (files: FileInfo[]) => {
  // 这里可以根据实际的文件结构来更新文件树
  // 暂时保持默认结构
};

// 刷新工作信息
const refreshWork = async () => {
  await loadWork();
  MessagePlugin.success('工作信息已刷新');
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
    // 尝试读取文件内容
    const content = await workspaceFileAPI.readFile(authStore.token, workId.value, fileKey);
    fileContents[fileKey] = content;
  } catch (error) {
    console.error('读取文件失败:', error);
    // 使用默认内容
    fileContents[fileKey] = `文件 ${fileKey} 的内容将在这里显示...`;
  }
};

// 简单的Markdown渲染函数
const renderMarkdown = (text: string) => {
  return text
    .replace(/^### (.*$)/gim, '<h3>$1</h3>')
    .replace(/^## (.*$)/gim, '<h2>$1</h2>')
    .replace(/^# (.*$)/gim, '<h1>$1</h1>')
    .replace(/\*\*(.*)\*\*/gim, '<strong>$1</strong>')
    .replace(/\*(.*)\*/gim, '<em>$1</em>')
    .replace(/\n/gim, '<br>')
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
const sendMessage = async (messageContent?: string, isRegenerate: boolean = false) => {
  const content = messageContent || inputValue.value.trim()
  if (!content || isStreaming.value) return
  
  // 如果不是重新生成，清空输入框并添加用户消息
  if (!isRegenerate) {
    const userMessage: ChatMessageDisplay = {
      id: Date.now().toString(),
      role: 'user',
      content: content,
      datetime: new Date().toLocaleString(),
      avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
    }
    
    chatMessages.value.push(userMessage)
    inputValue.value = ''
  }
  
  // 如果有聊天会话，使用真实API
  if (currentChatSession.value && authStore.token) {
    await sendRealMessage(content, isRegenerate)
  } else {
    // 回退到模拟聊天
    await sendMockMessage(content, isRegenerate)
  }
}

// 发送真实消息（WebSocket）
const sendRealMessage = async (message: string, isRegenerate: boolean = false) => {
  if (!currentChatSession.value || !authStore.token) return
  
  isStreaming.value = true
  
  let aiMessage: ChatMessageDisplay
  let aiMessageId: string
  
  if (isRegenerate) {
    // 重新生成：找到最后一条AI消息并更新它
    console.log('重新生成模式，当前消息列表:', chatMessages.value.map(m => ({ id: m.id, role: m.role, content: m.content.substring(0, 50) })))
    
    // 尝试多种方式查找AI消息
    let lastAiMessageIndex = -1
    for (let i = chatMessages.value.length - 1; i >= 0; i--) {
      if (chatMessages.value[i].role === 'assistant') {
        lastAiMessageIndex = i
        break
      }
    }
    console.log('找到的最后一条AI消息索引:', lastAiMessageIndex)
    
    if (lastAiMessageIndex === -1) {
      console.error('没有找到可重新生成的消息，所有消息:', chatMessages.value)
      MessagePlugin.error('没有找到可重新生成的消息')
      isStreaming.value = false
      return
    }
    
    aiMessage = chatMessages.value[lastAiMessageIndex]
    aiMessageId = aiMessage.id
    aiMessage.content = ''
    aiMessage.isStreaming = true
    console.log('准备重新生成消息:', aiMessageId)
  } else {
    // 新消息：创建AI回复消息
    aiMessageId = (Date.now() + 1).toString()
    aiMessage = {
      id: aiMessageId,
      role: 'assistant',
      content: '', // 从空内容开始，通过流式传输填充
      datetime: new Date().toLocaleString(),
      avatar: getSystemAvatar({ systemType: currentChatSession.value.system_type as 'brain' | 'code' | 'writing' }),
      systemType: currentChatSession.value.system_type as 'brain' | 'code' | 'writing',
      isStreaming: true
    }
    chatMessages.value.push(aiMessage)
    
    // 强制Vue更新视图
    chatMessages.value = [...chatMessages.value]
    console.log('创建AI消息:', aiMessage)
  }
  
  try {
    console.log('开始WebSocket聊天，session_id:', currentChatSession.value.session_id);
    console.log('API基础URL:', import.meta.env.VITE_API_BASE_URL);
    
    // 使用WebSocket发送消息
    await sendMessageViaWebSocket(message, aiMessageId);
    
  } catch (error) {
    console.error('发送消息失败:', error)
    const messageIndex = chatMessages.value.findIndex(m => m.id === aiMessageId)
    if (messageIndex > -1) {
      chatMessages.value[messageIndex].content = '发送消息失败，请稍后重试'
      chatMessages.value[messageIndex].isStreaming = false
    }
    isStreaming.value = false
    MessagePlugin.error('发送消息失败')
  }
}

// WebSocket方式发送消息
const sendMessageViaWebSocket = async (message: string, aiMessageId: string) => {
  try {
    // 创建WebSocket处理器
    webSocketHandler.value = new WebSocketChatHandler(
      currentChatSession.value!.session_id,
      authStore.token!
    );

    // 连接WebSocket
    await webSocketHandler.value.connect();

    let fullContent = '';
    let currentSystemType: 'brain' | 'code' | 'writing' = currentChatSession.value!.system_type as 'brain' | 'code' | 'writing';
    let systemTypeChanged = false;

    // 设置消息监听器
    webSocketHandler.value.onMessage((data) => {
      console.log('收到WebSocket消息:', data);
      
      switch (data.type) {
        case 'start':
          // 开始消息，可以显示加载状态
          console.log('开始AI分析...');
          break;
        case 'content':
          // 内容更新 - 实时流式显示
          const messageIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (messageIndex > -1) {
            // 实时更新内容，实现流式显示效果
            fullContent += data.content;
            chatMessages.value[messageIndex].content = fullContent;
            
            // 根据内容判断系统类型
            let newSystemType = currentSystemType;
            if (data.content.includes('<main_agent>')) {
              newSystemType = 'brain';
            } else if (data.content.includes('<call_code_agent>') || data.content.includes('<ret_code_agent>')) {
              newSystemType = 'code';
            } else if (data.content.includes('<call_exec>') || data.content.includes('<ret_exec>')) {
              newSystemType = 'code';
            } else if (data.content.includes('<writemd_result>') || data.content.includes('<tree_result>')) {
              newSystemType = 'writing';
            }
            
            // 如果系统类型发生变化，更新显示
            if (newSystemType !== currentSystemType) {
              currentSystemType = newSystemType;
              systemTypeChanged = true;
              chatMessages.value[messageIndex].systemType = currentSystemType;
              console.log('系统类型切换为:', currentSystemType);
            }
            
            // 强制Vue更新视图 - 使用响应式更新
            const updatedMessage = { ...chatMessages.value[messageIndex] };
            updatedMessage.content = fullContent;
            updatedMessage.systemType = currentSystemType;
            chatMessages.value[messageIndex] = updatedMessage;
            
            console.log('更新消息内容:', fullContent.substring(0, 100) + '...');
            
            // 自动滚动到底部
            nextTick(() => {
              const chatContainer = document.querySelector('.chat-messages');
              if (chatContainer) {
                chatContainer.scrollTop = chatContainer.scrollHeight;
              }
            });
          } else {
            console.error('未找到消息:', aiMessageId);
          }
          break;
        case 'xml_open':
        case 'xml_close':
          // XML标签，可以用于格式化显示
          break;
        case 'complete':
          // 完成消息
          const completeIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (completeIndex > -1) {
            chatMessages.value[completeIndex].isStreaming = false;
            // 确保最终内容完整显示
            chatMessages.value[completeIndex].content = fullContent;
            
            // 如果系统类型发生了变化，显示切换提示
            if (systemTypeChanged) {
              console.log('AI分析完成，系统类型:', currentSystemType);
            }
          }
          isStreaming.value = false;
          webSocketHandler.value = null;
          console.log('AI分析完成');
          break;
        case 'error':
          // 错误消息
          const errorIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (errorIndex > -1) {
            chatMessages.value[errorIndex].content = `错误: ${data.message}`;
            chatMessages.value[errorIndex].isStreaming = false;
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
    console.log('发送消息到WebSocket:', message);
    webSocketHandler.value.sendMessage(message);

  } catch (err) {
    console.error('WebSocket处理错误:', err);
    throw err;
  }
};

// 模拟聊天（回退方案）
const sendMockMessage = async (userInput: string, isRegenerate: boolean = false) => {
  // 模拟AI回复
  setTimeout(() => {
    if (isRegenerate) {
      // 重新生成：更新最后一条AI消息
      let lastAiMessageIndex = -1
      for (let i = chatMessages.value.length - 1; i >= 0; i--) {
        if (chatMessages.value[i].role === 'assistant') {
          lastAiMessageIndex = i
          break
        }
      }
      if (lastAiMessageIndex !== -1) {
        chatMessages.value[lastAiMessageIndex].content = `我理解您希望深入了解"${userInput}"。在空调降温速率研究中，我们可以从以下几个角度来分析：1) 热传导模型建立；2) 参数分析与计算；3) 数值模拟编程；4) 结果验证与优化；5) 论文撰写与格式规范。您希望我详细阐述哪个方面？`
        chatMessages.value[lastAiMessageIndex].isStreaming = false
      }
    } else {
      // 新消息：创建AI回复
      const aiReply: ChatMessageDisplay = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `我理解您希望深入了解"${userInput}"。在空调降温速率研究中，我们可以从以下几个角度来分析：1) 热传导模型建立；2) 参数分析与计算；3) 数值模拟编程；4) 结果验证与优化；5) 论文撰写与格式规范。您希望我详细阐述哪个方面？`,
        datetime: new Date().toLocaleString(),
        avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
        systemType: currentChatSession.value?.system_type as 'brain' | 'code' | 'writing' || 'brain'
      }
      chatMessages.value.push(aiReply)
    }
  }, 1000)
}

// 复制消息
const copyMessage = (content: string) => {
  navigator.clipboard.writeText(content)
  MessagePlugin.success('消息已复制到剪贴板！')
}

// 重新生成消息
const regenerateMessage = async (messageId: string) => {
  console.log('开始重新生成消息，消息ID:', messageId)
  console.log('当前所有消息:', chatMessages.value.map(m => ({ id: m.id, role: m.role, content: m.content.substring(0, 50) })))
  
  const message = chatMessages.value.find(m => m.id === messageId)
  console.log('找到的消息:', message)
  
  if (message && message.role === 'assistant') {
    if (currentChatSession.value && authStore.token) {
      try {
        // 显示重新生成状态
        message.content = '正在重新生成回复...'
        message.isStreaming = true // 确保是流式传输
        
        // 获取上一条用户消息作为重新生成的输入
        const messageIndex = chatMessages.value.findIndex(m => m.id === messageId)
        console.log('消息索引:', messageIndex)
        
        if (messageIndex > 0) {
          const userMessage = chatMessages.value[messageIndex - 1]
          console.log('上一条用户消息:', userMessage)
          
          if (userMessage.role === 'user') {
            // 调用真实的重新生成API
            console.log('调用重新生成API，用户输入:', userMessage.content)
            await sendMessage(userMessage.content, true) // 第二个参数表示是重新生成
            return
          } else {
            console.log('上一条消息不是用户消息，角色:', userMessage.role)
          }
        }
        
        // 如果没有找到用户消息，尝试查找最近的用户消息
        if (messageIndex > 0) {
          for (let i = messageIndex - 1; i >= 0; i--) {
            const prevMessage = chatMessages.value[i]
            if (prevMessage.role === 'user') {
              console.log('找到最近的用户消息:', prevMessage.content)
              await sendMessage(prevMessage.content, true)
              return
            }
          }
        }
        
        // 如果没有找到用户消息，显示错误
        message.content = '无法重新生成：未找到原始问题'
        message.isStreaming = false
        MessagePlugin.error('无法重新生成：未找到原始问题')
        
      } catch (error) {
        console.error('重新生成消息失败:', error)
        message.content = '重新生成失败，请稍后重试'
        message.isStreaming = false
        MessagePlugin.error('重新生成失败')
      }
    } else {
      // 模拟重新生成
      message.content = '正在重新生成回复...'
      message.isStreaming = true
      setTimeout(() => {
        message.content = '这是重新生成的内容。在空调降温速率研究过程中，我们可以根据不同的要点进行深入分析，包括热传导模型优化、参数敏感性分析、数值算法改进等，确保研究内容的科学性和准确性。'
        message.isStreaming = false
      }, 1000)
    }
  } else {
    console.error('消息不存在或不是AI消息:', message)
    MessagePlugin.error('无法重新生成：消息不存在或不是AI消息')
  }
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

// 组件挂载时加载工作信息
onMounted(() => {
  if (workId.value) {
    loadWork();
    // 初始化聊天会话
    if (authStore.token) {
      initializeChatSession();
    }
  }
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

.workspace-header h1 {
  margin: 0 0 8px 0;
  color: #2c3e50;
  font-size: 1.5em;
}

.workspace-header p {
  margin: 0;
  color: #7f8c8d;
  font-size: 0.9em;
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

.welcome-content,
.history-detail {
  max-width: 100%;
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

/* PDF展示区域样式 */
.pdf-container {
  margin-bottom: 16px;
  background: #f8f9fa;
  border-radius: 8px;
  padding: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.pdf-info {
  padding: 12px 0;
  border-top: 1px solid #eee;
}

.pdf-info p {
  margin: 4px 0;
  color: #666;
  font-size: 14px;
}

.pdf-info p:first-child {
  font-weight: 500;
  color: #333;
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
