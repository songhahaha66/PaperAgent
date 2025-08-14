<template>
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
          <div class="user-avatar">
            <span class="user-avatar-text">{{ userName.charAt(0).toUpperCase() }}</span>
          </div>
          <div class="user-details">
            <div class="user-name">{{ userName }}</div>
            <div class="user-email">{{ userEmail }}</div>
          </div>
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
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRouter } from 'vue-router';
import { BrowseIcon } from 'tdesign-icons-vue-next';
import { MessagePlugin } from 'tdesign-vue-next';
import { useAuthStore } from '@/stores/auth';

// 定义props
interface Props {
  isSidebarCollapsed: boolean
  activeHistoryId: number | null
  historyItems: Array<{
    id: number
    title: string
    date: string
    content: string
  }>
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
const selectHistory = (id: number) => {
  // 跳转到对应的工作页面
  router.push(`/work/${id}`);
};

// 用户信息
const userName = computed(() => authStore.currentUser?.username || '用户');
const userEmail = computed(() => authStore.currentUser?.email || '');
const userAvatar = ref(''); // 默认头像，如果为空则使用默认头像

// 历史工作数据
const historyItems = computed(() => props.historyItems);

// 当前选中的历史工作ID
const activeHistoryId = computed(() => props.activeHistoryId);

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
</style>
