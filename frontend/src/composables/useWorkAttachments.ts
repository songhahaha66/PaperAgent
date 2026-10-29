import { ref, onUnmounted, type Ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { attachmentAPI } from '@/api/workspace'
import { useAuthStore } from '@/stores/auth'

export function useWorkAttachments(options: {
  workId: Ref<string>
  onUploaded: (filename: string, size: number) => Promise<void>
}) {
  const auth = useAuthStore()
  const chatFileInput = ref<HTMLInputElement | null>(null)
  const uploading = ref(false)
  let disposed = false
  onUnmounted(() => {
    disposed = true
  })

  async function handleAttachmentChange(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (file) await uploadAttachment(file)
  }

  async function uploadAttachment(file: File) {
    if (disposed || uploading.value) return
    if (!auth.token || !options.workId.value) {
      MessagePlugin.error('请先登录并选择工作空间')
      return
    }
    uploading.value = true
    try {
      MessagePlugin.info(`正在上传文件: ${file.name}`)
      const result = await attachmentAPI.uploadAttachment(auth.token, options.workId.value, file)
      if (disposed) return
      MessagePlugin.success(`文件上传成功: ${result.attachment.original_filename}`)
      await options.onUploaded(result.attachment.original_filename, result.attachment.file_size)
    } catch (error) {
      if (!disposed)
        MessagePlugin.error(`文件上传失败: ${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      uploading.value = false
    }
  }

  return { chatFileInput, uploading, handleAttachmentChange, uploadAttachment }
}
