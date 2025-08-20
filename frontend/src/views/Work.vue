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
            <JsonChatRenderer :messages="chatMessages" />
            <FileManager 
              :file-tree-data="fileTreeData"
              :work-id="workId"
              :loading="loading"
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
                <div v-else-if="isImageFile(selectedFile)" class="image-preview">
                  <img v-if="imageUrls[selectedFile]" :src="imageUrls[selectedFile]" :alt="selectedFile" style="max-width: 100%; height: auto;" />
                  <div v-else class="loading-image">正在加载图片...</div>
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
import JsonChatRenderer from '@/components/JsonChatRenderer.vue';

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
const fileTreeData = ref<FileInfo[]>([])

// 文件内容映射
// 数据定义
const currentFileContent = ref('')
const currentFileName = ref('')
const fileContents = ref<Record<string, string>>({})
const currentWorkspaceConfig = ref('')

// 图片URL缓存
const imageUrls = ref<Record<string, string>>({})

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
    // 设置加载状态
    loading.value = true;
    
    const files = await workspaceFileAPI.listFiles(authStore.token, workId.value);
    
    // 更新文件树数据
    updateFileTreeData(files);
    
  } catch (error) {
    console.error('加载工作空间文件失败:', error);
    MessagePlugin.error('加载工作空间文件失败');
  } finally {
    // 确保加载状态被重置
    loading.value = false;
  }
};

// 更新文件树数据
const updateFileTreeData = (files: FileInfo[]) => {
  // 直接传递文件列表，让FileManager组件按类型分类
  fileTreeData.value = files;
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
const handleFileSelect = async (filePath: string) => {
  console.log('文件被选中:', filePath)
  currentFileName.value = filePath
  selectedFile.value = filePath  // 设置选中的文件
  
  // 检查是否为图片文件
  if (isImageFile(filePath)) {
    console.log('图片文件，获取blob URL')
    // 对于图片文件，设置一个特殊标记表示这是图片
    fileContents.value[filePath] = 'IMAGE_FILE'
    currentFileContent.value = 'IMAGE_FILE'
    
    // 如果已经有缓存的blob URL，直接使用
    if (imageUrls.value[filePath]) {
      console.log('使用缓存的图片URL')
      return
    }
    
    // 获取图片blob URL
    try {
      const blobUrl = await getImageBlobUrl(filePath)
      imageUrls.value[filePath] = blobUrl
      console.log('图片blob URL获取成功')
    } catch (error) {
      console.error('获取图片失败:', error)
      MessagePlugin.error('加载图片失败')
    }
    return
  }
  
  // 检查是否已缓存
  if (fileContents.value[filePath]) {
    currentFileContent.value = fileContents.value[filePath]
    console.log('使用缓存的文件内容')
    return
  }

  // 从服务器获取文件内容（仅对非图片文件）
  try {
    console.log('从服务器获取文件内容:', filePath)
    const content = await workspaceFileAPI.readFile(authStore.token!, workId.value, filePath)
    
    // 缓存文件内容
    fileContents.value[filePath] = content
    currentFileContent.value = content
    
    console.log('文件内容获取成功，长度:', content.length)
  } catch (error) {
    console.error('获取文件内容失败:', error)
    fileContents.value[filePath] = '文件读取失败'
    currentFileContent.value = '文件读取失败'
    MessagePlugin.error('加载文件失败')
  }
}



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

// 判断是否为图片文件
const isImageFile = (filePath: string): boolean => {
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']
  const lowerPath = filePath.toLowerCase()
  return imageExtensions.some(ext => lowerPath.endsWith(ext))
}

// 获取图片URL（使用新的图片API）
const getImageUrl = (filePath: string): string => {
  // 使用新的图片API端点
  return `${import.meta.env.VITE_API_BASE_URL || ''}/api/workspace/${workId.value}/images/${encodeURIComponent(filePath)}`;
}

// 获取图片的blob URL（带认证）
const getImageBlobUrl = async (filePath: string): Promise<string> => {
  try {
    const token = authStore.token;
    if (!token) {
      throw new Error('未登录');
    }
    
    const url = `${import.meta.env.VITE_API_BASE_URL || ''}/api/workspace/${workId.value}/images/${encodeURIComponent(filePath)}`;
    
    const response = await fetch(url, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const blob = await response.blob();
    return URL.createObjectURL(blob);
  } catch (error) {
    console.error('获取图片失败:', error);
    throw error;
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

    // 设置断开连接回调
    webSocketHandler.value.onDisconnect(() => {
      console.log('WebSocket连接已断开，重置流式状态');
      isStreaming.value = false;
    });

    // 设置消息监听器
    webSocketHandler.value.onMessage((data) => {
      console.log('WebSocket message received:', data);
      
      switch (data.type) {
        case 'start':
          console.log('AI分析开始');
          break;
          
        case 'json_block':
          // 处理JSON格式的数据块
          if (data.block) {
            handleJsonBlock(data.block, aiMessageId);
          }
          break;
          
        case 'content':
          // 兼容旧的内容格式
          handleContentUpdate(data.content, aiMessageId);
          break;
          
        case 'complete':
          console.log('AI分析完成');
          isStreaming.value = false;
          break;
          
        case 'error':
          console.error('WebSocket错误:', data.message);
          const errorIndex = chatMessages.value.findIndex(m => m.id === aiMessageId);
          if (errorIndex > -1) {
            chatMessages.value[errorIndex].content = `错误: ${data.message}`;
            chatMessages.value[errorIndex].isStreaming = false;
          }
          isStreaming.value = false;
          break;
          
        default:
          console.log('未知消息类型:', data.type);
      }
    });

    // 处理JSON块数据
    const handleJsonBlock = (block: any, messageId: string) => {
      console.log('处理JSON块:', block);
      
      const messageIndex = chatMessages.value.findIndex(m => m.id === messageId);
      if (messageIndex === -1) return;
      
      const blockType = block.type;
      const blockContent = block.content;
      
      // 根据块类型确定系统类型
      let systemType: 'brain' | 'code' | 'writing' = 'brain';
      let shouldUpdateSystem = false;
      
      switch (blockType) {
        case 'main':
          systemType = 'brain';
          break;
        case 'call_code_agent':
        case 'code_agent':
        case 'call_exec_py':
        case 'exec_py':
          systemType = 'code';
          shouldUpdateSystem = true;
          break;
        case 'call_writing_agent':
        case 'writing_agent':
          systemType = 'writing';
          shouldUpdateSystem = true;
          break;
        default:
          systemType = 'brain';
      }
      
      // 更新消息内容
      const currentMessage = chatMessages.value[messageIndex];
      let newContent = currentMessage.content;
      
      // 根据块类型格式化内容
      switch (blockType) {
        case 'main':
          newContent += blockContent;
          break;
        case 'call_code_agent':
          newContent += `\n\n🤖 **代码执行请求**:\n${blockContent}`;
          break;
        case 'code_agent':
          newContent += `\n\n💻 **代码执行**:\n${blockContent}`;
          break;
        case 'call_exec_py':
          newContent += `\n\n⚡ **执行代码**:\n\`\`\`python\n${blockContent}\n\`\`\``;
          break;
        case 'exec_py':
          newContent += `\n\n📊 **执行结果**:\n\`\`\`\n${blockContent}\n\`\`\``;
          break;
        case 'call_writing_agent':
          newContent += `\n\n✍️ **写作请求**:\n${blockContent}`;
          break;
        case 'writing_agent':
          newContent += `\n\n📝 **写作内容**:\n${blockContent}`;
          break;
        default:
          newContent += `\n\n${blockContent}`;
      }
      
      // 更新消息
      const updatedMessage = {
        ...currentMessage,
        content: newContent,
        systemType: shouldUpdateSystem ? systemType : currentMessage.systemType,
        avatar: shouldUpdateSystem ? getSystemAvatar({ systemType }) : currentMessage.avatar
      };
      
      chatMessages.value.splice(messageIndex, 1, updatedMessage);
      
      // 自动滚动到底部
      nextTick(() => {
        const chatContainer = document.querySelector('.chat-messages');
        if (chatContainer) {
          chatContainer.scrollTop = chatContainer.scrollHeight;
        }
      });
    };

    // 处理内容更新（兼容旧格式）
    const handleContentUpdate = (content: string, messageId: string) => {
      const messageIndex = chatMessages.value.findIndex(m => m.id === messageId);
      if (messageIndex > -1) {
        const currentMessage = chatMessages.value[messageIndex];
        const updatedMessage = {
          ...currentMessage,
          content: currentMessage.content + content
        };
        chatMessages.value.splice(messageIndex, 1, updatedMessage);
        
        // 自动滚动到底部
        nextTick(() => {
          const chatContainer = document.querySelector('.chat-messages');
          if (chatContainer) {
            chatContainer.scrollTop = chatContainer.scrollHeight;
          }
        });
      }
    };

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
  
  // 重置流式状态，确保输入框不被禁用
  isStreaming.value = false;
  
  // 清理blob URLs以释放内存
  Object.values(imageUrls.value).forEach(url => {
    if (url.startsWith('blob:')) {
      URL.revokeObjectURL(url);
    }
  });
  imageUrls.value = {};
});

// 监听路由变化
watch(() => route.params.work_id, (newWorkId) => {
  if (newWorkId) {
    // 重置流式状态，确保输入框不被禁用
    isStreaming.value = false;
    
    // 清理之前的WebSocket连接
    if (webSocketHandler.value) {
      webSocketHandler.value.disconnect();
      webSocketHandler.value = null;
    }
    
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
