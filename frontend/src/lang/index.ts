import { createI18n } from 'vue-i18n'
import zh_CN from './zh_CN'
import en_US from './en_US'

const messages = {
  'zh-CN': zh_CN,
  'en-US': en_US,
}

const i18n = createI18n({
  legacy: false, // 使用Composition API模式
  locale: 'zh-CN',
  fallbackLocale: 'en-US',
  messages,
})

export default i18n