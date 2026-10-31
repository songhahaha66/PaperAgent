import { ref, computed, onUnmounted, watch, nextTick, type Ref } from 'vue'
import type { ResearchSnapshot } from '@/types/research'
import { useBreakpoint } from '@/composables/useBreakpoint'
import {
  useWorkPanelsStore,
  type WorkPanel,
  type PanelGroup,
  type PanelKind,
  type WorkObject,
} from '@/stores/workPanels'
import { isImageFile, paperLabel } from '@/utils/workFiles'
import { useStudioCopy } from '@/lang/studio'

export function useWorkLayout(workId: Ref<string>, snapshot?: Ref<ResearchSnapshot | null>) {
  const { ui, isZh } = useStudioCopy()
  const panelStore = useWorkPanelsStore()
  const panelSession = computed(() => panelStore.session(workId.value))
  const isPanelVisible = (id: string) =>
    panelSession.value.activeId === id || panelSession.value.secondaryActiveId === id
  let disposed = false
  onUnmounted(() => {
    disposed = true
  })
  const { isMobile, width } = useBreakpoint()

  // 侧边栏折叠状态 - 手机端默认收起
  const isSidebarCollapsed = ref(isMobile.value)
  // Establish the initial workspace once; subsequent user tab arrangements survive.
  const fresh = !panelStore.sessions[workId.value]
  const initial = panelSession.value
  if (fresh) {
    initial.panels.push({
      id: 'paper',
      kind: 'paper',
      group: isMobile.value ? 'main' : 'secondary',
    })
    if (!isMobile.value) initial.secondaryActiveId = 'paper'
    initial.focusedGroup = 'main'
    initial.splitRatio = 0.45
  }
  const stackedPanes = computed(
    () => width.value - (isMobile.value ? 0 : isSidebarCollapsed.value ? 64 : 248) < 760,
  )
  watch(isMobile, (mobile) => {
    if (mobile) isSidebarCollapsed.value = true
  })

  const inputValue = computed({
    get: () => panelSession.value.draft,
    set: (value) => {
      panelSession.value.draft = value
    },
  })
  const figureScroller = ref<HTMLElement | null>(null)
  const atBottom = ref(panelSession.value.followChat)
  const figuresVisible = computed(() => isPanelVisible('figures'))
  const displayPanels = computed(() =>
    panelSession.value.panels.map((panel) => ({ ...panel, ...panelPresentation(panel) })),
  )
  function panelPresentation(panel: Pick<WorkPanel, 'kind' | 'path' | 'resultId' | 'resultType'>) {
    if (panel.kind === 'result')
      return {
        label:
          snapshot?.value?.results.find((r) => r.id === panel.resultId)?.name ||
          snapshot?.value?.experiments?.find(
            (e) => e.id === panel.resultId || e.results.some((r) => r.id === panel.resultId),
          )?.title ||
          (panel.resultType === 'comparison'
            ? isZh.value
              ? '方法比较'
              : 'Comparison'
            : isZh.value
              ? '实验记录'
              : 'Experiment'),
        icon: 'chart-line',
      }
    if (panel.kind === 'file')
      return {
        label: paperLabel(panel.path!, isZh.value) || panel.path!.split('/').pop()!,
        icon:
          panel.path!.endsWith('.py') || panel.path!.endsWith('.tex')
            ? 'code'
            : isImageFile(panel.path!)
              ? 'image'
              : 'file-1',
      }
    return {
      chat: { label: ui.value.conversation, icon: 'chat' },
      paper: { label: isZh.value ? '论文' : 'Paper', icon: 'file-1' },
      figures: { label: ui.value.figures, icon: 'chart-line' },
      files: { label: ui.value.files, icon: 'folder-open' },
    }[panel.kind]
  }
  const panelOptions = computed(() =>
    (['paper', 'figures', 'files'] as const).map((kind) => ({
      content: panelPresentation({ kind }).label,
      value: kind,
    })),
  )
  function openPanel(kind: string, group: PanelGroup) {
    panelStore.open(workId.value, kind as PanelKind, undefined, group)
  }
  function rememberPanelScroll(id: string, event: Event) {
    const target = event.target as HTMLElement
    if (id === 'files' && !target.classList.contains('fm-body')) return
    if (isPanelVisible(id)) panelSession.value.scroll[id] = target.scrollTop
  }
  function openFiles() {
    panelStore.open(workId.value, 'files')
  }
  function handleMainPaperClick() {
    panelStore.open(workId.value, 'paper', undefined, isMobile.value ? 'main' : 'secondary')
  }
  function openObject(object: WorkObject) {
    panelStore.openObject(workId.value, object, isMobile.value ? 'main' : 'secondary')
  }
  function handleWorkspaceFileSelect(path: string) {
    openObject({ type: 'file', path })
  }
  function trackChatScroll(event: Event) {
    const target = event.target as HTMLElement
    if (!target.classList.contains('chat-messages') || disposed || !isPanelVisible('chat')) return
    atBottom.value = target.scrollHeight - target.scrollTop - target.clientHeight < 100
    panelSession.value.followChat = atBottom.value
    panelSession.value.scroll.chat = target.scrollTop
  }
  function jumpToLatest() {
    atBottom.value = true
    panelSession.value.followChat = true
    scrollToBottom(true)
  }
  async function restorePanelScroll() {
    await nextTick()
    if (disposed) return
    if (isPanelVisible('chat')) {
      const scroller = document.querySelector<HTMLElement>('.chat-messages')
      if (scroller)
        scroller.scrollTop = panelSession.value.followChat
          ? scroller.scrollHeight
          : panelSession.value.scroll.chat || 0
    }
    if (isPanelVisible('figures') && figureScroller.value)
      figureScroller.value.scrollTop = panelSession.value.scroll.figures || 0
    if (isPanelVisible('files')) {
      const scroller = document.querySelector<HTMLElement>('.files-section .fm-body')
      if (scroller) scroller.scrollTop = panelSession.value.scroll.files || 0
    }
  }
  // 自动滚动到底部
  const scrollToBottom = (force = false) => {
    if (!force && !atBottom.value) return
    nextTick(() => {
      const chatContainer = document.querySelector('.chat-messages')
      if (chatContainer && !disposed && isPanelVisible('chat')) {
        chatContainer.scrollTop = chatContainer.scrollHeight
      }
    })
  }

  // 切换侧边栏折叠状态
  const toggleSidebar = () => {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }

  watch(
    () => [panelSession.value.activeId, panelSession.value.secondaryActiveId],
    restorePanelScroll,
  )
  function activateChat() {
    panelStore.activate(workId.value, 'chat')
  }

  return {
    panelStore,
    panelSession,
    isMobile,
    stackedPanes,
    isSidebarCollapsed,
    toggleSidebar,
    inputValue,
    atBottom,
    figuresVisible,
    figureScroller,
    displayPanels,
    panelOptions,
    openObject,
    openPanel,
    rememberPanelScroll,
    openFiles,
    handleMainPaperClick,
    handleWorkspaceFileSelect,
    trackChatScroll,
    jumpToLatest,
    restorePanelScroll,
    scrollToBottom,
    activateChat,
  }
}
