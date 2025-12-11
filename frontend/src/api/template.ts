// 模板管理API服务
import { apiClient } from '@/utils/apiClient'

export interface PaperTemplate {
  id: number
  name: string
  description?: string
  category?: string
  output_format: string // 添加输出格式字段
  file_path: string // 添加文件路径字段
  created_at: string
  updated_at: string
  is_public: boolean
  created_by: number
}

export interface PaperTemplateUpdate {
  name?: string
  description?: string
  category?: string
  output_format?: string // 添加输出格式字段
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
    outputFormat?: string,
  ): Promise<PaperTemplate[]> {
    let url = `/templates?skip=${skip}&limit=${limit}`
    if (outputFormat) {
      url += `&output_format=${outputFormat}`
    }
    return this.request<PaperTemplate[]>(url, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  }

  // 获取公开模板列表
  async getPublicTemplates(
    skip: number = 0,
    limit: number = 100,
    outputFormat?: string,
  ): Promise<PaperTemplate[]> {
    let url = `/templates/public?skip=${skip}&limit=${limit}`
    if (outputFormat) {
      url += `&output_format=${outputFormat}`
    }
    return this.request<PaperTemplate[]>(url)
  }

  // 获取指定模板
  async getTemplate(token: string, templateId: number): Promise<PaperTemplate> {
    return this.request<PaperTemplate>(`/templates/${templateId}`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  }

  // 创建模板（直接上传文件）
  async createTemplate(
    token: string,
    file: File,
    name: string,
    outputFormat: string,
    description?: string,
    category?: string,
    isPublic: boolean = false,
  ): Promise<PaperTemplate> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('name', name)
    formData.append('output_format', outputFormat)
    if (description) formData.append('description', description)
    if (category) formData.append('category', category)
    formData.append('is_public', String(isPublic))

    const response = await fetch(`${import.meta.env.VITE_API_BASE_URL || ''}/templates/upload`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
    })

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || '创建模板失败')
    }

    return response.json()
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

  // 获取模板预览内容（支持不同文件类型）
  async getTemplatePreview(token: string, templateId: number): Promise<{
    type: 'text' | 'image' | 'binary'
    content?: string
    filename: string
    size: number
    mime_type?: string
    download_url?: string
    message?: string
  }> {
    return this.request<{
      type: 'text' | 'image' | 'binary'
      content?: string
      filename: string
      size: number
      mime_type?: string
      download_url?: string
      message?: string
    }>(`/templates/${templateId}/preview`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  }

}

export const templateAPI = new TemplateAPI()
