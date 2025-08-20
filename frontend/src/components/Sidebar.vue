<template>
  <div class="sidebar" :class="{ 'sidebar-collapsed': isSidebarCollapsed }">
    <div class="sidebar-header">
      <div v-if="!isSidebarCollapsed" class="header-title">
        <h2>PaperAgent</h2>
        <img src="/logo.png" alt="PaperAgent Logo" class="header-logo" />
      </div>
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
          <!-- 加载状态 -->
          <div v-if="loadingHistory" class="loading-state">
            <t-loading size="small" />
            <p>加载中...</p>
          </div>
          
          <!-- 历史工作列表 -->
          <template v-else-if="historyItems.length > 0">
            <t-card 
              v-for="item in historyItems" 
              :key="item.id" 
              class="history-item"
              :class="{ 'active': activeHistoryId === item.id }"
              @click="selectHistory(item)"
            >
              <div class="history-item-content">
                <div class="history-header">
                  <h4>{{ item.title }}</h4>
                  <t-tag 
                    v-if="item.status" 
                    :theme="getStatusTheme(item.status)" 
                    variant="light" 
                    size="small"
                  >
                    {{ getStatusText(item.status) }}
                  </t-tag>
                </div>
                <p class="history-date">{{ item.date }}</p>
                <p class="history-content">{{ item.content }}</p>
              </div>
            </t-card>
          </template>
          
          <!-- 无历史记录时显示空状态 -->
          <template v-else>
            <div class="empty-history">
              <t-icon name="browse" class="empty-icon" />
              <p class="empty-text">暂无历史工作</p>
              <p class="empty-hint">点击"新建工作"开始您的第一个项目</p>
            </div>
          </template>
        </div>
      </div>
    </div>
    
    <div class="sidebar-footer" v-if="!isSidebarCollapsed">
      <t-dropdown :options="userOptions" placement="top-left" trigger="click">
        <div class="user-info">
          <div class="user-avatar">
            <span class="user-avatar-text">{{ userName.charAt(0).toUpperCase() }}</span>
          </div>
          <div class="user-details">
            <div class="user-name">{{ userName }}</div>
            <div class="user-email">{{ userEmail }}</div>
          </div>
        </div>
      </t-dropdown>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { BrowseIcon } from 'tdesign-icons-vue-next';
import { MessagePlugin } from 'tdesign-vue-next';
import { useAuthStore } from '@/stores/auth';
import { workspaceAPI } from '@/api/workspace';

// 定义props
interface Props {
  isSidebarCollapsed: boolean
  activeHistoryId: number | null
}

// 定义emits
interface Emits {
  (e: 'toggle-sidebar'): void
  (e: 'create-new-task'): void
  (e: 'select-history', id: number): void
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

const router = useRouter();
const authStore = useAuthStore();

// 历史工作数据类型定义
interface HistoryItem {
  id: number
  work_id?: string
  title: string
  date: string
  content: string
  status?: string
}

// 历史工作数据状态
const historyItems = ref<HistoryItem[]>([]);
const loadingHistory = ref(false);

// 侧边栏折叠状态
const isSidebarCollapsed = computed(() => props.isSidebarCollapsed);

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  emit('toggle-sidebar');
};

// 新建工作
const createNewTask = () => {
  // 跳转到home页面
  router.push('/home');
};

// 选择历史工作
const selectHistory = (item: any) => {
  // 通知父组件选中状态变化
  emit('select-history', item.id);
  
  if (item.work_id) {
    // 跳转到对应的工作页面
    router.push(`/work/${item.work_id}`);
  } else {
    // 如果没有work_id，使用id
    router.push(`/work/${item.id}`);
  }
};

// 用户信息
const userName = computed(() => authStore.currentUser?.username || '用户');
const userEmail = computed(() => authStore.currentUser?.email || '');
const userAvatar = ref(''); // 默认头像，如果为空则使用默认头像

// 加载用户工作列表
const loadUserWorks = async () => {
  if (!authStore.token) return;
  
  loadingHistory.value = true;
  try {
    const worksResponse = await workspaceAPI.getWorks(authStore.token, 0, 10);
    
    // 转换工作数据为历史记录格式
    historyItems.value = worksResponse.works.map(work => ({
      id: work.id,
      work_id: work.work_id,
      title: work.title,
      date: new Date(work.created_at).toLocaleString(),
      content: work.description || '暂无描述',
      status: work.status
    }));
  } catch (error) {
    console.error('加载工作列表失败:', error);
    // 清空历史记录，显示空状态
    historyItems.value = [];
  } finally {
    loadingHistory.value = false;
  }
};

// 当前选中的历史工作ID
const activeHistoryId = computed(() => props.activeHistoryId);

// 获取状态主题
const getStatusTheme = (status?: string) => {
  if (!status) return 'default';
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
const getStatusText = (status?: string) => {
  if (!status) return '';
  const texts: Record<string, string> = {
    'created': '已创建',
    'in_progress': '进行中',
    'completed': '已完成',
    'paused': '已暂停',
    'cancelled': '已取消'
  };
  return texts[status] || status;
};



// 组件挂载时加载历史工作数据
onMounted(() => {
  loadUserWorks();
  
  // 监听工作标题更新事件，刷新侧边栏工作列表
  window.addEventListener('work-title-updated', (event: any) => {
    const { workId, newTitle } = event.detail;
    
    // 找到对应的工作项并更新标题
    const workItem = historyItems.value.find(item => item.work_id === workId);
    if (workItem) {
      workItem.title = newTitle;
      console.log('侧边栏工作标题已更新:', newTitle);
    }
    
      // 也可以选择重新加载整个工作列表
  // loadUserWorks();
});

// 组件卸载时清理事件监听器
onUnmounted(() => {
  window.removeEventListener('work-title-updated', (event: any) => {
    const { workId, newTitle } = event.detail;
    
    // 找到对应的工作项并更新标题
    const workItem = historyItems.value.find(item => item.work_id === workId);
    if (workItem) {
      workItem.title = newTitle;
      console.log('侧边栏工作标题已更新:', newTitle);
    }
  });
});
});

// 用户菜单选项
const userOptions = [
  {
    content: '我的模板',
    value: 'template',
    onClick: () => {
      router.push('/template');
    }
  },
  {
    content: 'API Key 配置',
    value: 'api-key',
    onClick: () => {
      router.push('/api-config');
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
</script>

<style scoped>
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

.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-logo {
  width: 24px;
  height: 24px;
  object-fit: contain;
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
  overflow: hidden;
}

.history-item {
  margin-bottom: 10px;
  cursor: pointer;
  transition: all 0.2s;
  overflow: hidden;
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

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 5px;
}

.history-header h4 {
  flex: 1;
  margin-right: 8px;
  line-height: 1.2;
}

.history-date {
  margin: 5px 0;
  font-size: 11px;
  color: #999;
}



.history-content {
  margin: 8px 0 0 0;
  font-size: 11px;
  color: #666;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
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
  width: 40px;
  height: 40px;
  background-color: #3498db;
  border-radius: 50%;
  display: flex;
  justify-content: center;
  align-items: center;
  margin-right: 15px;
}

.user-avatar-text {
  color: white;
  font-size: 18px;
  font-weight: bold;
}

.user-details {
  display: flex;
  flex-direction: column;
}

.user-name {
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
}

.user-email {
  font-size: 12px;
  color: #7f8c8d;
}

/* 侧边栏折叠按钮 */
.sidebar-toggle-btn {
  margin-left: auto;
}

/* 加载状态样式 */
.loading-state {
  text-align: center;
  padding: 40px 20px;
  color: #7f8c8d;
}

.loading-state p {
  margin-top: 16px;
  color: #7f8c8d;
}

/* 空状态样式 */
.empty-history {
  text-align: center;
  padding: 40px 20px;
  color: #7f8c8d;
}

.empty-icon {
  font-size: 48px;
  color: #c0c4cc;
  margin-bottom: 16px;
}

.empty-text {
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
  margin: 0 0 8px 0;
}

.empty-hint {
  font-size: 14px;
  color: #7f8c8d;
  margin: 0;
  line-height: 1.4;
}
</style>
