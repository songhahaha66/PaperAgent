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
          <h1>{{ currentWork.title }}</h1>
          <p>创建于 {{ formatDate(currentWork.created_at) }}</p>
          <div class="work-status">
            <t-tag :theme="getStatusTheme(currentWork.status)" variant="light">
              {{ getStatusText(currentWork.status) }}
            </t-tag>
            <t-progress 
              :percentage="currentWork.progress" 
              :color="getProgressColor(currentWork.progress)"
              size="small"
              style="width: 120px; margin-left: 16px;"
            />
          </div>
        </div>
        <div class="work-actions">
          <t-button theme="default" variant="outline" size="small" @click="refreshWork">
            <template #icon>
              <t-icon name="refresh" />
            </template>
            刷新
          </t-button>
          <t-button theme="danger" variant="outline" size="small" @click="deleteWork">
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
                  :actions="message.role === 'assistant' ? 'copy,replay' : undefined"
                  @operation="(action) => {
                    if (action === 'copy') copyMessage(message.content)
                    if (action === 'replay') regenerateMessage(message.id)
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
                <p><strong>进度：</strong>{{ currentWork.progress }}%</p>
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
import { ref, computed, onMounted, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { MessagePlugin } from 'tdesign-vue-next';
import { ChatItem, ChatSender } from '@tdesign-vue-next/chat';
import { Tree, Collapse, CollapsePanel } from 'tdesign-vue-next';
import { useAuthStore } from '@/stores/auth';
import { workspaceAPI, workspaceFileAPI, type Work, type FileInfo } from '@/api/workspace';
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

// 定义聊天消息类型
interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system'
  content: string
  datetime: string
  avatar: string
  systemType?: 'central' | 'code' | 'paper' // 系统类型：中枢、代码执行、论文生成
}

// 聊天消息数据
const chatMessages = ref<ChatMessage[]>([]);

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
    
  } catch (error) {
    console.error('加载工作信息失败:', error);
    MessagePlugin.error('加载工作信息失败');
  } finally {
    loading.value = false;
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
const getSystemAvatar = (message: ChatMessage) => {
  if (message.systemType) {
    const systemAvatars = {
      central: 'https://api.dicebear.com/7.x/bottts/svg?seed=central&backgroundColor=0052d9', // 中枢系统头像 - 蓝色机器人
      code: 'https://api.dicebear.com/7.x/bottts/svg?seed=code&backgroundColor=00a870',        // 代码执行系统头像 - 绿色机器人
      paper: 'https://api.dicebear.com/7.x/bottts/svg?seed=paper&backgroundColor=ed7b2f'       // 论文生成系统头像 - 橙色机器人
    }
    return systemAvatars[message.systemType]
  }
  return message.avatar
}

// 获取系统名称
const getSystemName = (message: ChatMessage) => {
  if (message.systemType) {
    const systemNames = {
      central: '中枢系统',
      code: '代码执行',
      paper: '论文生成'
    }
    return systemNames[message.systemType]
  }
  return 'AI助手'
}

// 发送消息
const sendMessage = () => {
  if (!inputValue.value.trim()) return
  
  const newMessage: ChatMessage = {
    id: Date.now().toString(),
    role: 'user',
    content: inputValue.value,
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
  }
  
  chatMessages.value.push(newMessage)
  
  // 模拟AI回复
  setTimeout(() => {
    const aiReply: ChatMessage = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: `我理解您希望深入了解"${inputValue.value}"。在空调降温速率研究中，我们可以从以下几个角度来分析：1) 热传导模型建立；2) 参数分析与计算；3) 数值模拟编程；4) 结果验证与优化；5) 论文撰写与格式规范。您希望我详细阐述哪个方面？`,
      datetime: new Date().toLocaleString(),
      avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
      systemType: 'central'
    }
    chatMessages.value.push(aiReply)
  }, 1000)
  
  inputValue.value = ''
}

// 复制消息
const copyMessage = (content: string) => {
  navigator.clipboard.writeText(content)
  MessagePlugin.success('消息已复制到剪贴板！')
}

// 重新生成消息
const regenerateMessage = (messageId: string) => {
  const message = chatMessages.value.find(m => m.id === messageId)
  if (message && message.role === 'assistant') {
    message.content = '正在重新生成回复...'
    setTimeout(() => {
      message.content = '这是重新生成的内容。在空调降温速率研究过程中，我们可以根据不同的要点进行深入分析，包括热传导模型优化、参数敏感性分析、数值算法改进等，确保研究内容的科学性和准确性。'
    }, 1000)
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

// 获取进度颜色
const getProgressColor = (progress: number) => {
  if (progress < 30) return '#ff4d4f';
  if (progress < 70) return '#faad14';
  return '#52c41a';
};

// 监听路由变化
watch(() => route.params.work_id, (newWorkId) => {
  if (newWorkId) {
    loadWork();
  }
});

// 组件挂载时加载工作信息
onMounted(() => {
  if (workId.value) {
    loadWork();
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

.work-status {
  display: flex;
  align-items: center;
  margin-top: 8px;
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

.system-label.central {
  background: rgba(0, 82, 217, 0.1);
  color: #0052d9;
  border: 1px solid rgba(0, 82, 217, 0.2);
}

.system-label.code {
  background: rgba(0, 168, 112, 0.1);
  color: #00a870;
  border: 1px solid rgba(0, 168, 112, 0.2);
}

.system-label.paper {
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
