// 模板管理API服务
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export interface PaperTemplate {
  id: number
  name: string
  description?: string
  category?: string
  file_path: string  // 添加文件路径字段
  created_at: string
  updated_at: string
  is_public: boolean
  created_by: number
}

export interface PaperTemplateCreate {
  name: string
  description?: string
  category?: string
  file_path: string  // 添加文件路径字段
  is_public: boolean
}

export interface PaperTemplateCreateWithContent {
  name: string
  description?: string
  category?: string
  file_path: string
  is_public: boolean
  content: string  // 添加文件内容字段
}

export interface PaperTemplateUpdate {
  name?: string
  description?: string
  category?: string
  file_path?: string  // 允许更新文件路径
  is_public?: boolean
}

class TemplateAPI {
  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`
    
    const config: RequestInit = {
      ...options,
    }

    // 只有在有body且没有明确设置Content-Type时才设置默认的Content-Type
    if (options.body && !(options.headers && 'Content-Type' in options.headers)) {
      config.headers = {
        'Content-Type': 'application/json',
        ...options.headers,
      }
    } else if (options.headers) {
      config.headers = options.headers
    }

    try {
      const response = await fetch(url, config)
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`)
      }
      
      return await response.json()
    } catch (error) {
      if (error instanceof Error) {
        throw error
      }
      throw new Error('网络请求失败')
    }
  }

  // 获取用户模板列表
  async getUserTemplates(token: string, skip: number = 0, limit: number = 100): Promise<PaperTemplate[]> {
    return this.request<PaperTemplate[]>(`/templates?skip=${skip}&limit=${limit}`, {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })
  }

  // 获取公开模板列表
  async getPublicTemplates(skip: number = 0, limit: number = 100): Promise<PaperTemplate[]> {
    return this.request<PaperTemplate[]>(`/templates/public?skip=${skip}&limit=${limit}`)
  }

  // 获取指定模板
  async getTemplate(token: string, templateId: number): Promise<PaperTemplate> {
    return this.request<PaperTemplate>(`/templates/${templateId}`, {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })
  }

  // 创建模板（包含文件内容）
  async createTemplate(token: string, template: PaperTemplateCreateWithContent): Promise<PaperTemplate> {
    return this.request<PaperTemplate>('/templates', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify(template),
    })
  }

  // 更新模板
  async updateTemplate(token: string, templateId: number, template: PaperTemplateUpdate): Promise<PaperTemplate> {
    return this.request<PaperTemplate>(`/templates/${templateId}`, {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify(template),
    })
  }

  // 删除模板
  async deleteTemplate(token: string, templateId: number): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/templates/${templateId}`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })
  }

  // 获取模板文件内容
  async getTemplateContent(token: string, templateId: number): Promise<{ content: string }> {
    return this.request<{ content: string }>(`/templates/${templateId}/content`, {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    })
  }



  // 上传模板文件（创建模板时使用）
  async uploadTemplateFile(token: string, file: File): Promise<{ message: string; file_path: string; content: string }> {
    const formData = new FormData()
    formData.append('file', file)

    const url = `${API_BASE_URL}/files/upload`
    
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
        },
        body: formData,
      })
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`)
      }
      
      return await response.json()
    } catch (error) {
      if (error instanceof Error) {
        throw error
      }
      throw new Error('网络请求失败')
    }
  }
}

export const templateAPI = new TemplateAPI()
