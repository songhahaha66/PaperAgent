import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authAPI, type UserResponse, type UserRegisterData, type UserLoginData } from '@/api/auth'

export const useAuthStore = defineStore(
  'auth',
  () => {
    // 状态
    const user = ref<UserResponse | null>(null)
    const token = ref<string | null>(localStorage.getItem('auth_token'))
    const loading = ref(false)
    const initialized = ref(false) // 新增：标记是否已初始化

    // 计算属性
    const isAuthenticated = computed(() => !!token.value && !!user.value)
    const currentUser = computed(() => user.value)

    // 初始化认证状态
    async function initializeAuth() {
      if (!token.value) {
        initialized.value = true
        return
      }

      try {
        loading.value = true
        const userData = await authAPI.getCurrentUser(token.value)
        user.value = userData
      } catch (error) {
        console.error('获取用户信息失败:', error)
        // Token无效，清除本地存储
        logout()
      } finally {
        loading.value = false
        initialized.value = true
      }
    }

    // 加载当前用户信息
    async function loadCurrentUser() {
      if (!token.value) return

      try {
        loading.value = true
        const userData = await authAPI.getCurrentUser(token.value)
        user.value = userData
      } catch (error) {
        console.error('获取用户信息失败:', error)
        // Token无效，清除本地存储
        logout()
      } finally {
        loading.value = false
      }
    }

    // 用户注册
    async function register(userData: UserRegisterData) {
      try {
        loading.value = true
        const newUser = await authAPI.register(userData)
        return { success: true, user: newUser }
      } catch (error) {
        return {
          success: false,
          error: error instanceof Error ? error.message : '注册失败',
        }
      } finally {
        loading.value = false
      }
    }

    // 用户登录
    async function login(credentials: UserLoginData) {
      try {
        loading.value = true
        const tokenResponse = await authAPI.login(credentials)

        // 保存token
        token.value = tokenResponse.access_token
        localStorage.setItem('auth_token', tokenResponse.access_token)

        // 获取用户信息
        await loadCurrentUser()

        return { success: true }
      } catch (error) {
        return {
          success: false,
          error: error instanceof Error ? error.message : '登录失败',
        }
      } finally {
        loading.value = false
      }
    }

    // 用户登出
    function logout() {
      user.value = null
      token.value = null
      localStorage.removeItem('auth_token')
      initialized.value = false
    }

    return {
      // 状态
      user,
      token,
      loading,
      initialized, // 新增：导出初始化状态

      // 计算属性
      isAuthenticated,
      currentUser,

      // 方法
      register,
      login,
      logout,
      loadCurrentUser,
      initializeAuth, // 新增：导出初始化方法
    }
  },
  {
    persist: {
      key: 'auth-store',
      storage: localStorage,
      paths: ['token'],
    } as any,
  },
)
