// 模型配置API服务
import { apiClient } from '@/utils/apiClient'

export interface ModelConfig {
  id: number
  type: string  // brain(中枢大脑), code(代码实验), writing(论文写作)
  model_id: string
  base_url: string
  is_active: boolean
  created_at: string
}

export interface ModelConfigCreate {
  type: string
  model_id: string
  base_url: string
  api_key: string
}

export interface ModelConfigUpdate {
  model_id?: string
  base_url?: string
  api_key?: string
  is_active?: boolean
}

class ModelConfigAPI {
  // 获取所有模型配置
  async getAllModelConfigs(skip: number = 0, limit: number = 100): Promise<ModelConfig[]> {
    return apiClient.request<ModelConfig[]>(`/model-configs?skip=${skip}&limit=${limit}`)
  }

  // 根据ID获取模型配置
  async getModelConfig(configId: number): Promise<ModelConfig> {
    return apiClient.request<ModelConfig>(`/model-configs/${configId}`)
  }

  // 根据类型获取模型配置
  async getModelConfigByType(configType: string): Promise<ModelConfig> {
    return apiClient.request<ModelConfig>(`/model-configs/type/${configType}`)
  }

  // 创建模型配置
  async createModelConfig(config: ModelConfigCreate): Promise<ModelConfig> {
    return apiClient.request<ModelConfig>('/model-configs', {
      method: 'POST',
      body: JSON.stringify(config),
    })
  }

  // 更新模型配置
  async updateModelConfig(configId: number, config: ModelConfigUpdate): Promise<ModelConfig> {
    return apiClient.request<ModelConfig>(`/model-configs/${configId}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    })
  }

  // 删除模型配置
  async deleteModelConfig(configId: number): Promise<{ message: string }> {
    return apiClient.request<{ message: string }>(`/model-configs/${configId}`, {
      method: 'DELETE',
    })
  }

  // 清空所有模型配置
  async clearAllModelConfigs(): Promise<{ message: string }> {
    return apiClient.request<{ message: string }>('/model-configs', {
      method: 'DELETE',
    })
  }

  // 便捷接口：获取指定类型的配置
  async getConfig(type: 'brain' | 'code' | 'writing'): Promise<ModelConfig> {
    return this.getModelConfigByType(type)
  }

  // 便捷接口：创建指定类型的配置
  async createConfig(type: 'brain' | 'code' | 'writing', config: Omit<ModelConfigCreate, 'type'>): Promise<ModelConfig> {
    return this.createModelConfig({
      ...config,
      type
    })
  }
}

export const modelConfigAPI = new ModelConfigAPI()
