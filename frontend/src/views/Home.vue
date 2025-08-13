<template>
  <div class="home-page">
    <div class="sidebar" :class="{ 'sidebar-collapsed': isSidebarCollapsed }">
      <div class="sidebar-header">
        <h2 v-if="!isSidebarCollapsed">PaperAgent</h2>
        <t-button 
          theme="default" 
          shape="square" 
          variant="text" 
          @click="toggleSidebar"
          class="sidebar-toggle-btn"
        >
          <t-icon :name="isSidebarCollapsed ? 'chevron-right' : 'chevron-left'" />
        </t-button>
      </div>
      
      <div class="sidebar-content" v-if="!isSidebarCollapsed">
        <div class="menu-section">
          <t-button theme="primary" block @click="createNewTask">
            新建工作
          </t-button>
        </div>
        
        <div class="menu-section">
          <div class="menu-title">
            <browse-icon />
            <span>历史工作</span>
          </div>
          <div class="history-list">
            <t-card 
              v-for="item in historyItems" 
              :key="item.id" 
              class="history-item"
              :class="{ 'active': activeHistoryId === item.id }"
              @click="selectHistory(item.id)"
            >
              <div class="history-item-content">
                <h4>{{ item.title }}</h4>
                <p>{{ item.date }}</p>
              </div>
            </t-card>
          </div>
        </div>
      </div>
      
      <div class="sidebar-footer" v-if="!isSidebarCollapsed">
        <t-dropdown :options="userOptions" placement="top-left" trigger="click">
          <div class="user-info">
            <t-avatar class="user-avatar" :image="userAvatar"></t-avatar>
            <span class="user-name">{{ userName }}</span>
          </div>
        </t-dropdown>
        
        <!-- API Key 设置弹窗 -->
        <t-dialog 
          v-model:visible="showApiKeyDialog" 
          header="API Key 设置"
          @confirm="saveApiKey"
          @cancel="cancelApiKey"
        >
          <t-form :data="apiKeyForm" @submit="saveApiKey">
            <t-form-item label="API Key" name="apiKey">
              <t-input v-model="apiKeyForm.apiKey" type="password" placeholder="请输入您的 API Key"></t-input>
            </t-form-item>
          </t-form>
        </t-dialog>
      </div>
    </div>
    
    <div class="main-content">
      <div class="workspace-header" v-if="activeHistoryId">
        <h1>{{ selectedHistory?.title }}</h1>
        <p>创建于 {{ selectedHistory?.date }}</p>
      </div>
      
      <div class="workspace-header" v-else>
        <h1>论文生成工作区</h1>
        <p>在这里开始您的论文生成任务</p>
      </div>
      
      <div class="workspace-content">
        <div class="chat-section">
          <div class="chat-container">
            <div class="chat-messages">
              <ChatItem
                v-for="message in chatMessages"
                :key="message.id"
                :role="message.role"
                :content="message.content"
                :datetime="message.datetime"
                :avatar="message.avatar"
                :actions="message.role === 'assistant' ? 'copy,replay' : undefined"
                @operation="(action) => {
                  if (action === 'copy') copyMessage(message.content)
                  if (action === 'replay') regenerateMessage(message.id)
                }"
              />
            </div>
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
          <div v-if="activeHistoryId">
            <t-card title="论文预览">
              <p>{{ selectedHistory?.content }}</p>
            </t-card>
          </div>
          
          <div v-else>
            <t-card title="论文预览">
              <p>与AI对话生成论文内容后，将在此处预览论文。</p>
            </t-card>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { BrowseIcon } from 'tdesign-icons-vue-next';
import { MessagePlugin } from 'tdesign-vue-next';
import { ChatItem, ChatSender } from '@tdesign-vue-next/chat';

// 侧边栏折叠状态
const isSidebarCollapsed = ref(false);

// 定义聊天消息类型
interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system'
  content: string
  datetime: string
  avatar: string
}

// 聊天消息数据
const chatMessages = ref<ChatMessage[]>([
  {
    id: '1',
    role: 'user',
    content: '你好，请帮我生成一篇关于人工智能在医疗领域应用的论文',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
  },
  {
    id: '2',
    role: 'assistant',
    content: '好的，我可以帮您生成一篇关于人工智能在医疗领域应用的论文。首先，我们可以探讨人工智能在医学影像诊断、疾病预测、个性化治疗等方面的应用。请问您希望论文重点关注哪个方面？',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
  }
])

// 输入框内容
const inputValue = ref('')

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
      content: `我理解您希望深入了解"${inputValue.value}"。在论文中，我们可以从以下几个角度来分析：1) 技术原理；2) 实际应用案例；3) 优势与挑战；4) 未来发展趋势。您希望我详细阐述哪个方面？`,
      datetime: new Date().toLocaleString(),
      avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
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
      message.content = '这是重新生成的内容。在论文生成过程中，我们可以根据不同的要点进行深入分析，确保论文内容的全面性和专业性。'
    }, 1000)
  }
}

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value;
};

// 用户信息
const userName = ref('用户123');
const userAvatar = ref(''); // 默认头像，如果为空则使用默认头像

// 新建工作
const createNewTask = () => {
  // 这里可以添加创建新任务的逻辑
  console.log('创建新任务');
  activeHistoryId.value = null; // 重置选中的历史工作，显示欢迎界面
};

// 历史工作数据
const historyItems = ref([
  {
    id: 1,
    title: '人工智能在医疗诊断中的应用',
    date: '2024-08-10 14:30',
    content: '这是一个关于人工智能在医疗领域应用的详细研究论文，探讨了AI技术如何改善医疗诊断的准确性和效率...'
  },
  {
    id: 2,
    title: '区块链技术在金融领域的创新',
    date: '2024-08-05 09:15',
    content: '本论文研究了区块链技术在金融行业中的各种创新应用，包括数字货币、智能合约和去中心化金融(DeFi)等...'
  },
  {
    id: 3,
    title: '可再生能源与可持续发展',
    date: '2024-07-28 16:45',
    content: '该论文分析了可再生能源技术的发展现状和未来趋势，以及它们对实现全球可持续发展目标的重要作用...'
  }
]);

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null);

// 计算属性：获取当前选中的历史工作详情
const selectedHistory = computed(() => {
  return historyItems.value.find(item => item.id === activeHistoryId.value);
});

// 选择历史工作
const selectHistory = (id: number) => {
  activeHistoryId.value = id;
};

// 用户菜单选项
const userOptions = [
  {
    content: 'API Key 设置',
    value: 'api-key',
    onClick: () => {
      showApiKeyDialog.value = true;
    }
  },
  {
    content: '退出登录',
    value: 'logout',
    onClick: () => {
      // 执行退出登录逻辑
      MessagePlugin.success('已退出登录');
    }
  }
];

// 控制API Key对话框显示
const showApiKeyDialog = ref(false);

// API Key表单数据
const apiKeyForm = ref({
  apiKey: ''
});

// 保存API Key
const saveApiKey = () => {
  // 这里可以添加保存API Key的逻辑
  console.log('保存API Key:', apiKeyForm.value.apiKey);
  showApiKeyDialog.value = false;
  MessagePlugin.success('API Key 保存成功');
};

// 取消API Key设置
const cancelApiKey = () => {
  showApiKeyDialog.value = false;
};
</script>

<style scoped>
.home-page {
  display: flex;
  min-height: 100vh;
  background: #f5f7fa;
}

.sidebar {
  width: 300px;
  background: white;
  border-right: 1px solid #eee;
  display: flex;
  flex-direction: column;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);
  transition: width 0.3s ease;
}

.sidebar-collapsed {
  width: 60px;
}

.sidebar-header {
  padding: 10px;
  border-bottom: 1px solid #eee;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sidebar-content {
  flex: 1;
  padding: 20px 15px;
  overflow-y: auto;
}

.menu-section {
  margin-bottom: 25px;
}

.menu-title {
  display: flex;
  align-items: center;
  font-weight: 500;
  color: #2c3e50;
  margin-bottom: 12px;
  padding: 5px 10px;
}

.menu-title .t-icon {
  margin-right: 8px;
}

.new-task-button {
    width: 100%;
    text-align: center;
    vertical-align: middle;
}

.history-list {
  max-height: 400px;
  overflow-y: auto;
}

.history-item {
  margin-bottom: 10px;
  cursor: pointer;
  transition: all 0.2s;
}

.history-item:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.history-item.active {
  border-left: 4px solid #3498db;
}

.history-item-content h4 {
  margin: 0 0 5px 0;
  font-size: 14px;
  color: #2c3e50;
}

.history-item-content p {
  margin: 0;
  font-size: 12px;
  color: #7f8c8d;
}

.sidebar-footer {
  padding: 20px;
  border-top: 1px solid #eee;
}

/* 用户信息样式 */
.user-info {
  display: flex;
  align-items: center;
  padding: 10px 15px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.user-info:hover {
  background-color: #f0f0f0;
}

.user-avatar {
  margin-right: 10px;
}

.user-name {
  font-size: 14px;
  color: #2c3e50;
}

/* 侧边栏折叠按钮 */
.sidebar-toggle-btn {
  margin-left: auto;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.workspace-header {
  padding: 15px 30px;
  background: white;
  border-bottom: 1px solid #eee;
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
}

.chat-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #eee;
  padding: 20px;
  min-width: 300px;
}

.preview-section {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  background: #f9f9f9;
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
}

.chat-messages {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  background: #fafafa;
}

.chat-input {
  padding: 16px;
  border-top: 1px solid #eee;
  background: white;
}
</style>
