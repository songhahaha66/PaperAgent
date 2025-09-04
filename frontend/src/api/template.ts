// 模板管理API服务
import { apiClient } from '@/utils/apiClient'

export interface PaperTemplate {
  id: number
  name: string
  description?: string
  category?: string
  file_path: string // 添加文件路径字段
  created_at: string
  updated_at: string
  is_public: boolean
  created_by: number
}

export interface PaperTemplateCreate {
  name: string
  description?: string
  category?: string
  file_path: string // 添加文件路径字段
  is_public: boolean
}

export interface PaperTemplateCreateWithContent {
  name: string
  description?: string
  category?: string
  file_path: string
  is_public: boolean
  content: string // 添加文件内容字段
}

export interface PaperTemplateUpdate {
  name?: string
  description?: string
  category?: string
  file_path?: string // 允许更新文件路径
  is_public?: boolean
}

class TemplateAPI {
  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    return apiClient.request<T>(endpoint, options)
  }

  // 获取用户模板列表
  async getUserTemplates(
    token: string,
    skip: number = 0,
    limit: number = 100,
  ): Promise<PaperTemplate[]> {
    return this.request<PaperTemplate[]>(`/templates?skip=${skip}&limit=${limit}`, {
      headers: {
        Authorization: `Bearer ${token}`,
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
        Authorization: `Bearer ${token}`,
      },
    })
  }

  // 创建模板（包含文件内容）
  async createTemplate(
    token: string,
    template: PaperTemplateCreateWithContent,
  ): Promise<PaperTemplate> {
    return this.request<PaperTemplate>('/templates', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(template),
    })
  }

  // 更新模板
  async updateTemplate(
    token: string,
    templateId: number,
    template: PaperTemplateUpdate,
  ): Promise<PaperTemplate> {
    return this.request<PaperTemplate>(`/templates/${templateId}`, {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(template),
    })
  }

  // 删除模板
  async deleteTemplate(token: string, templateId: number): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/templates/${templateId}`, {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  }

  // 强制删除模板（同时删除引用该模板的工作）
  async forceDeleteTemplate(
    token: string,
    templateId: number,
  ): Promise<{ message: string; deleted_works_count: number }> {
    return this.request<{ message: string; deleted_works_count: number }>(
      `/templates/${templateId}/force`,
      {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${token}`,
        },
      },
    )
  }

  // 获取模板文件内容
  async getTemplateContent(token: string, templateId: number): Promise<{ content: string }> {
    return this.request<{ content: string }>(`/templates/${templateId}/content`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  }

  // 上传模板文件（创建模板时使用）
  async uploadTemplateFile(
    token: string,
    file: File,
  ): Promise<{ message: string; file_path: string; content: string }> {
    return apiClient.uploadFile<{ message: string; file_path: string; content: string }>(
      '/files/upload',
      file,
      token,
    )
  }
}

export const templateAPI = new TemplateAPI()
