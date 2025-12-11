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
            <t-button theme="primary" @click="showCreateTemplateDialog = true"> 新建模板 </t-button>
          </template>

          <t-table
            :data="templateList"
            :columns="columns"
            row-key="id"
            :pagination="pagination"
            @page-change="onPageChange"
            :loading="loading"
          >
            <template #output_format="{ row }">
              <t-tag :theme="getOutputFormatTheme(row.output_format)" variant="light">
                {{ getOutputFormatLabel(row.output_format) }}
              </t-tag>
            </template>
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
        <t-form-item label="输出格式" name="output_format">
          <t-select v-model="templateForm.output_format" placeholder="请选择输出格式">
            <t-option value="markdown" label="Markdown (.md)" />
            <t-option value="word" label="Word (.docx)" />
            <t-option value="latex" label="LaTeX (.tex)" disabled />
          </t-select>
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
            :accept="getAcceptedFileTypes()"
            @change="onFileChange"
            :show-upload-progress="false"
            :draggable="false"
            :tips="getUploadTips()"
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
          <!-- 加载状态 -->
          <div v-if="templatePreviewLoading" class="loading-container">
            <t-loading size="large" text="加载中..." />
          </div>
          
          <!-- 文本文件预览 -->
          <div v-else-if="templatePreviewData?.type === 'text'" class="text-preview">
            <MarkdownRenderer
              v-if="selectedTemplate?.file_path?.endsWith('.md')"
              :content="templatePreviewData.content || ''"
              :base-path="''"
            />
            <t-textarea
              v-else
              :model-value="templatePreviewData.content || ''"
              readonly
              :autosize="{ minRows: 15, maxRows: 25 }"
            />
          </div>
          
          <!-- 图片文件预览 -->
          <div v-else-if="templatePreviewData?.type === 'image'" class="image-preview">
            <img
              :src="`data:image/${templatePreviewData.filename.split('.').pop()};base64,${templatePreviewData.content}`"
              :alt="templatePreviewData.filename"
              style="max-width: 100%; height: auto;"
            />
          </div>
          
          <!-- 二进制文件预览 -->
          <div v-else-if="templatePreviewData?.type === 'binary'" class="binary-preview">
            <!-- DOCX文件使用DocxViewer预览 -->
            <DocxViewer
              v-if="templatePreviewData.filename?.toLowerCase().endsWith('.docx')"
              :file-info="{
                filename: templatePreviewData.filename,
                size: templatePreviewData.size,
                mime_type: templatePreviewData.mime_type,
                download_url: templatePreviewData.download_url,
                message: templatePreviewData.message
              }"
              :work-id="''"
              :token="authStore.token || ''"
            />
            <!-- 其他二进制文件显示下载信息 -->
            <div v-else class="file-info-display">
              <t-icon name="file" size="48px" />
              <p><strong>文件名：</strong>{{ templatePreviewData.filename }}</p>
              <p><strong>文件大小：</strong>{{ formatFileSize(templatePreviewData.size) }}</p>
              <p><strong>文件类型：</strong>{{ templatePreviewData.mime_type }}</p>
            </div>
          </div>
          
          <!-- 预览失败 -->
          <div v-else-if="templatePreviewError" class="error-preview">
            <t-icon name="error-circle" size="48px" />
            <p>模板预览加载失败</p>
            <p>{{ templatePreviewError }}</p>
          </div>
        </div>
      </div>
      <template #footer>
        <t-button theme="primary" @click="closeContentDialog">关闭</t-button>
      </template>
    </t-dialog>

    <!-- 确认删除对话框 -->
    <t-dialog
      v-model:visible="showDeleteConfirmDialog"
      header="确认删除模板"
      width="500px"
      @confirm="confirmForceDelete"
      @cancel="cancelForceDelete"
    >
      <div class="delete-confirm-content">
        <p>{{ deleteConfirmMessage }}</p>
        <p class="warning-text">⚠️ 强制删除将同时删除所有引用该模板的工作，此操作不可恢复！</p>
      </div>
      <template #footer>
        <t-space>
          <t-button theme="default" @click="cancelForceDelete">取消</t-button>
          <t-button theme="danger" @click="confirmForceDelete">强制删除</t-button>
        </t-space>
      </template>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import { useAuthStore } from '@/stores/auth'
import {
  templateAPI,
  type PaperTemplate,
  type PaperTemplateUpdate,
} from '@/api/template'
import Sidebar from '@/components/Sidebar.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import DocxViewer from '@/components/DocxViewer.vue'

// 侧边栏折叠状态 - 手机端默认收起
const isSidebarCollapsed = ref(window.innerWidth <= 768)

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

const router = useRouter()
const authStore = useAuthStore()

// 新建工作
const createNewTask = () => {
  // 这里可以添加创建新任务的逻辑
  console.log('创建新任务')
  router.push('/home')
}

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null)

// 选择历史工作
const selectHistory = (id: number) => {
  router.push('/home')
  // 在主页中实现选中历史工作的逻辑
}

// 模板列表数据
const templateList = ref<PaperTemplate[]>([])
const loading = ref(false)

// 表格列配置
const columns = ref([
  { colKey: 'name', title: '模板名称', width: '150px' },
  { colKey: 'description', title: '描述', width: '200px' },
  { colKey: 'category', title: '分类', width: '100px' },
  { colKey: 'output_format', title: '输出格式', width: '100px' },
  { colKey: 'is_public', title: '是否公开', width: '100px' },
  { colKey: 'created_at', title: '创建时间', width: '150px' },
  { colKey: 'operation', title: '操作', width: '200px' },
])

// 分页配置
const pagination = reactive({
  defaultCurrent: 1,
  defaultPageSize: 10,
  total: 0,
})

// 分页变化处理
const onPageChange = (curr: number, pageInfo: { current: number; pageSize: number }) => {
  pagination.defaultCurrent = curr
  pagination.defaultPageSize = pageInfo.pageSize
  loadTemplates()
}

// 加载模板列表
const loadTemplates = async () => {
  if (!authStore.token) return

  loading.value = true
  try {
    const skip = (pagination.defaultCurrent - 1) * pagination.defaultPageSize
    const templates = await templateAPI.getUserTemplates(
      authStore.token,
      skip,
      pagination.defaultPageSize,
    )
    templateList.value = templates
    pagination.total = templates.length // 这里应该从后端获取总数
  } catch (error: any) {
    const errorMessage = error?.response?.data?.detail || error?.message || '加载模板列表失败'
    MessagePlugin.error(errorMessage)
    console.error('加载模板失败:', error)
  } finally {
    loading.value = false
  }
}

// 控制新建模板对话框显示
const showCreateTemplateDialog = ref(false)

// 正在编辑的模板
const editingTemplate = ref<PaperTemplate | null>(null)

// 模板表单数据
const templateForm = reactive({
  name: '',
  description: '',
  category: '',
  output_format: 'markdown', // 默认为markdown格式
  is_public: false,
})

// 文件相关
const templateFormFileList = ref<Array<any>>([])

// 文件变更处理
const onFileChange = (fileList: Array<any>) => {
  console.log('文件变更:', fileList)
  if (fileList && fileList.length > 0) {
    console.log('选择的文件:', fileList[0])
    // 自动设置文件路径
    const fileName = fileList[0].name || fileList[0].raw?.name
    if (fileName) {
      templateForm.file_path = fileName
    }
  } else {
    console.log('没有选择文件')
    templateForm.file_path = ''
  }
}

// 根据输出格式获取允许的文件类型
const getAcceptedFileTypes = () => {
  const acceptMap: Record<string, string> = {
    markdown: '.md',
    word: '.docx',
    latex: '.tex'
  }
  return acceptMap[templateForm.output_format] || '.md,.docx,.tex'
}

// 根据输出格式获取上传提示
const getUploadTips = () => {
  const tipsMap: Record<string, string> = {
    markdown: '只能上传 .md 文件',
    word: '只能上传 .docx 文件',
    latex: '只能上传 .tex 文件'
  }
  return tipsMap[templateForm.output_format] || '请先选择输出格式'
}

// 输出格式标签转换
const getOutputFormatLabel = (format: string) => {
  const labels = {
    markdown: 'Markdown',
    word: 'Word',
    latex: 'LaTeX'
  }
  return labels[format as keyof typeof labels] || format
}

// 输出格式主题
const getOutputFormatTheme = (format: string) => {
  const themes = {
    markdown: 'primary',
    word: 'success',
    latex: 'warning'
  }
  return themes[format as keyof typeof themes] || 'default'
}

// 编辑模板
const editTemplate = async (template: PaperTemplate) => {
  editingTemplate.value = template
  templateForm.name = template.name
  templateForm.description = template.description || ''
  templateForm.category = template.category || ''
  templateForm.output_format = template.output_format || 'markdown'
  templateForm.file_path = template.file_path || ''
  templateForm.is_public = template.is_public

  showCreateTemplateDialog.value = true
}

// 删除模板
const deleteTemplate = async (template: PaperTemplate) => {
  if (!authStore.token) return

  console.log('开始删除模板:', template.id)

  try {
    const result = await templateAPI.deleteTemplate(authStore.token, template.id)
    console.log('删除成功:', result)
    MessagePlugin.success('模板删除成功')
    loadTemplates() // 重新加载列表
  } catch (error: any) {
    console.log('删除模板错误详情:', error)
    console.log('错误类型:', typeof error)
    console.log('错误响应状态:', error.response?.status)
    console.log('错误响应数据:', error.response?.data)
    console.log('错误消息:', error.message)

    // 检查是否是外键约束错误
    const isForeignKeyError =
      error.response?.status === 400 &&
      (error.response?.data?.detail?.includes('无法删除模板') ||
        error.response?.data?.detail?.includes('violates foreign key constraint') ||
        error.message?.includes('无法删除模板'))

    console.log('是否检测到外键约束错误:', isForeignKeyError)

    if (isForeignKeyError) {
      console.log('检测到外键约束错误，显示确认对话框')

      // 设置要删除的模板和确认消息
      templateToDelete.value = template
      deleteConfirmMessage.value =
        error.response?.data?.detail || error.message || '该模板正在被其他工作使用，无法直接删除。'

      // 显示确认删除对话框
      showDeleteConfirmDialog.value = true

      // 显示警告消息
      MessagePlugin.warning({
        content: '检测到外键约束，请在弹出的对话框中确认是否强制删除',
        duration: 3000,
      })
    } else {
      // 其他类型的错误
      const errorMessage = error.response?.data?.detail || error.message || '删除模板失败'
      MessagePlugin.error(errorMessage)
      console.error('删除模板失败:', error)
    }
  }
}

// 保存模板
const saveTemplate = async () => {
  console.log('开始保存模板...')
  console.log('表单数据:', templateForm)
  console.log('认证状态:', !!authStore.token)
  console.log('编辑状态:', !!editingTemplate.value)

  if (!templateForm.name) {
    console.log('模板名称为空，显示警告')
    MessagePlugin.warning('请输入模板名称')
    return
  }

  if (!authStore.token) {
    console.log('没有访问令牌，无法保存')
    MessagePlugin.error('请先登录')
    return
  }

  try {
    if (editingTemplate.value) {
      // 更新模板
      console.log('更新现有模板...')

      // 只包含有值的字段
      const updateData: PaperTemplateUpdate = {}
      if (templateForm.name) updateData.name = templateForm.name
      if (templateForm.description !== '') updateData.description = templateForm.description
      if (templateForm.category !== '') updateData.category = templateForm.category
      if (templateForm.file_path !== '') updateData.file_path = templateForm.file_path
      updateData.is_public = templateForm.is_public

      console.log('更新数据:', updateData)

      await templateAPI.updateTemplate(authStore.token, editingTemplate.value.id, updateData)

      MessagePlugin.success('模板更新成功')
    } else {
      // 新建模板
      console.log('创建新模板...')

      // 检查是否选择了文件
      if (templateFormFileList.value.length === 0) {
        MessagePlugin.warning('请选择模板文件')
        return
      }

      const fileToUpload = templateFormFileList.value[0].raw || templateFormFileList.value[0]

      try {
        const newTemplate = await templateAPI.createTemplate(
          authStore.token,
          fileToUpload,
          templateForm.name,
          templateForm.output_format,
          templateForm.description || undefined,
          templateForm.category || undefined,
          templateForm.is_public
        )

        console.log('模板创建成功:', newTemplate)
        MessagePlugin.success('模板创建成功')
      } catch (error: any) {
        console.error('模板创建失败:', error)
        const errorMessage = error?.message || '模板创建失败'
        MessagePlugin.error(errorMessage)
        return
      }
    }

    console.log('保存完成，重新加载模板列表...')
    loadTemplates() // 重新加载列表
    cancelTemplate()
  } catch (error: any) {
    console.error('保存模板时发生错误:', error)
    const errorMessage = error?.response?.data?.detail || error?.message || '保存模板失败'
    MessagePlugin.error(errorMessage)
  }
}

// 格式化日期函数
const formatDate = (dateString: string) => {
  if (!dateString) return ''
  const date = new Date(dateString)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// 取消编辑/新建模板
const cancelTemplate = () => {
  showCreateTemplateDialog.value = false
  editingTemplate.value = null
  templateForm.name = ''
  templateForm.description = ''
  templateForm.category = ''
  templateForm.file_path = ''
  templateForm.is_public = false
  templateFormFileList.value = []
}

// 模板内容相关
const showContentDialog = ref(false)
const selectedTemplate = ref<PaperTemplate | null>(null)
const templateContent = ref('')
const templatePreviewLoading = ref(false)
const templatePreviewData = ref<{
  type: 'text' | 'image' | 'binary'
  content?: string
  filename: string
  size: number
  mime_type?: string
  download_url?: string
  message?: string
} | null>(null)
const templatePreviewError = ref('')

const viewTemplateContent = async (template: PaperTemplate) => {
  if (!authStore.token) return

  selectedTemplate.value = template
  showContentDialog.value = true
  templatePreviewLoading.value = true
  templatePreviewError.value = ''
  templatePreviewData.value = null

  try {
    const result = await templateAPI.getTemplatePreview(authStore.token, template.id)
    templatePreviewData.value = result
    
    // 为了向后兼容，如果是文本类型，也设置templateContent
    if (result.type === 'text') {
      templateContent.value = result.content || ''
    }
  } catch (error: any) {
    const errorMessage = error?.response?.data?.detail || error?.message || '加载模板预览失败'
    templatePreviewError.value = errorMessage
    MessagePlugin.error(errorMessage)
    console.error('加载模板预览失败:', error)
  } finally {
    templatePreviewLoading.value = false
  }
}

const closeContentDialog = () => {
  showContentDialog.value = false
  selectedTemplate.value = null
  templateContent.value = ''
  templatePreviewData.value = null
  templatePreviewError.value = ''
  templatePreviewLoading.value = false
}

// 格式化文件大小
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 确认删除对话框相关
const showDeleteConfirmDialog = ref(false)
const deleteConfirmMessage = ref('')
const templateToDelete = ref<PaperTemplate | null>(null)

const confirmForceDelete = async () => {
  if (!authStore.token || !templateToDelete.value) return

  try {
    const result = await templateAPI.forceDeleteTemplate(authStore.token, templateToDelete.value.id)
    MessagePlugin.success(`强制删除成功！${result.deleted_works_count} 个相关工作已被删除`)
    loadTemplates() // 重新加载列表
    showDeleteConfirmDialog.value = false
    templateToDelete.value = null
  } catch (error) {
    MessagePlugin.error('强制删除失败')
    console.error('强制删除失败:', error)
  }
}

const cancelForceDelete = () => {
  showDeleteConfirmDialog.value = false
  templateToDelete.value = null
}

// 检查用户认证状态
onMounted(() => {
  loadTemplates()
})
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

/* 预览内容样式 */
.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 60px 20px;
}

.text-preview {
  background-color: #f5f5f5;
  border-radius: 4px;
  padding: 16px;
}

.image-preview {
  text-align: center;
  padding: 16px;
  background-color: #fafafa;
}

.binary-preview {
  padding: 20px;
}

.file-info-display {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 20px;
  background-color: #f8f9fa;
  border-radius: 8px;
}

.file-info-display p {
  margin: 4px 0;
  color: #495057;
}

.error-preview {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 40px 20px;
  color: #dc3545;
}

.delete-confirm-content {
  text-align: left;
  padding: 10px 0;
}

.delete-confirm-content p {
  margin-bottom: 5px;
  color: #333;
}

.warning-text {
  color: #e74c3c;
  font-weight: bold;
}
</style>
