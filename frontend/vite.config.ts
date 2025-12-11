import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  server: {
    host: '0.0.0.0', // 允许局域网访问
    port: 5173,       // 明确指定端口
    strictPort: true, // 如果端口被占用则失败而不是尝试下一个端口
  },
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            // TDesign 优先判断（因为包含 vue 字样）
            if (id.includes('tdesign-icons')) {
              return 'vendor-tdesign-icons'
            }
            if (id.includes('tdesign')) {
              return 'vendor-tdesign-core'
            }
            // highlight.js 按语言拆分
            if (id.includes('highlight.js/lib/languages')) {
              return 'vendor-highlight-langs'
            }
            if (id.includes('highlight.js')) {
              return 'vendor-highlight-core'
            }
            // Vue 生态
            if (id.includes('/vue-router/')) {
              return 'vendor-vue-router'
            }
            if (id.includes('/pinia')) {
              return 'vendor-pinia'
            }
            if (id.includes('/vue/') || id.includes('/@vue/')) {
              return 'vendor-vue'
            }
            if (id.includes('vue-i18n')) {
              return 'vendor-i18n'
            }
            // 其他
            if (id.includes('katex')) {
              return 'vendor-katex'
            }
            if (id.includes('markdown-it')) {
              return 'vendor-markdown'
            }
            if (id.includes('docx-preview')) {
              return 'vendor-docx'
            }
          }
        },
      },
    },
  },
})
