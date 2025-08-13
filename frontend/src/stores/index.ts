import { createPinia } from 'pinia'
import { createPersistedState } from 'pinia-plugin-persistedstate'

const pinia = createPinia()

// 添加持久化插件
pinia.use(createPersistedState())

export default pinia

// 导出所有store
export { useAppStore } from './app'
export { useAuthStore } from './auth'