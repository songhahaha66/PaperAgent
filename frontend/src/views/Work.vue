<template>
  <div class="work-page">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      :history-items="historyItems"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
    />
    
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
          
          <div v-else-if="activeHistoryId">
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
import Sidebar from '@/components/Sidebar.vue';
import FileManager from '@/components/FileManager.vue'; // 导入新的FileManager组件

const route = useRoute();
const workId = computed(() => route.params.work_id as string);

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
const fileContents: Record<string, string> = {
  'main.py': `import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import odeint

def cooling_model(T, t, params):
    """空调降温模型"""
    P, m, c, T_out = params
    dT_dt = P / (m * c) * (T_out - T)
    return dT_dt

# 参数设置
P = 12250  # 制冷功率 (W)
m = 336    # 空气质量 (kg)
c = 1005   # 比热容 (J/kg·K)
T_out = 30 # 室外温度 (℃)

# 时间序列
t = np.linspace(0, 3600, 100)  # 1小时
T0 = 30  # 初始温度

# 求解微分方程
solution = odeint(cooling_model, T0, t, args=([P, m, c, T_out],))

# 绘制结果
plt.figure(figsize=(10, 6))
plt.plot(t/60, solution, 'b-', linewidth=2)
plt.xlabel('时间 (分钟)')
plt.ylabel('温度 (℃)')
plt.title('空调降温曲线')
plt.grid(True)
plt.show()`,
  'requirements.txt': `numpy>=1.21.0
matplotlib>=3.5.0
scipy>=1.7.0
pandas>=1.3.0`,
  'data': `房间参数数据：
面积: 100 m²
层高: 2.8 m
体积: 280 m³
空气质量: 336 kg
初始温度: 30℃
目标温度: 25℃

空调参数数据：
制冷功率: 3500W
COP: 3.5
实际制冷量: 12250W`,
  'outline.md': `# 计算100平方的家庭使用空调降温速率研究

## 摘要
本研究通过建立数学模型和数值模拟，分析了100平方米家庭使用空调的降温速率。

## 1. 引言
- 研究背景
- 研究意义
- 研究目标

## 2. 数学模型
- 热传导方程
- 房间热平衡
- 空调制冷功率
- 降温速率计算

## 3. 数值模拟
- 参数设置
- 求解方法
- 结果分析

## 4. 结论
- 主要发现
- 应用价值
- 未来展望`,
  'sections': `## 1. 引言部分
研究背景：空调在现代家庭中的普及应用
研究意义：节能优化和舒适度提升
研究目标：建立准确的降温速率预测模型

## 2. 数学模型部分
热传导理论基础
能量守恒定律应用
参数敏感性分析

## 3. 数值模拟部分
Python编程实现
数值算法选择
结果可视化分析`,
  'output.log': `代码执行日志：
[INFO] 开始执行空调降温模型
[INFO] 参数设置完成
[INFO] 求解微分方程...
[INFO] 计算完成，降温时间：30分钟
[INFO] 生成图表成功`,
  'plots': `生成的图表文件：
1. cooling_curve.png - 降温曲线图
2. temperature_distribution.png - 温度分布图
3. power_consumption.png - 功率消耗图

图表说明：
- 降温曲线显示温度随时间的变化
- 温度分布图显示房间内温度梯度
- 功率消耗图显示空调工作状态`,
  'data_output': `数值计算结果：
时间序列: 0-3600秒 (0-60分钟)
温度数据: 30.0℃ → 25.0℃
降温速率: 平均0.17℃/分钟
峰值速率: 0.27℃/分钟 (前15分钟)

关键时间点：
- 5分钟: 28.2℃
- 10分钟: 26.8℃
- 15分钟: 25.9℃
- 30分钟: 25.0℃`,
  'final_paper.md': `# 计算100平方的家庭使用空调降温速率研究

## 摘要
本研究通过建立数学模型和数值模拟，分析了100平方米家庭使用空调的降温速率。结果表明，在标准条件下，房间温度从30℃降至25℃需要约30分钟，平均降温速率为0.17℃/分钟。

## 关键词
空调制冷、降温速率、热传导、数值模拟

## 1. 引言
随着生活水平的提高，空调已成为家庭必备设备。准确预测空调降温速率对节能和舒适度优化具有重要意义。

## 2. 数学模型
基于热传导理论和能量守恒定律，建立了房间降温的数学模型：

### 2.1 热传导方程
∂T/∂t = α∇²T

### 2.2 房间热平衡
Q = mcΔT

### 2.3 空调制冷功率
P = COP × Q

### 2.4 降温速率
dT/dt = P/(mc)

## 3. 数值模拟结果
通过Python数值计算，得到以下结果：
- 前15分钟降温最快，平均速率0.27℃/分钟
- 达到目标温度25℃需要约30分钟
- 降温过程符合指数衰减规律

## 4. 结论
本研究成功建立了空调降温的数学模型，并通过数值模拟验证了理论分析的正确性。`,
  'references': `参考文献：
1. 张三, 李四. 热传导理论在建筑节能中的应用[J]. 建筑科学, 2023, 39(5): 45-52.
2. Wang L, Smith J. HVAC System Optimization for Residential Buildings[J]. Energy and Buildings, 2023, 285: 112-125.
3. 王五, 赵六. 空调系统数值模拟方法研究[J]. 暖通空调, 2023, 53(3): 78-85.`,
  'images': `图片资源：
1. room_layout.png - 房间布局图
2. ac_unit.png - 空调设备图
3. temperature_flow.png - 温度流动图
4. energy_diagram.png - 能量平衡图`
}

// 处理文件选择
const handleFileSelect = (fileKey: string) => {
  selectedFile.value = fileKey
}

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

const router = useRouter();
const authStore = useAuthStore();

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

// 根据路由参数设置当前选中的历史工作
onMounted(() => {
  // 从路由参数获取work_id
  const currentWorkId = parseInt(workId.value);
  if (currentWorkId && !isNaN(currentWorkId)) {
    // 查找对应的历史工作
    const foundWork = historyItems.value.find(item => item.id === currentWorkId);
    if (foundWork) {
      activeHistoryId.value = currentWorkId;
    } else {
      // 如果没找到，创建一个新的工作
      const newWork = {
        id: currentWorkId,
        title: `新工作 ${currentWorkId}`,
        date: new Date().toLocaleString(),
        content: '这是一个新的论文生成工作。'
      };
      historyItems.value.unshift(newWork);
      activeHistoryId.value = currentWorkId;
    }
  }
});

// 监听路由变化
watch(() => route.params.work_id, (newWorkId) => {
  if (newWorkId) {
    const currentWorkId = parseInt(newWorkId as string);
    if (currentWorkId && !isNaN(currentWorkId)) {
      // 查找对应的历史工作
      const foundWork = historyItems.value.find(item => item.id === currentWorkId);
      if (foundWork) {
        activeHistoryId.value = currentWorkId;
      } else {
        // 如果没找到，创建一个新的工作
        const newWork = {
          id: currentWorkId,
          title: `新工作 ${currentWorkId}`,
          date: new Date().toLocaleString(),
          content: '这是一个新的论文生成工作。'
        };
        historyItems.value.unshift(newWork);
        activeHistoryId.value = currentWorkId;
      }
    }
  }
});

// 新建工作
const createNewTask = () => {
  // 这个方法现在由Sidebar组件直接处理路由跳转
};

// 选择历史工作
const selectHistory = (id: number) => {
  // 这个方法现在由Sidebar组件直接处理路由跳转
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
