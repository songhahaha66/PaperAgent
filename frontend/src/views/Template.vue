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
              新建模板
            </t-button>
          </template>
          
          <t-table
            :data="templateList"
            :columns="columns"
            row-key="id"
            :pagination="pagination"
            @page-change="onPageChange"
            :loading="loading"
          >
            <template #operation="{ row }">
              <t-space>
                <t-button theme="primary" variant="text" @click="editTemplate(row)">
                  编辑
                </t-button>
                <t-button theme="default" variant="text" @click="viewTemplateFiles(row)">
                  查看文件
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
        <t-form-item label="模板分类" name="category">
          <t-input v-model="templateForm.category" placeholder="请输入模板分类" />
        </t-form-item>
        <t-form-item label="是否公开" name="is_public">
          <t-switch v-model="templateForm.is_public" />
        </t-form-item>
        <t-form-item label="模板文件" name="file" v-if="!editingTemplate">
          <t-upload
            v-model="templateFormFileList"
            action="#"
            :auto-upload="false"
            :max="1"
            accept=".tex,.md,.txt"
            @change="onFileChange"
            :show-upload-progress="false"
            :draggable="false"
            tips="支持 .tex, .md, .txt 文件"
          >
            <t-button variant="outline">选择文件</t-button>
          </t-upload>
        </t-form-item>
      </t-form>
    </t-dialog>

    <!-- 模板文件列表对话框 -->
    <t-dialog
      v-model:visible="showFilesDialog"
      :header="`模板文件 - ${selectedTemplate?.name}`"
      width="800px"
    >
      <div class="files-content">
        <div class="files-header">
          <t-button theme="primary" @click="showUploadFileDialog = true">
            上传文件
          </t-button>
        </div>
        
        <t-table
          :data="templateFiles"
          :columns="fileColumns"
          row-key="filename"
          :loading="filesLoading"
        >
          <template #operation="{ row }">
            <t-space>
              <t-button theme="primary" variant="text" @click="viewFileContent(row)">
                查看
              </t-button>
              <t-button theme="danger" variant="text" @click="deleteFile(row)">
                删除
              </t-button>
            </t-space>
          </template>
        </t-table>
      </div>
    </t-dialog>

    <!-- 上传文件对话框 -->
    <t-dialog
      v-model:visible="showUploadFileDialog"
      header="上传文件"
      @confirm="uploadFile"
      @cancel="cancelUploadFile"
      width="500px"
    >
      <t-upload
        v-model="uploadFileList"
        action="#"
        :auto-upload="true"
        :max="1"
        accept=".tex,.md,.txt"
        @change="onUploadFileChange"
        :show-upload-progress="false"
        :draggable="false"
        tips="支持 .tex, .md, .txt 文件"
      >
        <t-button variant="outline">选择文件</t-button>
      </t-upload>
    </t-dialog>

    <!-- 文件内容查看对话框 -->
    <t-dialog
      v-model:visible="showFileContentDialog"
      :header="`文件内容 - ${selectedFile?.filename}`"
      width="900px"
    >
      <div class="file-content">
        <t-textarea
          v-model="fileContent"
          readonly
          :autosize="{ minRows: 10, maxRows: 20 }"
          placeholder="文件内容加载中..."
        />
      </div>
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
import { templateAPI, type PaperTemplate, type PaperTemplateCreate, type PaperTemplateUpdate, type TemplateFile } from '@/api/template';

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

// 模板列表数据
const templateList = ref<PaperTemplate[]>([]);
const loading = ref(false);

// 表格列配置
const columns = ref([
  { colKey: 'name', title: '模板名称', width: '150px' },
  { colKey: 'description', title: '描述', width: '200px' },
  { colKey: 'category', title: '分类', width: '100px' },
  { colKey: 'is_public', title: '是否公开', width: '100px' },
  { colKey: 'created_at', title: '创建时间', width: '150px' },
  { colKey: 'operation', title: '操作', width: '200px' }
]);

// 分页配置
const pagination = reactive({
  defaultCurrent: 1,
  defaultPageSize: 10,
  total: 0,
});

// 分页变化处理
const onPageChange = (curr: number, pageInfo: { current: number; pageSize: number }) => {
  pagination.defaultCurrent = curr;
  pagination.defaultPageSize = pageInfo.pageSize;
  loadTemplates();
};

// 加载模板列表
const loadTemplates = async () => {
  if (!authStore.token) return;
  
  loading.value = true;
  try {
    const skip = (pagination.defaultCurrent - 1) * pagination.defaultPageSize;
    const templates = await templateAPI.getUserTemplates(
      authStore.token,
      skip,
      pagination.defaultPageSize
    );
    templateList.value = templates;
    pagination.total = templates.length; // 这里应该从后端获取总数
  } catch (error) {
    MessagePlugin.error('加载模板列表失败');
    console.error('加载模板失败:', error);
  } finally {
    loading.value = false;
  }
};

// 控制新建模板对话框显示
const showCreateTemplateDialog = ref(false);

// 正在编辑的模板
const editingTemplate = ref<PaperTemplate | null>(null);

// 模板表单数据
const templateForm = reactive<PaperTemplateCreate>({
  name: '',
  description: '',
  category: '',
  is_public: false
});

// 文件相关
const templateFormFileList = ref<Array<any>>([]);

// 控制API Key对话框显示
const showApiKeyDialog = ref(false);

// API Key表单数据
const apiKeyForm = ref({
  apiKey: ''
});

// 文件变更处理
const onFileChange = (fileList: Array<any>) => {
  console.log('文件变更:', fileList);
  // 直接使用文件列表，不需要额外的templateFormFile变量
  if (fileList && fileList.length > 0) {
    console.log('选择的文件:', fileList[0]);
  } else {
    console.log('没有选择文件');
  }
};

// 清除已选择的文件
const clearSelectedFile = () => {
  templateFormFileList.value = [];
};

// 编辑模板
const editTemplate = (template: PaperTemplate) => {
  editingTemplate.value = template;
  templateForm.name = template.name;
  templateForm.description = template.description || '';
  templateForm.category = template.category || '';
  templateForm.is_public = template.is_public;
  showCreateTemplateDialog.value = true;
};

// 删除模板
const deleteTemplate = async (template: PaperTemplate) => {
  if (!authStore.token) return;
  
  try {
    await templateAPI.deleteTemplate(authStore.token, template.id);
    MessagePlugin.success('模板删除成功');
    loadTemplates(); // 重新加载列表
  } catch (error) {
    MessagePlugin.error('删除模板失败');
    console.error('删除模板失败:', error);
  }
};

// 保存模板
const saveTemplate = async () => {
  console.log('开始保存模板...');
  console.log('表单数据:', templateForm);
  console.log('文件列表:', templateFormFileList.value);
  console.log('认证状态:', !!authStore.token);
  console.log('编辑状态:', !!editingTemplate.value);
  
  if (!templateForm.name) {
    console.log('模板名称为空，显示警告');
    MessagePlugin.warning('请输入模板名称');
    return;
  }

  if (!authStore.token) {
    console.log('没有访问令牌，无法保存');
    MessagePlugin.error('请先登录');
    return;
  }

  try {
    if (editingTemplate.value) {
      // 更新模板
      console.log('更新现有模板...');
      
      // 只包含有值的字段
      const updateData: PaperTemplateUpdate = {};
      if (templateForm.name) updateData.name = templateForm.name;
      if (templateForm.description !== '') updateData.description = templateForm.description;
      if (templateForm.category !== '') updateData.category = templateForm.category;
      updateData.is_public = templateForm.is_public;
      
      console.log('更新数据:', updateData);
      
      await templateAPI.updateTemplate(
        authStore.token,
        editingTemplate.value.id,
        updateData
      );
      MessagePlugin.success('模板更新成功');
    } else {
      // 新建模板
      console.log('创建新模板...');
      console.log('创建模板数据:', templateForm);
      console.log('模板文件:', templateFormFileList.value);
      
      // 创建符合后端期望的数据对象
      const createData = {
        name: templateForm.name,
        description: templateForm.description || undefined,
        category: templateForm.category || undefined,
        is_public: templateForm.is_public
      };
      
      console.log('发送给后端的数据:', createData);
      
      const newTemplate = await templateAPI.createTemplate(
        authStore.token,
        createData
      );
      
      console.log('模板创建成功:', newTemplate);
      
      // 如果有文件，上传文件
      if (templateFormFileList.value.length > 0) {
        console.log('开始上传文件:', templateFormFileList.value[0]);
        try {
          const fileToUpload = templateFormFileList.value[0].raw || templateFormFileList.value[0];
          console.log('准备上传的文件:', fileToUpload);
          
          await templateAPI.uploadTemplateFile(
            authStore.token,
            newTemplate.id,
            fileToUpload
          );
          console.log('文件上传成功');
        } catch (fileError) {
          console.error('文件上传失败:', fileError);
          MessagePlugin.warning('模板创建成功，但文件上传失败');
        }
      } else {
        console.log('没有选择文件');
      }
      
      MessagePlugin.success('模板创建成功');
    }
    
    console.log('保存完成，重新加载模板列表...');
    loadTemplates(); // 重新加载列表
    cancelTemplate();
  } catch (error) {
    console.error('保存模板时发生错误:', error);
    MessagePlugin.error('保存模板失败');
    console.error('保存模板失败:', error);
  }
};

// 取消编辑/新建模板
const cancelTemplate = () => {
  showCreateTemplateDialog.value = false;
  editingTemplate.value = null;
  templateForm.name = '';
  templateForm.description = '';
  templateForm.category = '';
  templateForm.is_public = false;
  templateFormFileList.value = [];
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

// 模板文件相关
const showFilesDialog = ref(false);
const selectedTemplate = ref<PaperTemplate | null>(null);
const templateFiles = ref<TemplateFile[]>([]);
const filesLoading = ref(false);

// 文件表格列配置
const fileColumns = ref([
  { colKey: 'filename', title: '文件名', width: '200px' },
  { colKey: 'size', title: '大小', width: '100px' },
  { colKey: 'modified', title: '修改时间', width: '150px' },
  { colKey: 'operation', title: '操作', width: '150px' }
]);

// 查看模板文件
const viewTemplateFiles = async (template: PaperTemplate) => {
  if (!authStore.token) return;
  
  selectedTemplate.value = template;
  showFilesDialog.value = true;
  await loadTemplateFiles();
};

// 加载模板文件列表
const loadTemplateFiles = async () => {
  if (!authStore.token || !selectedTemplate.value) return;
  
  filesLoading.value = true;
  try {
    const result = await templateAPI.getTemplateFiles(
      authStore.token,
      selectedTemplate.value.id
    );
    templateFiles.value = result.files.map(file => ({
      ...file,
      size: `${(file.size / 1024).toFixed(1)} KB`
    }));
  } catch (error) {
    MessagePlugin.error('加载文件列表失败');
    console.error('加载文件列表失败:', error);
  } finally {
    filesLoading.value = false;
  }
};

// 上传文件相关
const showUploadFileDialog = ref(false);
const uploadFileList = ref<Array<any>>([]);

const onUploadFileChange = (fileList: Array<any>) => {
  console.log('上传文件变更:', fileList);
  if (fileList && fileList.length > 0) {
    // TDesign Upload组件的文件对象结构
    const fileObj = fileList[0];
    console.log('上传文件对象:', fileObj);
    
    // 检查文件对象的结构
    if (fileObj.raw) {
      uploadFileList.value = [fileObj];
    } else if (fileObj instanceof File) {
      uploadFileList.value = [{ raw: fileObj }];
    } else {
      console.log('未知的上传文件对象格式:', fileObj);
      uploadFileList.value = [];
    }
  } else {
    uploadFileList.value = [];
  }
};

const uploadFile = async () => {
  console.log('开始上传文件，文件列表:', uploadFileList.value);
  if (!authStore.token || !selectedTemplate.value || uploadFileList.value.length === 0) {
    console.log('上传条件检查失败:', {
      hasToken: !!authStore.token,
      hasTemplate: !!selectedTemplate.value,
      fileCount: uploadFileList.value.length
    });
    return;
  }
  
  try {
    const file = uploadFileList.value[0].raw;
    console.log('准备上传文件:', file);
    
    await templateAPI.uploadTemplateFile(
      authStore.token,
      selectedTemplate.value.id,
      file
    );
    MessagePlugin.success('文件上传成功');
    await loadTemplateFiles();
    cancelUploadFile();
  } catch (error) {
    MessagePlugin.error('文件上传失败');
    console.error('文件上传失败:', error);
  }
};

const cancelUploadFile = () => {
  showUploadFileDialog.value = false;
  uploadFileList.value = [];
};

// 文件内容查看相关
const showFileContentDialog = ref(false);
const selectedFile = ref<TemplateFile | null>(null);
const fileContent = ref('');

const viewFileContent = async (file: TemplateFile) => {
  if (!authStore.token || !selectedTemplate.value) return;
  
  selectedFile.value = file;
  showFileContentDialog.value = true;
  
  try {
    const result = await templateAPI.getTemplateFileContent(
      authStore.token,
      selectedTemplate.value.id,
      file.filename
    );
    fileContent.value = result.content;
  } catch (error) {
    MessagePlugin.error('加载文件内容失败');
    console.error('加载文件内容失败:', error);
    fileContent.value = '文件内容加载失败';
  }
};

// 删除文件
const deleteFile = async (file: TemplateFile) => {
  if (!authStore.token || !selectedTemplate.value) return;
  
  try {
    await templateAPI.deleteTemplateFile(
      authStore.token,
      selectedTemplate.value.id,
      file.filename
    );
    MessagePlugin.success('文件删除成功');
    await loadTemplateFiles();
  } catch (error) {
    MessagePlugin.error('删除文件失败');
    console.error('删除文件失败:', error);
  }
};

// 清除上传文件列表
const clearUploadFile = () => {
  uploadFileList.value = [];
};

// 检查用户认证状态
onMounted(() => {
  loadTemplates();
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

.files-content {
  padding: 20px 0;
}

.files-header {
  margin-bottom: 20px;
}

.file-content {
  max-height: 500px;
  overflow-y: auto;
}

/* 新增样式 */
</style>