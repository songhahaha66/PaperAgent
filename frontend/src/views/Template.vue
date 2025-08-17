<template>
  <div class="template-page">
        <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
    />
    
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
            <template #is_public="{ row }">
              <t-tag :theme="row.is_public ? 'success' : 'warning'" variant="light">
                {{ row.is_public ? '是' : '否' }}
              </t-tag>
            </template>
            <template #created_at="{ row }">
              {{ formatDate(row.created_at) }}
            </template>
            <template #operation="{ row }">
              <t-space>
                <t-button theme="primary" variant="text" @click="editTemplate(row)">
                  编辑
                </t-button>
                <t-button theme="default" variant="text" @click="viewTemplateContent(row)">
                  查看内容
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
      width="800px"
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
            accept=".tex,.md,.txt,.doc,.docx"
            @change="onFileChange"
            :show-upload-progress="false"
            :draggable="false"
            tips="支持 .tex, .md, .txt, .doc, .docx 文件，一个模板对应一个文件"
          >
            <t-button variant="outline">选择文件</t-button>
          </t-upload>
        </t-form-item>
      </t-form>
    </t-dialog>

    <!-- 模板内容查看对话框 -->
    <t-dialog
      v-model:visible="showContentDialog"
      :header="`模板内容 - ${selectedTemplate?.name}`"
      width="900px"
      @confirm="closeContentDialog"
      @cancel="closeContentDialog"
    >
      <div class="content-viewer">
        <div class="content-display">
          <!-- 如果是Markdown文件，使用Markdown渲染 -->
          <div 
            v-if="selectedTemplate?.file_path?.endsWith('.md')" 
            class="markdown-content"
            v-html="renderMarkdown(templateContent)"
          ></div>
          <!-- 其他文件格式使用文本域显示 -->
          <t-textarea
            v-else
            v-model="templateContent"
            readonly
            :autosize="{ minRows: 15, maxRows: 25 }"
            placeholder="模板内容加载中..."
          />
        </div>
      </div>
      <template #footer>
        <t-button theme="primary" @click="closeContentDialog">关闭</t-button>
      </template>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { MessagePlugin } from 'tdesign-vue-next';
import { useAuthStore } from '@/stores/auth';
import { templateAPI, type PaperTemplate, type PaperTemplateCreate, type PaperTemplateUpdate } from '@/api/template';
import Sidebar from '@/components/Sidebar.vue';

// 侧边栏折叠状态
const isSidebarCollapsed = ref(false);

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value;
};

const router = useRouter();
const authStore = useAuthStore();

// 新建工作
const createNewTask = () => {
  // 这里可以添加创建新任务的逻辑
  console.log('创建新任务');
  router.push('/home');
};

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null);

// 选择历史工作
const selectHistory = (id: number) => {
  router.push('/home');
  // 在主页中实现选中历史工作的逻辑
};

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
  file_path: '',
  is_public: false
});

// 文件相关
const templateFormFileList = ref<Array<any>>([]);

// 文件变更处理
const onFileChange = (fileList: Array<any>) => {
  console.log('文件变更:', fileList);
  if (fileList && fileList.length > 0) {
    console.log('选择的文件:', fileList[0]);
    // 自动设置文件路径
    const fileName = fileList[0].name || fileList[0].raw?.name;
    if (fileName) {
      templateForm.file_path = fileName;
    }
  } else {
    console.log('没有选择文件');
    templateForm.file_path = '';
  }
};

// 编辑模板
const editTemplate = async (template: PaperTemplate) => {
  editingTemplate.value = template;
  templateForm.name = template.name;
  templateForm.description = template.description || '';
  templateForm.category = template.category || '';
  templateForm.file_path = template.file_path || '';
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
      if (templateForm.file_path !== '') updateData.file_path = templateForm.file_path;
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
      
      // 检查是否选择了文件
      if (templateFormFileList.value.length === 0) {
        MessagePlugin.warning('请选择模板文件');
        return;
      }
      
      // 先上传文件获取内容
      const fileToUpload = templateFormFileList.value[0].raw || templateFormFileList.value[0];
      console.log('准备上传的文件:', fileToUpload);
      
      try {
        const uploadResult = await templateAPI.uploadTemplateFile(
          authStore.token,
          fileToUpload
        );
        
        console.log('文件上传成功:', uploadResult);
        
        // 创建符合后端期望的数据对象
        const createData = {
          name: templateForm.name,
          description: templateForm.description || undefined,
          category: templateForm.category || undefined,
          file_path: uploadResult.file_path,
          is_public: templateForm.is_public,
          content: uploadResult.content
        };
        
        console.log('发送给后端的数据:', createData);
        
        const newTemplate = await templateAPI.createTemplate(
          authStore.token,
          createData
        );
        
        console.log('模板创建成功:', newTemplate);
        MessagePlugin.success('模板创建成功');
      } catch (fileError) {
        console.error('文件处理失败:', fileError);
        MessagePlugin.error('模板创建失败：文件处理错误');
        return;
      }
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

// 格式化日期函数
const formatDate = (dateString: string) => {
  if (!dateString) return '';
  const date = new Date(dateString);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

// 简单的Markdown渲染函数
const renderMarkdown = (text: string) => {
  if (!text) return '';
  
  // 先处理代码块，避免代码块中的标记被处理
  let codeBlocks: string[] = [];
  let codeBlockCounter = 0;
  
  // 提取代码块（包括语言标识）
  text = text.replace(/```(\w+)?\n([\s\S]*?)\n```/g, (match, lang, content) => {
    codeBlocks.push(`<pre><code class="language-${lang || 'plaintext'}">${content}</code></pre>`);
    return `{{CODE_BLOCK_${codeBlockCounter++}}}`;
  });

  // 处理行内代码
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  
  // 按照从h6到h1的顺序处理标题
  text = text
    .replace(/^###### (.*)$/gm, '<h6>$1</h6>')
    .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
    .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h2>$1</h2>')
    .replace(/^# (.*)$/gm, '<h1>$1</h1>');
  
  // 处理粗体和斜体
  text = text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>');
  
  // 处理链接
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  
  // 处理无序列表
  text = text.replace(/^\s*[\*|\-|\+]\s(.*)$/gm, '<li>$1</li>');
  text = text.replace(/(<li>.*<\/li>)/gms, '<ul>$1</ul>');
  
  // 处理有序列表
  text = text.replace(/^\s*(\d+)\.\s(.*)$/gm, '<li data-line="$1">$2</li>');
  text = text.replace(/(<li data-line="\d+">.*<\/li>)/gms, '<ol>$1</ol>');
  text = text.replace(/ data-line="\d+"/g, '');
  
  // 处理换行（但不在块级元素内部添加<br>）
  text = text.replace(/\n/g, '<br>');

  // 恢复代码块
  for (let i = 0; i < codeBlockCounter; i++) {
    text = text.replace(`{{CODE_BLOCK_${i}}}`, codeBlocks[i]);
  }
  
  return text;
};

// 取消编辑/新建模板
const cancelTemplate = () => {
  showCreateTemplateDialog.value = false;
  editingTemplate.value = null;
  templateForm.name = '';
  templateForm.description = '';
  templateForm.category = '';
  templateForm.file_path = '';
  templateForm.is_public = false;
  templateFormFileList.value = [];
};

// 模板内容相关
const showContentDialog = ref(false);
const selectedTemplate = ref<PaperTemplate | null>(null);
const templateContent = ref('');

const viewTemplateContent = async (template: PaperTemplate) => {
  if (!authStore.token) return;
  
  selectedTemplate.value = template;
  showContentDialog.value = true;
  
  try {
    const result = await templateAPI.getTemplateContent(
      authStore.token,
      template.id
    );
    templateContent.value = result.content;
  } catch (error) {
    MessagePlugin.error('加载模板内容失败');
    console.error('加载模板内容失败:', error);
    templateContent.value = '模板内容加载失败';
  }
};

const closeContentDialog = () => {
  showContentDialog.value = false;
  selectedTemplate.value = null;
  templateContent.value = '';
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

/* 新增样式 */
.content-viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.content-display {
  flex: 1;
  overflow-y: auto;
  background-color: #f9f9f9;
  border: 1px solid #eee;
  border-radius: 4px;
  padding: 15px;
}

.content-display .t-textarea {
  min-height: 100%;
  box-sizing: border-box;
}

/* Markdown内容样式 */
.markdown-content {
  padding: 15px;
  background-color: #fff;
  border-radius: 4px;
}

.markdown-content h1 {
  font-size: 2em;
  margin: 0.67em 0;
  font-weight: bold;
}

.markdown-content h2 {
  font-size: 1.5em;
  margin: 0.83em 0;
  font-weight: bold;
}

.markdown-content h3 {
  font-size: 1.17em;
  margin: 1em 0;
  font-weight: bold;
}

.markdown-content h4 {
  font-size: 1em;
  margin: 1.33em 0;
  font-weight: bold;
}

.markdown-content h5 {
  font-size: 0.83em;
  margin: 1.67em 0;
  font-weight: bold;
}

.markdown-content h6 {
  font-size: 0.67em;
  margin: 2.33em 0;
  font-weight: bold;
}

.markdown-content p {
  margin: 1em 0;
}

.markdown-content a {
  color: #007bff;
  text-decoration: none;
}

.markdown-content a:hover {
  text-decoration: underline;
}

.markdown-content strong {
  font-weight: bold;
}

.markdown-content em {
  font-style: italic;
}

.markdown-content ul, .markdown-content ol {
  padding-left: 2em;
  margin: 1em 0;
}

.markdown-content li {
  margin: 0.5em 0;
}

.markdown-content code {
  background-color: #f5f5f5;
  padding: 0.2em 0.4em;
  border-radius: 3px;
  font-family: monospace;
}

.markdown-content pre {
  background-color: #f5f5f5;
  padding: 1em;
  border-radius: 5px;
  overflow-x: auto;
}

.markdown-content pre code {
  background-color: transparent;
  padding: 0;
}
</style>