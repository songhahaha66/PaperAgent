<template>
  <div class="home-page">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      :history-items="historyItems"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
    />
    
    <div class="home-container">
      <div class="welcome-section">
        <h1>欢迎使用论文Agent</h1>
        <p>智能论文生成助手，让学术写作更高效</p>
      </div>
      
      <div class="action-section">
        <t-card title="快速开始" class="action-card">
          <div class="action-buttons">
            <t-button 
              theme="primary" 
              size="large" 
              @click="createNewWork"
              class="action-button"
            >
              <template #icon>
                <t-icon name="add" />
              </template>
              创建新工作
            </t-button>
            
            <t-button 
              theme="default" 
              size="large" 
              @click="viewHistory"
              class="action-button"
            >
              <template #icon>
                <t-icon name="time" />
              </template>
              查看历史工作
            </t-button>
          </div>
        </t-card>
      </div>
      
      <div class="features-section">
        <t-row :gutter="[16, 16]">
          <t-col :span="8">
            <t-card title="智能建模" class="feature-card">
              <template #icon>
                <t-icon name="chart" theme="primary" />
              </template>
              <p>基于AI的智能建模系统，自动分析研究问题并建立数学模型</p>
            </t-card>
          </t-col>
          
          <t-col :span="8">
            <t-card title="代码执行" class="feature-card">
              <template #icon>
                <t-icon name="code" theme="success" />
              </template>
              <p>自动生成并执行Python代码，进行数值模拟和数据分析</p>
            </t-card>
          </t-col>
          
          <t-col :span="8">
            <t-card title="论文生成" class="feature-card">
              <template #icon>
                <t-icon name="file" theme="warning" />
              </template>
              <p>智能生成结构化的学术论文，包含摘要、引言、方法等完整章节</p>
            </t-card>
          </t-col>
        </t-row>
      </div>
      
      <div class="recent-section" v-if="recentWorks.length > 0">
        <t-card title="最近工作" class="recent-card">
          <t-list>
            <t-list-item 
              v-for="work in recentWorks" 
              :key="work.id"
              @click="openWork(work.id)"
              class="recent-work-item"
            >
              <template #content>
                <div class="work-info">
                  <h4>{{ work.title }}</h4>
                  <p>{{ work.date }}</p>
                </div>
              </template>
              <template #action>
                <t-button theme="primary" variant="text" size="small">
                  继续工作
                </t-button>
              </template>
            </t-list-item>
          </t-list>
        </t-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import Sidebar from '@/components/Sidebar.vue';

const router = useRouter();
const authStore = useAuthStore();

// 侧边栏折叠状态
const isSidebarCollapsed = ref(false);

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

// 最近工作数据
const recentWorks = ref([
  {
    id: 1,
    title: '计算100平方的家庭使用空调降温速率研究',
    date: '2024-08-10 14:30'
  },
  {
    id: 2,
    title: '区块链技术在金融领域的创新',
    date: '2024-08-05 09:15'
  },
  {
    id: 3,
    title: '可再生能源与可持续发展',
    date: '2024-07-28 16:45'
  }
]);

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value;
};

// 创建新工作
const createNewWork = () => {
  // 生成新的工作ID（这里简单使用时间戳）
  const newWorkId = Date.now();
  router.push(`/work/${newWorkId}`);
};

// 查看历史工作
const viewHistory = () => {
  // 跳转到第一个历史工作
  if (recentWorks.value.length > 0) {
    router.push(`/work/${recentWorks.value[0].id}`);
  }
};

// 打开特定工作
const openWork = (workId: number) => {
  router.push(`/work/${workId}`);
};

// 新建工作（侧边栏调用）
const createNewTask = () => {
  // 这个方法现在由Sidebar组件直接处理路由跳转
  console.log('创建新任务');
};

// 选择历史工作（侧边栏调用）
const selectHistory = (id: number) => {
  // 这个方法现在由Sidebar组件直接处理路由跳转
  console.log('选择历史工作:', id);
};

// 检查用户认证状态
onMounted(() => {
  // 路由守卫已经处理认证检查
});
</script>

<style scoped>
.home-page {
  display: flex;
  height: 100vh;
  width: 100vw;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  overflow: hidden;
}

.home-container {
  flex: 1;
  padding: 40px 20px;
  overflow-y: auto;
}

.welcome-section {
  text-align: center;
  margin-bottom: 60px;
}

.welcome-section h1 {
  font-size: 3rem;
  color: #2c3e50;
  margin-bottom: 20px;
  font-weight: 700;
}

.welcome-section p {
  font-size: 1.2rem;
  color: #7f8c8d;
  margin: 0;
}

.action-section {
  margin-bottom: 60px;
}

.action-card {
  text-align: center;
}

.action-buttons {
  display: flex;
  gap: 20px;
  justify-content: center;
  flex-wrap: wrap;
}

.action-button {
  min-width: 160px;
}

.features-section {
  margin-bottom: 60px;
}

.feature-card {
  height: 100%;
  text-align: center;
}

.feature-card .t-card__header {
  justify-content: center;
}

.feature-card .t-icon {
  font-size: 2rem;
  margin-bottom: 16px;
}

.feature-card p {
  color: #666;
  line-height: 1.6;
  margin: 0;
}

.recent-section {
  margin-bottom: 40px;
}

.recent-card {
  max-height: 400px;
  overflow-y: auto;
}

.recent-work-item {
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.recent-work-item:hover {
  background-color: #f8f9fa;
}

.work-info h4 {
  margin: 0 0 8px 0;
  color: #2c3e50;
  font-size: 1rem;
}

.work-info p {
  margin: 0;
  color: #7f8c8d;
  font-size: 0.9rem;
}

@media (max-width: 768px) {
  .welcome-section h1 {
    font-size: 2rem;
  }
  
  .action-buttons {
    flex-direction: column;
    align-items: center;
  }
  
  .action-button {
    width: 100%;
    max-width: 300px;
  }
}
</style>
