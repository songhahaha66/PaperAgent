export interface GenerationSettings {
  max_output_tokens?: number
  context_window?: number | null
  reasoning_effort?: string | null
  prices?: { input_per_million: number; output_per_million: number; cached_input_per_million?: number | null } | null
}

export interface ModelCapabilities {
  max_context_tokens: number | null
  reasoning_efforts: string[]
  source: 'provider' | 'sdk' | 'unknown'
}

// 模型配置API服务
import { apiClient } from '@/utils/apiClient'

export interface ModelConfig {
  id: number
  type: string // brain, code, writing, jev
  model_id: string
  base_url: string
  timeout_seconds?: number | null
  thinking_enabled?: boolean
  generation_settings?: GenerationSettings
  is_active: boolean
  created_at: string
}

export interface ModelConfigCreate {
  type: string
  model_id: string
  base_url: string
  timeout_seconds?: number | null
  thinking_enabled?: boolean
  generation_settings?: GenerationSettings
  api_key: string
}

export interface ModelConfigUpdate {
  model_id?: string
  base_url?: string
  timeout_seconds?: number | null
  thinking_enabled?: boolean
  generation_settings?: GenerationSettings
  api_key?: string
  is_active?: boolean
}

export interface ModelReadiness {
  ready: boolean
  roles: Record<string, boolean>
  research_ready: boolean
  jev: {
    source: 'user' | 'deployment' | 'missing'
    model_id?: string
    base_url?: string
    timeout_seconds?: number
  }
}

class ModelConfigAPI {
  async capabilities(modelId: string): Promise<ModelCapabilities> {
    return apiClient.request(`/model-configs/capabilities?model_id=${encodeURIComponent(modelId)}`)
  }
  async readiness(): Promise<ModelReadiness> {
    return apiClient.request('/model-configs/readiness')
  }
  async applyDefault(): Promise<ModelConfig[]> {
    return apiClient.request<ModelConfig[]>('/model-configs/apply-default', { method: 'POST' })
  }

  async testConnection(config: {
    type: string
    model_id: string
    base_url: string
    timeout_seconds?: number | null
    thinking_enabled?: boolean
  generation_settings?: GenerationSettings
    api_key?: string
  }): Promise<{ ok: boolean }> {
    return apiClient.request<{ ok: boolean }>('/model-configs/test', {
      method: 'POST',
      body: JSON.stringify(config),
    })
  }

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
  async getConfig(type: 'brain' | 'code' | 'writing' | 'jev'): Promise<ModelConfig> {
    return this.getModelConfigByType(type)
  }

  // 便捷接口：创建指定类型的配置
  async createConfig(
    type: 'brain' | 'code' | 'writing' | 'jev',
    config: Omit<ModelConfigCreate, 'type'>,
  ): Promise<ModelConfig> {
    return this.createModelConfig({
      ...config,
      type,
    })
  }
}

export const modelConfigAPI = new ModelConfigAPI()
