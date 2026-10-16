<template>
  <div class="binary-file-viewer">
    <div class="file-info">
      <div class="file-header">
        <t-icon name="file" theme="default" size="24px" class="file-icon" />
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
          variant="base"
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

    <p v-if="isPdf" class="pdf-help">如浏览器未显示预览，可下载 PDF 查看完整排版。</p>
    <div v-if="isPdf" class="pdf-preview">
      <iframe v-if="pdfUrl" :src="pdfUrl" :title="fileInfo.filename" class="pdf-frame" />
      <div v-else-if="pdfError" class="file-message">
        <t-alert theme="warning" :message="pdfError" />
      </div>
      <t-loading v-else size="small" text="正在加载 PDF..." />
    </div>

    <div v-else class="file-message">
      <t-alert theme="info" message="此文件类型无法预览，请使用下载按钮查看文件内容" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'

interface BinaryFileInfo {
  filename: string
  size: number
  mime_type: string
  download_url: string
  message: string
}

interface Props {
  fileInfo: BinaryFileInfo
  workId: string
  token: string
}

const props = defineProps<Props>()
const downloading = ref(false)

const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const fetchFile = async (): Promise<Blob> => {
  const response = await fetch(`${apiBase}${props.fileInfo.download_url}`, {
    headers: {
      Authorization: `Bearer ${props.token}`,
    },
  })
  if (!response.ok) {
    throw new Error(`下载失败: ${response.status} ${response.statusText}`)
  }
  return response.blob()
}

// PDF（如 LaTeX 编译出的 paper.pdf）用浏览器内置阅读器预览
const isPdf = computed(
  () =>
    props.fileInfo.mime_type === 'application/pdf' ||
    props.fileInfo.filename.toLowerCase().endsWith('.pdf'),
)
const pdfUrl = ref('')
const pdfError = ref('')

const releasePdf = () => {
  if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value)
  pdfUrl.value = ''
}

const loadPdf = async () => {
  releasePdf()
  pdfError.value = ''
  if (!isPdf.value) return
  try {
    const blob = await fetchFile()
    pdfUrl.value = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
  } catch (error) {
    console.error('加载 PDF 失败:', error)
    pdfError.value = 'PDF 加载失败，请使用下载按钮查看'
  }
}

watch(() => props.fileInfo.download_url, loadPdf, { immediate: true })
onBeforeUnmount(releasePdf)

// 格式化文件大小
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 下载文件
const downloadFile = async () => {
  downloading.value = true

  try {
    const blob = await fetchFile()
    const downloadUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = props.fileInfo.filename

    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(downloadUrl)

    MessagePlugin.success('文件下载成功')
  } catch (error) {
    console.error('下载文件失败:', error)
    MessagePlugin.error('文件下载失败，请重试')
  } finally {
    downloading.value = false
  }
}
</script>

<style scoped>
.binary-file-viewer {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  background: #f5f7fa;
  border-radius: 8px;
  border: 1px solid #e0e6ed;
  overflow: hidden;
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
  color: #0052d9;
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

.file-message {
  margin-top: 8px;
}

.pdf-frame {
  width: 100%;
  height: 75vh;
  border: 1px solid #e0e6ed;
  border-radius: 4px;
  background: #fff;
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
.pdf-help {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  line-height: 1.8;
  margin: 0;
}
</style>
