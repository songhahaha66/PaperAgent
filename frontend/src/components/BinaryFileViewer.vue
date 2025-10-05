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

    <div class="file-message">
      <t-alert theme="info" message="这是一个二进制文件，请使用下载按钮查看文件内容" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
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
    const url = `${import.meta.env.VITE_API_BASE_URL || ''}${props.fileInfo.download_url}`

    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${props.token}`,
      },
    })

    if (!response.ok) {
      throw new Error(`下载失败: ${response.status} ${response.statusText}`)
    }

    // 获取文件blob
    const blob = await response.blob()

    // 创建下载链接
    const downloadUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = props.fileInfo.filename

    // 触发下载
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)

    // 清理URL对象
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
}

.file-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.file-header {
  display: flex;
  align-items: center;
  gap: 16px;
}

.file-icon {
  color: #0052d9;
  flex-shrink: 0;
}

.file-details {
  flex: 1;
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

.file-size, .file-type {
  font-size: 14px;
  color: #7f8c8d;
}

.file-actions {
  flex-shrink: 0;
}

.download-btn {
  min-width: 100px;
}

.file-message {
  margin-top: 8px;
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