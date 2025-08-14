<template>
  <div class="api-key-page">
    <Sidebar 
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      :history-items="historyItems"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
    />
    
    <div class="api-key-container">
      <div class="page-header">
        <h1>API Key 配置</h1>
        <p>配置不同模型服务的API密钥和连接信息</p>
        <p>此页面不回显api-key，这是正常现象</p>
      </div>

      <div class="config-grid">
        <t-card 
          v-for="config in configs" 
          :key="config.type"
          class="config-card" 
          :header="config.title"
        >
          <t-form 
            :ref="(el) => setFormRef(el, config.type)"
            :data="config.data" 
            @submit="() => saveConfig(config.type)" 
            layout="vertical"
            :rules="formRules"
          >
            <t-form-item label="API Key" name="apiKey" :rules="formRules.apiKey">
              <t-input 
                v-model="config.data.apiKey" 
                type="password" 
                :placeholder="`请输入${config.title}的API Key`"
                clearable
              />
            </t-form-item>
            <t-form-item label="Base URL" name="baseUrl" :rules="formRules.baseUrl">
              <t-input 
                v-model="config.data.baseUrl" 
                placeholder="例如: https://api.openai.com/v1"
                clearable
              />
            </t-form-item>
            <t-form-item label="Model ID" name="modelId" :rules="formRules.modelId">
              <t-input 
                v-model="config.data.modelId" 
                placeholder="例如: gpt-4"
                clearable
              />
            </t-form-item>
            <t-form-item>
              <div class="button-container">
                <t-button 
                  theme="primary" 
                  type="submit" 
                  :loading="saving[config.type]" 
                  size="middle"
                  :disabled="!isFormValid(config.type)"
                >
                  {{ config.data.id ? '更新配置' : '保存配置' }}
                </t-button>
                <t-button v-if="config.data.id" theme="danger" @click="() => deleteConfig(config.type)" size="middle">
                  删除配置
                </t-button>
              </div>
            </t-form-item>
          </t-form>
        </t-card>
      </div>

      <div class="global-actions">
        <t-button theme="default" @click="loadAllConfigs" :loading="loading">
          重新加载配置
        </t-button>
        <t-button theme="danger" @click="clearAllConfigs">
          清空所有配置
        </t-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import type { FormInstanceFunctions } from 'tdesign-vue-next'
import Sidebar from '@/components/Sidebar.vue'
import { modelConfigAPI } from '@/api/modelConfig'

// 配置数据结构
interface ModelConfigForm {
  id?: number
  apiKey: string
  baseUrl: string
  modelId: string
}

const router = useRouter()

// 侧边栏状态
const isSidebarCollapsed = ref(false)

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
])

// 当前选中的历史工作ID
const activeHistoryId = ref<number | null>(null)

// 表单引用
const formRefs = reactive<Record<string, FormInstanceFunctions | null>>({
  brain: null,
  code: null,
  writing: null
})

// 表单验证规则
const formRules = {
  apiKey: [{ required: true, message: '请输入API Key', trigger: 'blur' }],
  baseUrl: [
    { required: true, message: '请输入Base URL', trigger: 'blur' },
    { 
      pattern: /^https?:\/\/.+/, 
      message: '请输入有效的URL地址', 
      trigger: 'blur' 
    }
  ],
  modelId: [{ required: true, message: '请输入Model ID', trigger: 'blur' }]
}

// 配置数据 - 使用循环渲染，大大减少重复代码
const configs = reactive([
  {
    type: 'brain' as const,
    title: '中枢大脑',
    data: reactive<ModelConfigForm>({
      apiKey: '',
      baseUrl: '',
      modelId: ''
    })
  },
  {
    type: 'code' as const,
    title: '代码实验',
    data: reactive<ModelConfigForm>({
      apiKey: '',
      baseUrl: '',
      modelId: ''
    })
  },
  {
    type: 'writing' as const,
    title: '论文写作',
    data: reactive<ModelConfigForm>({
      apiKey: '',
      baseUrl: '',
      modelId: ''
    })
  }
])

// 保存状态
const saving = reactive({
  brain: false,
  code: false,
  writing: false
})

const loading = ref(false)

// 设置表单引用
const setFormRef = (el: any, type: string) => {
  if (el) {
    formRefs[type] = el
  }
}

// 检查表单是否有效
const isFormValid = (type: string): boolean => {
  const config = configs.find(c => c.type === type)
  if (!config) return false
  
  return config.data.apiKey.trim() !== '' && 
         config.data.baseUrl.trim() !== '' && 
         config.data.modelId.trim() !== '' &&
         /^https?:\/\/.+/.test(config.data.baseUrl.trim())
}

// 验证表单
const validateForm = async (type: string): Promise<boolean> => {
  const formRef = formRefs[type]
  if (!formRef) return false
  
  try {
    const result = await formRef.validate()
    return result === true
  } catch (error) {
    return false
  }
}

// 侧边栏事件处理
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

const createNewTask = () => {
  router.push('/home')
}

const selectHistory = (id: number) => {
  router.push(`/work/${id}`)
}

// 通用保存配置方法
const saveConfig = async (type: 'brain' | 'code' | 'writing') => {
  const config = configs.find(c => c.type === type)
  if (!config) return

  // 先验证表单
  const isValid = await validateForm(type)
  if (!isValid) {
    MessagePlugin.warning('请检查表单输入，确保所有必填字段都已填写且格式正确')
    return
  }

  saving[type] = true
  try {
    if (config.data.id) {
      // 更新配置
      await modelConfigAPI.updateModelConfig(config.data.id, {
        model_id: config.data.modelId,
        base_url: config.data.baseUrl,
        api_key: config.data.apiKey
      })
      MessagePlugin.success(`${config.title}配置更新成功`)
    } else {
      // 创建配置
      const result = await modelConfigAPI.createConfig(type, {
        model_id: config.data.modelId,
        base_url: config.data.baseUrl,
        api_key: config.data.apiKey
      })
      config.data.id = result.id
      MessagePlugin.success(`${config.title}配置保存成功`)
    }
  } catch (error: any) {
    MessagePlugin.error(error.message || '保存失败，请重试')
  } finally {
    saving[type] = false
  }
}

// 通用删除配置方法
const deleteConfig = async (type: 'brain' | 'code' | 'writing') => {
  const config = configs.find(c => c.type === type)
  if (!config || !config.data.id) return

  try {
    await modelConfigAPI.deleteModelConfig(config.data.id)
    config.data = { apiKey: '', baseUrl: '', modelId: '' }
    MessagePlugin.success(`${config.title}配置已删除`)
  } catch (error: any) {
    MessagePlugin.error(error.message || '删除失败')
  }
}

// 加载配置方法
const loadAllConfigs = async () => {
  loading.value = true
  try {
    // 加载各个配置
    for (const config of configs) {
      try {
        const result = await modelConfigAPI.getConfig(config.type)
        config.data = {
          id: result.id,
          apiKey: '', // 安全考虑，不显示api_key
          baseUrl: result.base_url,
          modelId: result.model_id
        }
      } catch (error) {
        config.data = { apiKey: '', baseUrl: '', modelId: '' }
      }
    }
    MessagePlugin.success('配置加载成功')
  } catch (error: any) {
    MessagePlugin.error(error.message || '配置加载失败')
  } finally {
    loading.value = false
  }
}

// 清空配置方法
const clearAllConfigs = async () => {
  try {
    await modelConfigAPI.clearAllModelConfigs()
    for (const config of configs) {
      config.data = { apiKey: '', baseUrl: '', modelId: '' }
    }
    MessagePlugin.success('所有配置已清空')
  } catch (error: any) {
    MessagePlugin.error(error.message || '清空配置失败')
  }
}

// 组件挂载时加载配置
onMounted(async () => {
  await loadAllConfigs()
})
</script>

<style scoped>
.api-key-page {
  display: flex;
  height: 100vh;
  width: 100vw;
  background: #ffffff;
  overflow: hidden;
}

.api-key-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 40px 20px;
  overflow-y: auto;
}

.page-header {
  text-align: center;
  margin-bottom: 40px;
}

.page-header h1 {
  font-size: 2.5rem;
  color: #2c3e50;
  margin-bottom: 16px;
  font-weight: 700;
}

.page-header p {
  font-size: 1.1rem;
  color: #7f8c8d;
  margin: 0;
}

.config-grid {
  display: flex;
  flex-direction: column;
  gap: 24px;
  margin-bottom: 40px;
}

.config-card {
  width: 100%;
  min-height: 320px;
}

.button-container {
  display: flex;
  justify-content: center;
  gap: 10px;
  margin-top: 16px;
}

.global-actions {
  display: flex;
  justify-content: center;
  gap: 15px;
  max-width: 1200px;
  margin: 0 auto;
}

@media (max-width: 768px) {
  .api-key-container {
    padding: 20px 16px;
  }
  
  .page-header h1 {
    font-size: 2rem;
  }
  
  .config-grid {
    grid-template-columns: 1fr;
    gap: 16px;
  }
}
</style>
