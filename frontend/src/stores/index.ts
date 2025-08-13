import { createPinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'

const pinia = createPinia()

// 添加持久化插件
pinia.use(piniaPluginPersistedstate)

export default pinia

// 导出所有store
export { useAppStore } from './app'
export { useAuthStore } from './auth'