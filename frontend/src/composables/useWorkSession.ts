import { ref, computed, onUnmounted, type Ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useWorkHistoryStore } from '@/stores/workHistory'
import { workspaceAPI, type Work } from '@/api/workspace'
import { chatAPI } from '@/api/chat'
import type { ResearchAction, ResearchSnapshot } from '@/types/research'
import { useStudioCopy } from '@/lang/studio'

export function useWorkSession(workId: Ref<string>) {
  const authStore = useAuthStore()
  const historyStore = useWorkHistoryStore()
  const { isZh } = useStudioCopy()
  let disposed = false
  onUnmounted(() => {
    disposed = true
  })
  // 当前工作信息
  const currentWork = ref<Work | null>(null)

  // 加载状态
  const loading = ref(false)
  const loadError = ref<'forbidden' | 'missing' | 'failed' | null>(null)
  const loadErrorText = computed(() => {
    if (loadError.value === 'forbidden')
      return isZh.value ? '当前账号无权查看这项研究' : 'Your account cannot access this research'
    if (loadError.value === 'missing')
      return isZh.value ? '这项研究不存在' : 'This research does not exist'
    return isZh.value ? '研究加载失败，请稍后重试' : 'Research could not be loaded. Please retry.'
  })

  const researchSnapshot = ref<ResearchSnapshot | null>(null)
  let evidenceRequest = 0
  function applyResearchSnapshot(snapshot: ResearchSnapshot) {
    if (disposed || snapshot.work_id !== workId.value) return
    const previous = researchSnapshot.value
    if (
      previous?.research_id === snapshot.research_id &&
      (snapshot.commit_seq || 0) < (previous?.commit_seq || 0)
    )
      return
    researchSnapshot.value = snapshot
    applyWorkStatus(snapshot.work_status)
  }
  async function loadResearchSnapshot() {
    if (disposed) return
    const request = ++evidenceRequest
    try {
      const snapshot = await chatAPI.getResearchSnapshot(workId.value)
      if (request === evidenceRequest) applyResearchSnapshot(snapshot)
    } catch {
      // Do not blank existing evidence on transient read/network failures.
    }
  }
  // 统一的流式消息处理函数
  // 后端在开始、完成、失败时带上工作状态，页头和侧边栏随之更新
  const applyWorkStatus = (status?: string) => {
    if (disposed || !status || !currentWork.value || currentWork.value.status === status) return
    currentWork.value.status = status
    window.dispatchEvent(
      new CustomEvent('work-status-updated', { detail: { workId: workId.value, status } }),
    )
  }

  // 加载工作信息
  const loadWork = async (dependencies: {
    loadFiles: () => Promise<void>
    loadHistory: () => Promise<void>
    resume: () => Promise<void>
    sendMessage: (content: string, action: ResearchAction) => Promise<void>
  }) => {
    if (disposed || loading.value || !workId.value || !authStore.token) return

    loading.value = true
    loadError.value = null
    try {
      const work = await workspaceAPI.getWork(authStore.token, workId.value)
      if (disposed) return
      currentWork.value = work
      historyStore.upsert(work)

      // 加载工作空间文件
      await Promise.all([dependencies.loadFiles(), loadResearchSnapshot()])
      if (disposed) return
      await dependencies.loadHistory()
      if (disposed) return
      await dependencies.resume()
      if (!disposed) await checkAndAutoSendFirstMessage(dependencies.sendMessage)
    } catch (error) {
      console.error('加载工作信息失败:', error)
      if (!disposed) {
        if (!currentWork.value) {
          const status = (error as { response?: { status?: number } })?.response?.status
          loadError.value = status === 403 ? 'forbidden' : status === 404 ? 'missing' : 'failed'
        } else MessagePlugin.error('加载工作信息失败')
      }
    } finally {
      loading.value = false
    }
  }

  // 检查并自动发送第一句话
  const checkAndAutoSendFirstMessage = async (
    sendMessage: (content: string, action: ResearchAction) => Promise<void>,
  ) => {
    const pendingKey = `pendingQuestion:${workId.value}`
    const pendingQuestion =
      localStorage.getItem(pendingKey) || localStorage.getItem('pendingQuestion')
    if (pendingQuestion && currentWork.value?.title?.trim() === '') {
      localStorage.removeItem(pendingKey)
      localStorage.removeItem('pendingQuestion')
      try {
        generateWorkTitle(pendingQuestion).catch((err: Error) =>
          console.error('后台生成标题失败:', err),
        )
        await sendMessage(pendingQuestion, 'run')
      } catch (error) {
        console.error('自动发送第一句话失败:', error)
      }
    }
  }

  const generateWorkTitle = async (question: string) => {
    try {
      // 调用标题生成API，会自动更新数据库
      const response = await chatAPI.generateTitle(authStore.token!, workId.value!, question)

      if (disposed) return
      // 更新本地状态
      if (currentWork.value) {
        currentWork.value.title = response.title
      }

      // 通知侧边栏刷新工作列表
      // 通过触发一个自定义事件来通知父组件或侧边栏
      window.dispatchEvent(
        new CustomEvent('work-title-updated', {
          detail: {
            workId: workId.value,
            newTitle: response.title,
          },
        }),
      )

      console.log('标题已更新:', response.title)
    } catch (error) {
      console.error('生成标题失败:', error)
    }
  }

  return {
    workId,
    currentWork,
    loading,
    loadError,
    loadErrorText,
    researchSnapshot,
    loadWork,
    loadResearchSnapshot,
    applyResearchSnapshot,
    applyWorkStatus,
    isDisposed: () => disposed,
    invalidateSnapshot: () => {
      ++evidenceRequest
    },
  }
}
