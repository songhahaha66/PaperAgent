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
                    @mouseleave="hideDivider(index)"
                  >
                    <t-icon name="arrow-up" />
                  </div>
                </div>
              </div>
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
              </div>
            </t-card>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { BrowseIcon } from 'tdesign-icons-vue-next';
import { MessagePlugin } from 'tdesign-vue-next';
import { ChatItem, ChatSender } from '@tdesign-vue-next/chat';
import { useAuthStore } from '@/stores/auth';

// 侧边栏折叠状态
const isSidebarCollapsed = ref(false);

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
const chatMessages = ref<ChatMessage[]>([
  {
    id: '1',
    role: 'user',
    content: '你好，请帮我生成一篇关于《计算100平方的家庭使用空调降温速率研究》的论文',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg'
  },
  {
    id: '2',
    role: 'assistant',
    content: '好的，我将帮您生成一篇关于《计算100平方的家庭使用空调降温速率研究》的论文。让我先进行建模分析。',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
    systemType: 'central'
  },
  {
    id: '3',
    role: 'assistant',
    content: '我正在建立空调降温的数学模型：\n\n1. 热传导方程：∂T/∂t = α∇²T\n2. 房间热平衡：Q = mcΔT\n3. 空调制冷功率：P = COP × Q\n4. 降温速率：dT/dt = P/(mc)\n\n其中：\n- T: 温度 (℃)\n- t: 时间 (s)\n- α: 热扩散系数\n- m: 空气质量 (kg)\n- c: 比热容 (J/kg·K)\n- COP: 制冷系数\n- P: 制冷功率 (W)',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
    systemType: 'central'
  },
  {
    id: '4',
    role: 'assistant',
    content: '基于100平方米房间的参数分析：\n\n房间参数：\n- 面积：100 m²\n- 层高：2.8 m\n- 体积：280 m³\n- 空气质量：约336 kg\n- 初始温度：30℃\n- 目标温度：25℃\n\n空调参数：\n- 制冷功率：3500W\n- COP：3.5\n- 实际制冷量：12250W\n\n理论降温时间：约15分钟',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
    systemType: 'central'
  },
  {
    id: '5',
    role: 'assistant',
    content: '正在编写Python代码进行数值模拟：\n\n```python\nimport numpy as np\nimport matplotlib.pyplot as plt\nfrom scipy.integrate import odeint\n\ndef cooling_model(T, t, params):\n    """空调降温模型"""\n    P, m, c, T_out = params\n    dT_dt = P / (m * c) * (T_out - T)\n    return dT_dt\n\n# 参数设置\nP = 12250  # 制冷功率 (W)\nm = 336    # 空气质量 (kg)\nc = 1005   # 比热容 (J/kg·K)\nT_out = 30 # 室外温度 (℃)\n\n# 时间序列\nt = np.linspace(0, 3600, 100)  # 1小时\nT0 = 30  # 初始温度\n\n# 求解微分方程\nsolution = odeint(cooling_model, T0, t, args=([P, m, c, T_out],))\n```',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
    systemType: 'code'
  },
  {
    id: '6',
    role: 'assistant',
    content: '代码执行结果：\n\n温度变化曲线：\n- 0分钟：30.0℃\n- 5分钟：28.2℃\n- 10分钟：26.8℃\n- 15分钟：25.9℃\n- 20分钟：25.3℃\n- 30分钟：25.0℃\n\n关键发现：\n1. 前15分钟降温最快，平均速率0.27℃/分钟\n2. 达到目标温度25℃需要约30分钟\n3. 降温过程符合指数衰减规律\n4. 实际效果受房间密封性、热源等因素影响',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
    systemType: 'code'
  },
  {
    id: '7',
    role: 'assistant',
    content: '正在生成论文内容：\n\n**摘要**\n本研究通过建立数学模型和数值模拟，分析了100平方米家庭使用空调的降温速率。结果表明，在标准条件下，房间温度从30℃降至25℃需要约30分钟，平均降温速率为0.17℃/分钟。\n\n**关键词**：空调制冷、降温速率、热传导、数值模拟\n\n**1. 引言**\n随着生活水平的提高，空调已成为家庭必备设备。准确预测空调降温速率对节能和舒适度优化具有重要意义。\n\n**2. 数学模型**\n基于热传导理论和能量守恒定律，建立了房间降温的数学模型...',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
    systemType: 'paper'
  }
])

// 输入框内容
const inputValue = ref('')

// 分割线悬停状态
const hoveredDivider = ref<number | null>(null)

// 显示分割线
const showDivider = (index: number) => {
  hoveredDivider.value = index
}

// 隐藏分割线
const hideDivider = (index: number) => {
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

const router = useRouter();
const authStore = useAuthStore();

// 用户信息
const userName = computed(() => authStore.currentUser?.username || '用户');
const userAvatar = ref(''); // 默认头像，如果为空则使用默认头像

// 检查用户认证状态
onMounted(() => {
  // 移除重复的认证检查，路由守卫已经处理
  // 这里可以添加其他初始化逻辑
});

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
    title: '计算100平方的家庭使用空调降温速率研究',
    date: '2024-08-10 14:30',
    content: '本研究通过建立数学模型和数值模拟，分析了100平方米家庭使用空调的降温速率。结果表明，在标准条件下，房间温度从30℃降至25℃需要约30分钟，平均降温速率为0.17℃/分钟。研究包括建模过程、分析过程、编程过程、运行过程和论文写作过程。'
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
      authStore.logout();
      MessagePlugin.success('已退出登录');
      router.push('/login');
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
.home-page {
  display: flex;
  height: 100vh;
  width: 100vw;
  background: #f5f7fa;
  overflow: hidden;
}

.sidebar {
  width: 300px;
  background: white;
  border-right: 1px solid #eee;
  display: flex;
  flex-direction: column;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);
  transition: width 0.3s ease;
  overflow: hidden;
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
  min-height: 0;
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
  overflow: hidden;
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
</style>
