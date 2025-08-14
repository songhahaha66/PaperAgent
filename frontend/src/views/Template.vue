<template>
  <div class="template-page">
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
      </div>
    </div>
    
    <div class="main-content">
      <div class="workspace-header">
        <h1>我的模板</h1>
        <p>管理和配置您的论文模板</p>
      </div>
      
      <div class="template-content">
        <t-card title="模板列表">
          <template #actions>
            <t-button theme="primary" @click="showCreateTemplateDialog = true">
              <add-icon />新建模板
            </t-button>
          </template>
          
          <t-table
            :data="templateList"
            :columns="columns"
            row-key="id"
            :pagination="pagination"
            @page-change="onPageChange"
          >
            <template #operation="{ row }">
              <t-space>
                <t-button theme="primary" variant="text" @click="editTemplate(row)">
                  编辑
                </t-button>
                <t-button theme="default" variant="text" @click="downloadTemplate(row)">
                  下载
                </t-button>
                <t-button theme="danger" variant="text" @click="deleteTemplate(row)">
                  删除
                </t-button>
              </t-space>
            </template>
          </t-table>
        </t-card>
      </div>
    </div>
    
    <!-- 新建/编辑模板对话框 -->
    <t-dialog
      v-model:visible="showCreateTemplateDialog"
      :header="editingTemplate ? '编辑模板' : '新建模板'"
      @confirm="saveTemplate"
      @cancel="cancelTemplate"
      width="600px"
    >
      <t-form :data="templateForm" label-align="top">
        <t-form-item label="模板名称" name="name">
          <t-input v-model="templateForm.name" placeholder="请输入模板名称" />
        </t-form-item>
        <t-form-item label="模板描述" name="description">
          <t-textarea v-model="templateForm.description" placeholder="请输入模板描述" />
        </t-form-item>
        <t-form-item label="模板文件" name="file">
          <t-upload
            v-model="templateForm.file"
            action="/upload"
            :auto-upload="false"
            :max="1"
            accept=".tex"
            @change="onFileChange"
            tips="仅支持 .tex 文件"
            v-if="!editingTemplate"
          >
            <t-button variant="outline">选择文件</t-button>
          </t-upload>
          <div v-else>
            <p>当前文件: {{ editingTemplate.fileName }}</p>
            <t-upload
              action="/upload"
              :auto-upload="false"
              :max="1"
              accept=".tex"
              @change="onFileChange"
              tips="选择新文件以替换当前文件"
            >
              <t-button variant="outline">替换文件</t-button>
            </t-upload>
          </div>
        </t-form-item>
      </t-form>
    </t-dialog>
    
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
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { AddIcon, BrowseIcon } from 'tdesign-icons-vue-next';
import { MessagePlugin } from 'tdesign-vue-next';
import { useAuthStore } from '@/stores/auth';

// 侧边栏折叠状态
const isSidebarCollapsed = ref(false);

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value;
};

const router = useRouter();
const authStore = useAuthStore();

// 用户信息
const userName = computed(() => authStore.currentUser?.username || '用户');
const userAvatar = ref(''); // 默认头像，如果为空则使用默认头像

// 新建工作
const createNewTask = () => {
  // 这里可以添加创建新任务的逻辑
  console.log('创建新任务');
  router.push('/home');
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

// 选择历史工作
const selectHistory = (id: number) => {
  router.push('/home');
  // 在主页中实现选中历史工作的逻辑
};

// 用户菜单选项
const userOptions = [
  {
    content: '我的模板',
    value: 'template',
    onClick: () => {
      // 当前已在模板页面
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

interface TemplateItem {
  id: number;
  name: string;
  description: string;
  fileName: string;
  fileSize: string;
  createdAt: string;
}

// 模板列表数据
const templateList = ref<TemplateItem[]>([
  {
    id: 1,
    name: '学术论文模板',
    description: '标准学术论文格式模板',
    fileName: 'academic-template.tex',
    fileSize: '2.4 KB',
    createdAt: '2024-08-10'
  },
  {
    id: 2,
    name: '技术报告模板',
    description: '技术研究报告格式模板',
    fileName: 'tech-report.tex',
    fileSize: '1.8 KB',
    createdAt: '2024-08-05'
  }
]);

// 表格列配置
const columns = ref([
  { colKey: 'name', title: '模板名称', width: '150px' },
  { colKey: 'description', title: '描述', width: '200px' },
  { colKey: 'fileName', title: '文件名', width: '150px' },
  { colKey: 'fileSize', title: '大小', width: '100px' },
  { colKey: 'createdAt', title: '创建时间', width: '150px' },
  { colKey: 'operation', title: '操作', width: '200px' }
]);

// 分页配置
const pagination = reactive({
  defaultCurrent: 1,
  defaultPageSize: 10,
  total: 2,
});

// 分页变化处理
const onPageChange = (curr: number, pageInfo: { current: number; pageSize: number }) => {
  console.log('分页变化', curr, pageInfo);
};

// 控制新建模板对话框显示
const showCreateTemplateDialog = ref(false);

// 正在编辑的模板
const editingTemplate = ref<TemplateItem | null>(null);

// 模板表单数据
const templateForm = reactive({
  name: '',
  description: '',
  file: [] as Array<any>
});

// 控制API Key对话框显示
const showApiKeyDialog = ref(false);

// API Key表单数据
const apiKeyForm = ref({
  apiKey: ''
});

// 文件变更处理
const onFileChange = (fileList: Array<any>) => {
  templateForm.file = fileList;
};

// 编辑模板
const editTemplate = (template: TemplateItem) => {
  editingTemplate.value = template;
  templateForm.name = template.name;
  templateForm.description = template.description;
  templateForm.file = [];
  showCreateTemplateDialog.value = true;
};

// 删除模板
const deleteTemplate = (template: TemplateItem) => {
  const index = templateList.value.findIndex(t => t.id === template.id);
  if (index !== -1) {
    templateList.value.splice(index, 1);
    MessagePlugin.success('模板删除成功');
  }
};

// 下载模板
const downloadTemplate = (template: TemplateItem) => {
  MessagePlugin.info(`开始下载模板: ${template.fileName}`);
  // 实际项目中这里会实现文件下载逻辑
};

// 保存模板
const saveTemplate = () => {
  if (!templateForm.name) {
    MessagePlugin.warning('请输入模板名称');
    return;
  }

  // 检查是否选择了文件（新建时）
  if (!editingTemplate.value && templateForm.file.length === 0) {
    MessagePlugin.warning('请选择模板文件');
    return;
  }

  if (editingTemplate.value) {
    // 更新模板
    const index = templateList.value.findIndex(t => t.id === editingTemplate.value!.id);
    if (index !== -1) {
      templateList.value[index] = {
        ...templateList.value[index],
        name: templateForm.name,
        description: templateForm.description
      };
      MessagePlugin.success('模板更新成功');
    }
  } else {
    // 新建模板
    const newTemplate: TemplateItem = {
      id: Date.now(),
      name: templateForm.name,
      description: templateForm.description,
      fileName: templateForm.file[0]?.name || 'template.tex',
      fileSize: templateForm.file[0]?.raw?.size ? `${(templateForm.file[0].raw.size / 1024).toFixed(1)} KB` : '0 KB',
      createdAt: new Date().toISOString().split('T')[0]
    };
    templateList.value.push(newTemplate);
    MessagePlugin.success('模板创建成功');
  }

  // 重置表单和状态
  cancelTemplate();
};

// 取消编辑/新建模板
const cancelTemplate = () => {
  showCreateTemplateDialog.value = false;
  editingTemplate.value = null;
  templateForm.name = '';
  templateForm.description = '';
  templateForm.file = [];
};

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

// 检查用户认证状态
onMounted(() => {
  // 移除重复的认证检查，路由守卫已经处理
  // 这里可以添加其他初始化逻辑
});
</script>

<style scoped>
.template-page {
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

.template-content {
  flex: 1;
  padding: 20px 30px;
  overflow-y: auto;
}

.t-card {
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}
</style>