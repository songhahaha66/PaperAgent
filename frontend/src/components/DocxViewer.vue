<template>
  <div class="docx-viewer">
    <div class="file-info">
      <div class="file-header">
        <t-icon name="file-word" theme="default" size="24px" class="file-icon" />
        <div class="file-details">
          <h4 class="file-name">{{ fileInfo.filename }}</h4>
          <div class="file-meta">
            <span class="file-size">{{ formatFileSize(fileInfo.size) }}</span>
            <span class="file-type">{{ fileInfo.mime_type }}</span>
          </div>
        </div>
      </div>

      <div class="file-actions">
        <t-button
          theme="primary"
          variant="outline"
          @click="downloadFile"
          :loading="downloading"
          class="download-btn"
        >
          <template #icon>
            <t-icon name="download" />
          </template>
          下载文件
        </t-button>
      </div>
    </div>

    <div class="preview-area">
      <!-- 加载状态覆盖层 -->
      <div v-if="loading" class="loading-overlay">
        <t-loading text="正在加载文档..." />
      </div>

      <!-- 错误提示 -->
      <div v-if="error" class="error-container">
        <t-alert theme="error" :message="error" />
      </div>

      <!-- 文档容器 -->
      <div ref="docxContainer" class="docx-container"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { renderAsync } from 'docx-preview'

interface DocxFileInfo {
  filename: string
  size: number
  mime_type: string
  download_url: string
  message: string
}

interface Props {
  fileInfo: DocxFileInfo
  workId: string
  token: string
}

const props = defineProps<Props>()
const docxContainer = ref<HTMLElement | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const downloading = ref(false)

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

const loadDocx = async () => {
  try {
    error.value = null
    loading.value = true

    const url = `${import.meta.env.VITE_API_BASE_URL || ''}${props.fileInfo.download_url}`
    console.log('DocxViewer - 加载文件:', url)

    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${props.token}`,
      },
    })

    if (!response.ok) {
      throw new Error(`加载失败: ${response.status} ${response.statusText}`)
    }

    const blob = await response.blob()
    console.log('DocxViewer - Blob大小:', blob.size)

    if (blob.size === 0) {
      throw new Error('文件为空，无法预览')
    }

    await nextTick()

    if (!docxContainer.value) {
      throw new Error('文档容器未准备好')
    }

    console.log('DocxViewer - 开始渲染...')
    await renderAsync(blob, docxContainer.value, undefined, {
      className: 'docx-preview-content',
      inWrapper: true,  // 使用包装器以支持分页
      ignoreWidth: false,
      ignoreHeight: false,  // 保持页面高度
      ignoreFonts: false,
      breakPages: true,
      ignoreLastRenderedPageBreak: false,
      experimental: false,
      trimXmlDeclaration: true,
      useBase64URL: false,
      renderHeaders: true,
      renderFooters: true,
      renderFootnotes: true,
      renderEndnotes: true,
    })
    console.log('DocxViewer - 渲染完成')
  } catch (err) {
    console.error('加载 DOCX 文件失败:', err)
    error.value = err instanceof Error ? err.message : '加载文档失败，请重试'
  } finally {
    loading.value = false
  }
}

const downloadFile = async () => {
  downloading.value = true

  try {
    const url = `${import.meta.env.VITE_API_BASE_URL || ''}${props.fileInfo.download_url}`

    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${props.token}`,
      },
    })

    if (!response.ok) {
      throw new Error(`下载失败: ${response.status} ${response.statusText}`)
    }

    const blob = await response.blob()
    const downloadUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = props.fileInfo.filename

    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(downloadUrl)

    MessagePlugin.success('文件下载成功')
  } catch (err) {
    console.error('下载文件失败:', err)
    MessagePlugin.error('文件下载失败，请重试')
  } finally {
    downloading.value = false
  }
}

onMounted(() => {
  loadDocx()
})
</script>

<style scoped>
.docx-viewer {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  background: #f5f7fa;
  border-radius: 8px;
  border: 1px solid #e0e6ed;
  box-sizing: border-box;
}

.file-info {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.file-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.file-icon {
  color: #2b579a;
  flex-shrink: 0;
  margin-top: 2px;
}

.file-details {
  flex: 1;
  min-width: 0;
}

.file-name {
  margin: 0 0 8px 0;
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
  word-break: break-all;
}

.file-meta {
  display: flex;
  gap: 16px;
  align-items: center;
}

.file-size,
.file-type {
  font-size: 14px;
  color: #7f8c8d;
}

.file-actions {
  flex-shrink: 0;
  align-self: flex-start;
}

.download-btn {
  min-width: 100px;
}

.preview-area {
  position: relative;
  max-height: 600px;
  overflow-y: auto;
  overflow-x: auto;
}

.loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  border-radius: 4px;
}

.error-container {
  padding: 20px;
}

.docx-container {
  background: white;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  min-width: fit-content;
  padding: 20px;
}

.docx-container :deep(.docx-wrapper) {
  background: #e8e8e8 !important;
  padding: 20px !important;
}

.docx-container :deep(section.docx) {
  background: white !important;
  padding: 40px 60px !important;
  margin: 0 auto 20px !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
  box-sizing: border-box !important;
  min-height: 800px;
}

.docx-container :deep(table) {
  table-layout: auto;
}

.docx-container :deep(img) {
  max-width: 100%;
  height: auto;
}

.docx-container :deep(p),
.docx-container :deep(h1),
.docx-container :deep(h2),
.docx-container :deep(h3),
.docx-container :deep(h4),
.docx-container :deep(h5),
.docx-container :deep(h6) {
  word-wrap: break-word;
  overflow-wrap: break-word;
}

@media (max-width: 768px) {
  .file-info {
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
  }

  .file-header {
    justify-content: center;
  }

  .file-actions {
    text-align: center;
  }

  .download-btn {
    width: 100%;
  }
}
</style>
