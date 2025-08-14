import { useAuthStore } from '@/stores/auth'
import router from '@/router'

// 通用API客户端
export class ApiClient {
  private baseURL: string

  constructor(baseURL: string) {
    this.baseURL = baseURL
  }

  // 处理401错误的通用方法
  private handleUnauthorized() {
    const authStore = useAuthStore()
    authStore.logout()
    router.push('/login')
    throw new Error('认证失败，请重新登录')
  }

  // 通用请求方法
  async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseURL}${endpoint}`
    
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
      
      // 检查401错误并自动处理
      if (response.status === 401) {
        this.handleUnauthorized()
      }
      
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

  // 文件上传方法
  async uploadFile<T>(endpoint: string, file: File, token: string): Promise<T> {
    const url = `${this.baseURL}${endpoint}`
    const formData = new FormData()
    formData.append('file', file)

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
        },
        body: formData,
      })
      
      // 检查401错误并自动处理
      if (response.status === 401) {
        this.handleUnauthorized()
      }
      
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

// 创建默认实例
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
export const apiClient = new ApiClient(API_BASE_URL)
