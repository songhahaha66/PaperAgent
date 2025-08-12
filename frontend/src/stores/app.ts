import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    title: 'PaperAgent',
    language: 'zh-CN',
  }),
  
  actions: {
    setLanguage(lang: string) {
      this.language = lang
    },
    
    setTitle(title: string) {
      this.title = title
    },
  },
  
  persist: {
    key: 'app-store',
    storage: localStorage,
  },
})