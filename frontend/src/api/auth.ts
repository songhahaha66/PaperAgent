// 用户认证API服务
import { apiClient } from '@/utils/apiClient'

export interface UserRegisterData {
  email: string
  username: string
  password: string
}

export interface UserLoginData {
  email: string
  password: string
}

export interface UserResponse {
  id: number
  email: string
  username: string
  created_at: string
  is_active: boolean
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

class AuthAPI {
  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    return apiClient.request<T>(`/auth${endpoint}`, options)
  }

  // 用户注册
  async register(data: UserRegisterData): Promise<UserResponse> {
    return this.request<UserResponse>('/register', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  // 用户登录
  async login(data: UserLoginData): Promise<TokenResponse> {
    return this.request<TokenResponse>('/login', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  // 获取当前用户信息
  async getCurrentUser(token: string): Promise<UserResponse> {
    return this.request<UserResponse>('/me', {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  }
}

export const authAPI = new AuthAPI()
