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

export interface PasskeyOptionsResponse {
  challenge_id: string
  options: Record<string, any>
}

export interface PasskeyCredential {
  id: number
  name: string
  device_type?: string | null
  backed_up: boolean
  transports?: string[] | null
  created_at: string
  last_used_at?: string | null
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

  async getPasskeyRegisterOptions(): Promise<PasskeyOptionsResponse> {
    return this.request<PasskeyOptionsResponse>('/passkey/register/options', {
      method: 'POST',
    })
  }

  async verifyPasskeyRegister(
    challengeId: string,
    credential: object,
    name?: string,
  ): Promise<PasskeyCredential> {
    return this.request<PasskeyCredential>('/passkey/register/verify', {
      method: 'POST',
      body: JSON.stringify({
        challenge_id: challengeId,
        credential,
        name,
      }),
    })
  }

  async getPasskeyLoginOptions(email?: string): Promise<PasskeyOptionsResponse> {
    return this.request<PasskeyOptionsResponse>('/passkey/login/options', {
      method: 'POST',
      body: JSON.stringify(email ? { email } : {}),
    })
  }

  async verifyPasskeyLogin(
    challengeId: string,
    credential: object,
  ): Promise<TokenResponse> {
    return this.request<TokenResponse>('/passkey/login/verify', {
      method: 'POST',
      body: JSON.stringify({
        challenge_id: challengeId,
        credential,
      }),
    })
  }

  async listPasskeys(): Promise<PasskeyCredential[]> {
    return this.request<PasskeyCredential[]>('/passkey/credentials')
  }

  async renamePasskey(id: number, name: string): Promise<PasskeyCredential> {
    return this.request<PasskeyCredential>(`/passkey/credentials/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    })
  }

  async deletePasskey(id: number): Promise<void> {
    await this.request(`/passkey/credentials/${id}`, {
      method: 'DELETE',
    })
  }
}

export const authAPI = new AuthAPI()
