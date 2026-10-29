import type { ResearchSnapshot } from '@/types/research'
import { ref, computed, watch, onUnmounted, type Ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { useAuthStore } from '@/stores/auth'
import { workspaceFileAPI, type FileInfo, type Work } from '@/api/workspace'
import { isImageFile } from '@/utils/workFiles'
import { useStudioCopy } from '@/lang/studio'

export function useArtifactPreview(options: {
  workId: Ref<string>
  currentWork: Ref<Work | null>
  researchSnapshot: Ref<ResearchSnapshot | null>
  figuresVisible: Readonly<Ref<boolean>>
}) {
  const { workId, currentWork, researchSnapshot, figuresVisible } = options
  const authStore = useAuthStore()
  const { ui, isZh } = useStudioCopy()
  let disposed = false
  const workspaceFiles = ref<FileInfo[]>([])
  const imageUrls = ref<Record<string, string>>({})
  const isFileRefreshing = ref(false)
  const downloadLoading = ref(false)
  const exportLoading = ref(false)
  // Published downloads follow research artifacts. Editable drafts are loaded
  // independently by PaperEditor through paperProjectAPI, even before acceptance.
  const primaryArtifact = computed(() => {
    const names =
      currentWork.value?.output_mode === 'latex'
        ? ['paper.pdf', 'paper.tex']
        : currentWork.value?.output_mode === 'word'
          ? ['paper.docx', 'paper.md']
          : ['paper.md']
    for (const name of names) {
      if (researchSnapshot.value?.has_research && !researchSnapshot.value.artifacts.includes(name))
        continue
      const file = workspaceFiles.value.find(
        (f) => f.type === 'file' && f.path === name && (f.size || 0) > 0,
      )
      if (file) return file
    }
    return null
  })
  const paperPath = computed(() =>
    currentWork.value?.output_mode === 'latex'
      ? primaryArtifact.value?.path || 'paper.tex'
      : 'paper.md',
  )
  const paperAvailable = computed(() => !!primaryArtifact.value)
  const downloadLabel = computed(() =>
    primaryArtifact.value?.name.endsWith('.pdf')
      ? ui.value.downloadPdf
      : primaryArtifact.value?.name.endsWith('.docx')
        ? ui.value.downloadWord
        : primaryArtifact.value?.name.endsWith('.tex')
          ? ui.value.downloadTex
          : ui.value.downloadMd,
  )
  const figureFiles = computed(() =>
    workspaceFiles.value.filter(
      (f) => f.type === 'file' && isImageFile(f.path) && f.category !== 'attachments',
    ),
  )
  async function downloadArtifact() {
    if (!primaryArtifact.value || !authStore.token || disposed) return
    downloadLoading.value = true
    try {
      const data = await workspaceFileAPI.readFile(
        authStore.token,
        workId.value,
        primaryArtifact.value.path,
      )
      let blob: Blob
      if (data.type === 'text')
        blob = new Blob([data.content || ''], {
          type: data.filename.endsWith('.tex')
            ? 'application/x-tex;charset=utf-8'
            : 'text/markdown;charset=utf-8',
        })
      else {
        const response = await fetch(
          `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}${data.download_url}`,
          { headers: { Authorization: `Bearer ${authStore.token}` } },
        )
        if (!response.ok) throw new Error('download')
        blob = await response.blob()
      }
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = data.filename
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      MessagePlugin.error(isZh.value ? '下载失败，请重试' : 'Download failed. Please retry.')
    } finally {
      downloadLoading.value = false
    }
  }
  // Workspace refresh scheduling belongs to useWorkStream.
  const loadWorkspaceFiles = async () => {
    if (!workId.value || !authStore.token || disposed) return

    if (isFileRefreshing.value) {
      return
    }

    try {
      isFileRefreshing.value = true

      const requestedWork = workId.value
      const files = await workspaceFileAPI.listFiles(authStore.token, requestedWork)
      if (!disposed && requestedWork === workId.value) workspaceFiles.value = files
    } catch (error) {
      console.error('加载工作空间文件失败:', error)
      if (!disposed) MessagePlugin.error('加载工作空间文件失败')
    } finally {
      isFileRefreshing.value = false
    }
  }

  // 导出工作空间
  const exportWorkspace = async () => {
    if (!workId.value || !authStore.token || !currentWork.value || disposed) return

    try {
      exportLoading.value = true

      // 调用导出API
      const blob = await workspaceFileAPI.exportWorkspace(authStore.token, workId.value)

      // 创建下载链接
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `workspace_${workId.value}.zip`

      // 触发下载
      document.body.appendChild(link)
      link.click()

      // 清理
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)

      MessagePlugin.success('工作空间导出成功')
    } catch (error) {
      console.error('导出工作空间失败:', error)
      MessagePlugin.error('导出工作空间失败')
    } finally {
      exportLoading.value = false
    }
  }

  // 获取图片的blob URL（带认证）
  const getImageBlobUrl = async (filePath: string): Promise<string> => {
    try {
      const token = authStore.token
      if (!token) {
        throw new Error('未登录')
      }

      const url = workspaceFileAPI.getImageUrl(token, workId.value, filePath)

      const response = await fetch(url, {
        cache: 'no-store',
        headers: {
          Authorization: `Bearer ${token}`,
        },
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const blob = await response.blob()
      return URL.createObjectURL(blob)
    } catch (error) {
      console.error('获取图片失败:', error)
      throw error
    }
  }

  const figureLoads = new Map<string, { version: string }>()
  watch([figuresVisible, figureFiles], async ([visible]) => {
    const versions = new Map(figureFiles.value.map((file) => [file.path, `${file.modified}:${file.size}`]))
    for (const [path, load] of figureLoads) {
      if (versions.get(path) === load.version) continue
      figureLoads.delete(path)
      if (imageUrls.value[path]) URL.revokeObjectURL(imageUrls.value[path])
      delete imageUrls.value[path]
    }
    if (!visible) return
    await Promise.allSettled(
      figureFiles.value
        .filter((file) => !figureLoads.has(file.path))
        .map(async (file) => {
          // The token owns this version, even if an older request finishes later.
          const load = { version: versions.get(file.path)! }
          figureLoads.set(file.path, load)
          try {
            const url = await getImageBlobUrl(file.path)
            if (disposed || figureLoads.get(file.path) !== load) URL.revokeObjectURL(url)
            else imageUrls.value[file.path] = url
          } catch {
            if (figureLoads.get(file.path) === load) figureLoads.delete(file.path)
          }
        }),
    )
  })
  onUnmounted(() => {
    disposed = true
    figureLoads.clear()
    Object.values(imageUrls.value).forEach((url) => URL.revokeObjectURL(url))
    imageUrls.value = {}
  })

  return {
    workspaceFiles,
    imageUrls,
    isFileRefreshing,
    downloadLoading,
    exportLoading,
    primaryArtifact,
    paperAvailable,
    paperPath,
    downloadLabel,
    figureFiles,
    loadWorkspaceFiles,
    downloadArtifact,
    exportWorkspace,
  }
}
