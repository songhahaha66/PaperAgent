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
                placeholder="请详细描述您要研究的学术问题，例如：计算100平方米家庭使用空调的降温速率研究..."
                :autosize="{ minRows: 5, maxRows: 8 }"
                class="question-input"
              />
              
              <!-- 按钮容器 -->
              <div class="button-container">
                <!-- 附件按钮 - 左下角 -->
                <div class="attachment-btn">
                  <t-upload
                    v-model="uploadedFiles"
                    :action="uploadAction"
                    :headers="uploadHeaders"
                    :data="uploadData"
                    :auto-upload="false"
                    :multiple="true"
                    :accept="'.pdf,.doc,.docx,.tex,.txt'"
                    :max="5"
                    :format-response="formatUploadResponse"
                    @success="onUploadSuccess"
                    @fail="onUploadFail"
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
                  :class="{ 'selected': selectedTemplateId === template.id }"
                  @click="selectTemplate(template.id)"
                >
                  <t-list-item-meta
                    :title="template.name"
                    :description="template.description||'暂无描述'"
                  >
                    
                  </t-list-item-meta>
                  
                  <template #action>
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
              <t-button 
                theme="default" 
                size="large" 
                @click="prevStep"
                class="prev-btn"
              >
                上一步
              </t-button>
              
              <t-button 
                theme="success" 
                size="large" 
                @click="startWork"
                :disabled="!selectedTemplateId"
                class="start-btn"
              >
                <template #icon>
                  <t-icon name="play" />
                </template>
                开始工作
              </t-button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import { MessagePlugin } from 'tdesign-vue-next';
import { templateAPI, type PaperTemplate } from '@/api/template';
import Sidebar from '@/components/Sidebar.vue';

const router = useRouter();
const authStore = useAuthStore();

// 侧边栏折叠状态
const isSidebarCollapsed = ref(false);

// 任务创建步骤
const currentStep = ref(1);

// 研究问题
const researchQuestion = ref('');

// 选择的模板ID
const selectedTemplateId = ref<number | null>(null);

// 上传的文件
const uploadedFiles = ref([]);

// 可用模板列表
const availableTemplates = ref<PaperTemplate[]>([]);

// 加载状态
const loading = ref(false);

// 用户名
const userName = computed(() => authStore.currentUser?.username || '用户');

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

// 上传相关配置
const uploadAction = 'http://localhost:8000/upload'; // 替换为实际的上传接口
const uploadHeaders = {
  'Authorization': `Bearer ${authStore.token}`
};
const uploadData = {
  type: 'research_attachment'
};

// 加载用户模板
const loadUserTemplates = async () => {
  if (!authStore.token) return;
  
  loading.value = true;
  try {
    const templates = await templateAPI.getUserTemplates(authStore.token);
    availableTemplates.value = templates;
  } catch (error) {
    console.error('加载模板失败:', error);
    MessagePlugin.error('加载模板失败');
  } finally {
    loading.value = false;
  }
};

// 下一步
const nextStep = () => {
  if (currentStep.value === 1) {
    // 进入第二步时加载模板
    loadUserTemplates();
  }
  
  if (currentStep.value < 2) {
    currentStep.value++;
  }
};

// 上一步
const prevStep = () => {
  if (currentStep.value > 1) {
    currentStep.value--;
  }
};

// 选择模板
const selectTemplate = (templateId: number) => {
  selectedTemplateId.value = templateId;
};

// 跳转到模板页面
const goToTemplatePage = () => {
  router.push('/template');
};

// 上传成功回调
const onUploadSuccess = (response: any, file: any) => {
  MessagePlugin.success(`文件 ${file.name} 上传成功`);
};

// 上传失败回调
const onUploadFail = (error: any, file: any) => {
  MessagePlugin.error(`文件 ${file.name} 上传失败`);
};

// 格式化上传响应
const formatUploadResponse = (response: any) => {
  return {
    name: response.filename,
    url: response.file_url,
    status: 'success'
  };
};

// 开始工作
const startWork = () => {
  if (researchQuestion.value.trim() && selectedTemplateId.value) {
    // 生成新的工作ID
    const newWorkId = Date.now();
    
    // 创建新的工作记录
    const newWork = {
      id: newWorkId,
      title: researchQuestion.value.length > 50 ? researchQuestion.value.substring(0, 50) + '...' : researchQuestion.value,
      date: new Date().toLocaleString(),
      content: `研究问题：${researchQuestion.value}\n使用模板：${getSelectedTemplateName()}\n附件数量：${uploadedFiles.value.length}`
    };
    
    // 添加到历史记录
    historyItems.value.unshift(newWork);
    
    // 跳转到工作页面
    router.push(`/work/${newWorkId}`);
  }
};

// 获取选中的模板名称
const getSelectedTemplateName = () => {
  const template = availableTemplates.value.find(t => t.id === selectedTemplateId.value);
  return template ? template.name : '未选择';
};

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value;
};

// 创建新工作（侧边栏调用）
const createNewTask = () => {
  currentStep.value = 1;
  researchQuestion.value = '';
  selectedTemplateId.value = null;
  uploadedFiles.value = [];
};

// 选择历史工作（侧边栏调用）
const selectHistory = (id: number) => {
  activeHistoryId.value = id;
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
  justify-content: space-between;
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

/* 下一步按钮 - 右下角 */
.next-btn-wrapper {
  display: flex;
  justify-content: flex-end;
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



.step-actions {
  display: flex;
  gap: 16px;
  justify-content: space-between;
}



.start-btn {
  min-width: 140px;
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
