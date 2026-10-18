import { ref, computed, onUnmounted, watch, nextTick, type Ref } from 'vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import {
  useWorkPanelsStore,
  type WorkPanel,
  type PanelGroup,
  type PanelKind,
} from '@/stores/workPanels'
import { isImageFile, paperLabel } from '@/utils/workFiles'
import { useStudioCopy } from '@/lang/studio'

export function useWorkLayout(workId: Ref<string>) {
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
  const initial = panelSession.value
  if (!isMobile.value && initial.panels.length === 1) {
    panelStore.open(workId.value, 'paper', undefined, 'secondary')
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
  function panelPresentation(panel: Pick<WorkPanel, 'kind' | 'path'>) {
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
      skills: { label: isZh.value ? '科研技能' : 'Research skills', icon: 'books' },
      progress: { label: isZh.value ? '任务进度' : 'Progress', icon: 'task' },
    }[panel.kind]
  }
  const panelOptions = computed(() =>
    (['progress', 'paper', 'skills', 'figures', 'files'] as const).map((kind) => ({
      content: panelPresentation({ kind }).label,
      value: kind,
    })),
  )
  function openProgress() {
    const state = panelSession.value
    const side = !stackedPanes.value
    const hadSecondary = !!state.secondaryActiveId
    panelStore.open(workId.value, 'progress', undefined, side ? 'secondary' : 'main')
    if (side && !hadSecondary) {
      state.splitAxis = 'horizontal'
      state.secondaryFirst = false
      state.splitRatio = 0.64
    }
  }
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
  function handleWorkspaceFileSelect(path: string) {
    if (path === 'paper.md') handleMainPaperClick()
    else panelStore.open(workId.value, 'file', path, isMobile.value ? 'main' : 'secondary')
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
    if (isPanelVisible('progress')) {
      const scroller = document.querySelector<HTMLElement>('.progress-panel')
      if (scroller) scroller.scrollTop = panelSession.value.scroll.progress || 0
    }
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
    openProgress,
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
