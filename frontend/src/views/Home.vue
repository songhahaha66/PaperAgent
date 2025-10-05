<template>
  <div class="home-page">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
    />

    <div class="home-container">
      <!-- 欢迎标题 -->
      <div class="welcome-header">
        <h1>你好，{{ userName }}</h1>
        <p>智能论文生成助手，让学术写作更高效</p>
      </div>

      <!-- 主要任务创建区域 -->
      <div class="main-task-area">
        <div class="input-container">
          <!-- 第一阶段：输入问题和上传附件 -->
          <div v-if="currentStep === 1" class="step-content">
            <div class="input-wrapper">
              <t-textarea
                v-model="researchQuestion"
                placeholder="请详细描述您要研究的学术问题"
                :autosize="{ minRows: 5, maxRows: 8 }"
                class="question-input"
              />

              <!-- 按钮容器 -->
              <div class="button-container">
                <!-- 附件按钮 - 左下角 -->
                <div class="attachment-btn">
                  <t-upload
                    v-model="uploadedFiles"
                    :auto-upload="false"
                    :multiple="true"
                    :accept="'.pdf,.doc,.docx,.tex,.txt,.zip,.rar,.png,.jpg,.jpeg,.gif'"
                    :max="10"
                    :drag="false"
                    :show-upload-progress="true"
                    class="file-upload"
                  >
                    <t-button theme="default" variant="text" size="middle">
                      <template #icon>
                        <t-icon name="attach" />
                      </template>
                      附件
                    </t-button>
                  </t-upload>
                  <span class="file-count" v-if="uploadedFiles.length > 0">{{ uploadedFiles.length }}</span>
                </div>

                <!-- 下一步按钮 - 右下角 -->
                <div class="next-btn-wrapper">
                  <t-button
                    theme="primary"
                    size="middle"
                    @click="nextStep"
                    :disabled="!researchQuestion.trim()"
                    class="next-btn"
                  >
                    下一步
                    <template #icon>
                      <t-icon name="arrow-right" />
                    </template>
                  </t-button>
                </div>
              </div>
            </div>
          </div>

          <!-- 第二阶段：选择模板 -->
          <div v-if="currentStep === 2" class="step-content">
            <h3>选择论文模板</h3>

            <!-- 不使用模板选项 -->
            <div class="no-template-option">
              <t-card
                :class="{ selected: selectedTemplateId === null }"
                @click="selectNoTemplate"
                class="no-template-card"
              >
                <div class="no-template-content">
                  <t-icon name="file-add" theme="default" size="32px" />
                  <div class="no-template-text">
                    <h4>不使用模板</h4>
                    <p>从头开始创建论文，完全自定义内容结构</p>
                  </div>
                  <div v-if="selectedTemplateId === null" class="selection-indicator">
                    <t-icon name="check-circle-filled" theme="success" />
                  </div>
                </div>
              </t-card>
            </div>

            <!-- 或者分割线 -->
            <div class="template-divider">
              <span>或者选择现有模板</span>
            </div>

            <!-- 加载状态 -->
            <div v-if="loading" class="loading-state">
              <t-loading size="large" />
              <p>正在加载您的模板...</p>
            </div>

            <!-- 模板列表 -->
            <div v-else-if="availableTemplates.length > 0" class="template-list-container">
              <t-list>
                <t-list-item
                  v-for="template in availableTemplates"
                  :key="template.id"
                  :class="{ selected: selectedTemplateId === template.id }"
                  @click="selectTemplate(template.id)"
                >
                  <t-list-item-meta
                    :title="template.name"
                    :description="template.description || '暂无描述'"
                  >
                  </t-list-item-meta>

                  <template #action>
                    <t-space>
                      <t-button
                        theme="default"
                        variant="text"
                        size="small"
                        @click.stop="previewTemplate(template)"
                      >
                        预览
                      </t-button>
                      <div v-if="selectedTemplateId === template.id">
                        <t-icon name="check-circle-filled" theme="success" />
                      </div>
                      <t-button
                        v-else
                        theme="primary"
                        variant="text"
                        size="small"
                        @click.stop="selectTemplate(template.id)"
                      >
                        选择
                      </t-button>
                    </t-space>
                  </template>
                </t-list-item>
              </t-list>
            </div>

            <!-- 无模板状态 -->
            <div v-else class="no-template-state">
              <div class="no-template-icon">
                <t-icon name="file" theme="default" size="48px" />
              </div>
              <h4>暂无模板</h4>
              <p>您还没有创建任何论文模板</p>
              <t-button theme="primary" variant="outline" @click="goToTemplatePage">
                去创建模板
              </t-button>
            </div>

            <div class="step-actions">
              <t-button theme="default" size="middle" @click="prevStep" class="prev-btn">
                上一步
              </t-button>

              <t-button
                theme="success"
                size="middle"
                @click="startWork"
                :disabled="creatingWork"
                class="start-btn"
              >
                <template #icon>
                  <t-icon name="play" />
                </template>
                {{ creatingWork ? '创建中...' : '开始工作' }}
              </t-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 模板预览对话框 -->
    <t-dialog
      v-model:visible="showPreviewDialog"
      :header="`模板预览 - ${previewTemplateData?.name}`"
      width="900px"
      @confirm="closePreviewDialog"
      @cancel="closePreviewDialog"
    >
      <div class="template-preview">
        <div class="template-info">
          <p><strong>模板名称：</strong>{{ previewTemplateData?.name }}</p>
          <p><strong>模板描述：</strong>{{ previewTemplateData?.description || '暂无描述' }}</p>
          <p><strong>模板分类：</strong>{{ previewTemplateData?.category || '未分类' }}</p>
        </div>
        <div class="template-content">
          <h4>模板内容：</h4>
          <div class="content-display">
            <MarkdownRenderer
              v-if="previewTemplateData?.file_path?.endsWith('.md')"
              :content="templateContent"
              :base-path="''"
            />
            <t-textarea
              v-else
              v-model="templateContent"
              readonly
              :autosize="{ minRows: 15, maxRows: 25 }"
              placeholder="模板内容加载中..."
            />
          </div>
        </div>
      </div>
      <template #footer>
        <t-button theme="primary" @click="closePreviewDialog">关闭</t-button>
      </template>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { MessagePlugin } from 'tdesign-vue-next'
import { templateAPI, type PaperTemplate } from '@/api/template'
import { workspaceAPI, type WorkCreate } from '@/api/workspace'
import Sidebar from '@/components/Sidebar.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'

const router = useRouter()
const authStore = useAuthStore()

// 侧边栏折叠状态 - 手机端默认收起
const isSidebarCollapsed = ref(window.innerWidth <= 768)

// 任务创建步骤
const currentStep = ref(1)

// 研究问题
const researchQuestion = ref('')

// 选择的模板ID
const selectedTemplateId = ref<number | null>(null)

// 上传的文件
const uploadedFiles = ref([])

// 可用模板列表
const availableTemplates = ref<PaperTemplate[]>([])

// 加载状态
const loading = ref(false)

// 创建工作状态
const creatingWork = ref(false)

// 用户名
const userName = computed(() => authStore.currentUser?.username || '用户')

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null)

// 模板预览相关
const showPreviewDialog = ref(false)
const previewTemplateData = ref<PaperTemplate | null>(null)
const templateContent = ref('')

// 上传相关配置
const tempWorkId = ref<string | null>(null) // 临时存储的工作ID

// 加载用户模板
const loadUserTemplates = async () => {
  if (!authStore.token) return

  loading.value = true
  try {
    const templates = await templateAPI.getUserTemplates(authStore.token)
    availableTemplates.value = templates
  } catch (error) {
    console.error('加载模板失败:', error)
    MessagePlugin.error('加载模板失败')
  } finally {
    loading.value = false
  }
}

// 下一步
const nextStep = () => {
  if (currentStep.value === 1) {
    // 进入第二步时加载模板
    loadUserTemplates()
  }

  if (currentStep.value < 2) {
    currentStep.value++
  }
}

// 上一步
const prevStep = () => {
  if (currentStep.value > 1) {
    currentStep.value--
  }
}

// 选择模板
const selectTemplate = (templateId: number) => {
  selectedTemplateId.value = templateId
}

// 选择"不使用模板"
const selectNoTemplate = () => {
  selectedTemplateId.value = null
}

// 预览模板
const previewTemplate = async (template: PaperTemplate) => {
  if (!authStore.token) return

  previewTemplateData.value = template
  showPreviewDialog.value = true

  try {
    const result = await templateAPI.getTemplateContent(authStore.token, template.id)
    templateContent.value = result.content
  } catch (error) {
    MessagePlugin.error('加载模板内容失败')
    console.error('加载模板内容失败:', error)
    templateContent.value = '模板内容加载失败'
  }
}

// 关闭预览对话框
const closePreviewDialog = () => {
  showPreviewDialog.value = false
  previewTemplateData.value = null
  templateContent.value = ''
}

// 跳转到模板页面
const goToTemplatePage = () => {
  router.push('/template')
}

// 上传成功回调
const onUploadSuccess = (response: any, file: any) => {
  MessagePlugin.success(`文件 ${file.name} 上传成功`)
}

// 上传失败回调
const onUploadFail = (error: any, file: any) => {
  MessagePlugin.error(`文件 ${file.name} 上传失败`)
}

// 格式化上传响应
const formatUploadResponse = (response: any) => {
  return {
    name: response.filename,
    url: response.file_url,
    status: 'success',
  }
}

// 开始工作
const startWork = async () => {
  if (!researchQuestion.value.trim() || !authStore.token) {
    return
  }

  creatingWork.value = true

  try {
    // 创建工作数据，标题用空格作为初始值
    const workData: WorkCreate = {
      title: ' ', // 用空格作为初始标题，后续由AI生成
      description: `研究问题：${researchQuestion.value}\n${selectedTemplateId.value ? `使用模板：${getSelectedTemplateName()}` : '不使用模板，从头开始创建'}\n`,
      tags: '研究,论文,AI生成',
      template_id: selectedTemplateId.value || undefined, // 如果为null则传undefined
    }

    // 调用API创建工作
    const newWork = await workspaceAPI.createWork(authStore.token, workData)
    tempWorkId.value = newWork.work_id

    // 将研究问题存储到localStorage，供Work.vue使用
    localStorage.setItem('pendingQuestion', researchQuestion.value)

    // 如果有附件，先上传附件
    if (uploadedFiles.value.length > 0) {
      await uploadAttachments(newWork.work_id)
    }

    MessagePlugin.success('工作创建成功！')

    // 跳转到工作页面
    router.push(`/work/${newWork.work_id}`)
  } catch (error) {
    console.error('创建工作失败:', error)
    MessagePlugin.error('创建工作失败，请重试')
  } finally {
    creatingWork.value = false
    tempWorkId.value = null
  }
}

// 上传附件
const uploadAttachments = async (workId: string) => {
  if (!authStore.token || uploadedFiles.value.length === 0) return

  const uploadPromises = uploadedFiles.value.map(async (file: any) => {
    try {
      const formData = new FormData()
      formData.append('file', file.raw || file)

      const response = await fetch(
        `${import.meta.env.VITE_API_BASE_URL || ''}/api/works/${workId}/attachment`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${authStore.token}`,
          },
          body: formData,
        }
      )

      if (!response.ok) {
        throw new Error(`上传失败: ${response.statusText}`)
      }

      const result = await response.json()
      console.log('附件上传成功:', result)
      return result
    } catch (error) {
      console.error(`文件 ${file.name} 上传失败:`, error)
      throw error
    }
  })

  try {
    const results = await Promise.allSettled(uploadPromises)
    const failed = results.filter(result => result.status === 'rejected')

    if (failed.length > 0) {
      MessagePlugin.warning(`${failed.length} 个文件上传失败，${results.length - failed.length} 个文件上传成功`)
    } else {
      MessagePlugin.success(`所有 ${results.length} 个附件上传成功`)
    }
  } catch (error) {
    MessagePlugin.error('附件上传过程中出现错误')
  }
}

// 获取选中的模板名称
const getSelectedTemplateName = () => {
  if (selectedTemplateId.value === null) {
    return '不使用模板'
  }
  const template = availableTemplates.value.find((t) => t.id === selectedTemplateId.value)
  return template ? template.name : '未选择'
}

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

// 创建新工作（侧边栏调用）
const createNewTask = () => {
  currentStep.value = 1
  researchQuestion.value = ''
  selectedTemplateId.value = null // 重置为null，表示不使用模板
  uploadedFiles.value = []
  tempWorkId.value = null
}

// 选择历史工作（侧边栏调用）
const selectHistory = (id: number) => {
  activeHistoryId.value = id
  // 侧边栏会处理跳转逻辑，这里只需要更新选中状态
}
</script>

<style scoped>
.home-page {
  display: flex;
  height: 100vh;
  width: 100vw;
  background: #ffffff;
  overflow: hidden;
}

.home-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 40px 20px;
  overflow-y: auto;
  max-width: 800px;
  margin: 0 auto;
}

.welcome-header {
  text-align: center;
  margin-bottom: 60px;
}

.welcome-header h1 {
  font-size: 3rem;
  color: #2c3e50;
  margin-bottom: 16px;
  font-weight: 700;
}

.welcome-header p {
  font-size: 1.2rem;
  color: #7f8c8d;
  margin: 0;
}

.main-task-area {
  width: 100%;
  max-width: 800px;
}

.input-container:focus-within {
  border-color: #0052d9;
  background: white;
}

.input-wrapper {
  position: relative;
}

.question-input {
  width: 100%;
  flex: 1;
  margin-bottom: 0;
}

/* 按钮容器 */
.button-container {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-top: 16px;
  width: 100%;
}

/* 附件按钮 - 左下角 */
.attachment-btn {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-upload {
  display: inline-block;
}

.file-count {
  background: #0052d9;
  color: white;
  border-radius: 50%;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}

.attachment-hint {
  color: #999;
  font-size: 12px;
}

/* 下一步按钮 - 右下角 */
.next-btn-wrapper {
  display: flex;
  justify-content: flex-end;
  float: right;
}

.next-btn {
  min-width: 80px;
}

/* 加载状态 */
.loading-state {
  text-align: center;
  padding: 40px 20px;
}

.loading-state p {
  margin-top: 16px;
  color: #7f8c8d;
}

/* 模板列表容器 */
.template-list-container {
  width: 100%;
  margin: 24px 0;
}

/* 模板列表项选中状态 */
.t-list-item.selected {
  background-color: #e6f4ff;
  border: 2px solid #0052d9;
  border-radius: 8px;
  box-shadow: 0 0 0 2px rgba(0, 82, 217, 0.1);
}

/* 无模板状态 */
.no-template-state {
  text-align: center;
  padding: 40px 20px;
}

.no-template-icon {
  margin-bottom: 16px;
  color: #c0c4cc;
}

.no-template-state h4 {
  color: #2c3e50;
  margin: 0 0 8px 0;
  font-size: 1.2rem;
}

.no-template-state p {
  color: #7f8c8d;
  margin: 0 0 24px 0;
}

/* 不使用模板选项 */
.no-template-option {
  margin-bottom: 24px;
}

.no-template-card {
  cursor: pointer;
  border: 2px solid #e0e6ed;
  border-radius: 8px;
  padding: 16px;
  transition: all 0.2s ease;
  background-color: #f5f7fa;
}

.no-template-card:hover {
  background-color: #e6f4ff;
  border-color: #0052d9;
}

.no-template-card.selected {
  background-color: #e6f4ff;
  border-color: #0052d9;
  box-shadow: 0 0 0 2px rgba(0, 82, 217, 0.1);
}

.no-template-content {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}

.no-template-text {
  flex: 1;
}

.no-template-text h4 {
  margin: 0 0 8px 0;
  color: #2c3e50;
  font-size: 1.2rem;
  font-weight: 600;
}

.no-template-text p {
  margin: 0;
  color: #7f8c8d;
  font-size: 0.95rem;
  line-height: 1.4;
}

.selection-indicator {
  margin-left: auto;
}

/* 分割线 */
.template-divider {
  text-align: center;
  margin: 32px 0;
  position: relative;
}

.template-divider span {
  background-color: #ffffff;
  padding: 0 16px;
  color: #7f8c8d;
  font-size: 0.9rem;
  font-weight: 500;
  position: relative;
  z-index: 1;
}

.template-divider::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  width: 100%;
  height: 1px;
  background-color: #e0e6ed;
  z-index: 0;
}

.step-actions {
  display: flex;
  gap: 16px;
  justify-content: space-between;
}

/* 模板预览样式 */
.template-preview {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.template-info {
  background-color: #f5f7fa;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid #e0e6ed;
}

.template-info p {
  margin: 8px 0;
  color: #2c3e50;
}

.template-content h4 {
  margin: 0 0 12px 0;
  color: #2c3e50;
  font-size: 1.1rem;
}

.content-display {
  border: 1px solid #e0e6ed;
  border-radius: 8px;
  overflow: hidden;
}

@media (max-width: 768px) {
  .home-container {
    padding: 20px 16px;
  }

  .welcome-header h1 {
    font-size: 2rem;
  }

  .input-container {
    padding: 24px;
  }

  .step-actions {
    flex-direction: column;
    align-items: center;
  }

  .prev-btn,
  .start-btn {
    width: 100%;
    max-width: 300px;
  }
}
</style>
