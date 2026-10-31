import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'

export type PanelKind = 'chat' | 'paper' | 'figures' | 'files' | 'file' | 'result'
export type PanelGroup = 'main' | 'secondary'
export type SplitEdge = 'left' | 'right' | 'top' | 'bottom'
export interface WorkPanel {
  id: string
  kind: PanelKind
  group: PanelGroup
  path?: string
  resultType?: 'comparison' | 'registered' | 'experiment'
  resultId?: string
}
export interface WorkPanelSession {
  panels: WorkPanel[]
  activeId: string
  secondaryActiveId: string | null
  focusedGroup: PanelGroup
  splitRatio: number
  splitAxis: 'horizontal' | 'vertical'
  secondaryFirst: boolean
  draft: string
  scroll: Record<string, number>
  followChat: boolean
}

export type WorkObject =
  | { type: 'file'; path: string }
  | { type: 'paper' }
  | { type: 'result'; resultType: 'comparison' | 'registered' | 'experiment'; id: string }

// Tab-scoped, per-user UI state survives refresh; no credentials are stored here.
export const useWorkPanelsStore = defineStore('work-panels', () => {
  const auth = useAuthStore()
  const sessions = ref<Record<string, WorkPanelSession>>({})
  watch(
    () => (auth.token ? auth.currentUser?.id : null),
    () => {
      sessions.value = {}
    },
    { flush: 'sync' },
  )
  const storageKey = () => `paperagent:workspace:v4:${auth.currentUser?.id || 'anonymous'}`
  try {
    if (auth.token && typeof sessionStorage !== 'undefined') {
      const saved = sessionStorage.getItem(storageKey())
      const cached = JSON.parse(
        saved || sessionStorage.getItem(storageKey().replace(':v4:', ':')) || '{}',
      )
      if (cached && typeof cached === 'object' && !Array.isArray(cached)) {
        if (!saved)
          for (const session of Object.values(cached) as WorkPanelSession[]) {
            for (const panel of session.panels) {
              const old = panel.resultType as string
              if (panel.kind === 'result' && (old === 'task' || old === 'experiment')) {
                panel.resultType = old === 'task' ? 'experiment' : 'comparison'
                const id = `result:${panel.resultType}:${panel.resultId}`
                if (session.activeId === panel.id) session.activeId = id
                if (session.secondaryActiveId === panel.id) session.secondaryActiveId = id
                panel.id = id
              }
            }
          }
        sessions.value = cached
      }
    }
  } catch {
    /* Storage may be unavailable or from an older browser session. */
  }
  watch(
    sessions,
    (value) => {
      try {
        if (auth.token && typeof sessionStorage !== 'undefined')
          sessionStorage.setItem(storageKey(), JSON.stringify(value))
      } catch {
        /* Keep the live session usable when storage is full. */
      }
    },
    { deep: true, flush: 'sync' },
  )
  function session(workId: string): WorkPanelSession {
    if (!sessions.value[workId])
      sessions.value[workId] = {
        panels: [{ id: 'chat', kind: 'chat', group: 'main' }],
        activeId: 'chat',
        secondaryActiveId: null,
        focusedGroup: 'main',
        splitRatio: 0.5,
        splitAxis: 'horizontal',
        secondaryFirst: false,
        draft: '',
        scroll: {},
        followChat: true,
      }
    const state = sessions.value[workId]!
    const allowed = new Set(['chat', 'paper', 'figures', 'files', 'file', 'result'])
    const seen = new Set<string>()
    const panels = state.panels.filter((panel) => {
      if (!allowed.has(panel.kind) || seen.has(panel.id)) return false
      seen.add(panel.id)
      return true
    })
    if (panels.length !== state.panels.length) {
      state.panels = panels
      for (const id of Object.keys(state.scroll)) {
        if (!panels.some((p) => p.id === id)) delete state.scroll[id]
      }
      if (!panels.some((p) => p.id === state.activeId && p.group === 'main'))
        state.activeId = panels.find((p) => p.group === 'main')?.id || 'chat'
      if (!panels.some((p) => p.id === state.secondaryActiveId && p.group === 'secondary'))
        state.secondaryActiveId = panels.find((p) => p.group === 'secondary')?.id || null
      if (!state.secondaryActiveId) state.focusedGroup = 'main'
    }
    return state
  }
  function activate(workId: string, id: string) {
    const state = session(workId),
      panel = state.panels.find((panel) => panel.id === id)
    if (!panel) return
    if (panel.group === 'main') state.activeId = id
    else state.secondaryActiveId = id
    state.focusedGroup = panel.group
  }
  function open(workId: string, kind: PanelKind, path?: string, group?: PanelGroup) {
    if (!['chat', 'paper', 'figures', 'files', 'file', 'result'].includes(kind)) return
    if (kind === 'file') {
      path = path?.replace(/^\.\//, '').replace(/\\/g, '/')
      if (path === 'paper.md') {
        kind = 'paper'
        path = undefined
      }
      if (!path && kind === 'file') return
    }
    const state = session(workId),
      id = kind === 'file' ? `file:${path}` : kind
    const existing = state.panels.find((panel) => panel.id === id)
    if (!existing)
      state.panels.push({
        id,
        kind,
        group: kind === 'chat' ? 'main' : group || state.focusedGroup,
        ...(path ? { path } : {}),
      })
    else if (group && existing.group !== group) move(workId, id, group)
    activate(workId, id)
  }
  function openObject(workId: string, object: WorkObject, group?: PanelGroup) {
    if (object.type !== 'result') {
      open(workId, object.type, object.type === 'file' ? object.path : undefined, group)
      return
    }
    const state = session(workId),
      id = `result:${object.resultType}:${object.id}`
    if (!state.panels.some((p) => p.id === id))
      state.panels.push({
        id,
        kind: 'result',
        group: group || state.focusedGroup,
        resultType: object.resultType,
        resultId: object.id,
      })
    activate(workId, id)
  }
  function close(workId: string, id: string) {
    if (id === 'chat') return
    const state = session(workId),
      panel = state.panels.find((panel) => panel.id === id)
    if (!panel) return
    const groupPanels = state.panels.filter((item) => item.group === panel.group),
      index = groupPanels.indexOf(panel)
    state.panels.splice(state.panels.indexOf(panel), 1)
    delete state.scroll[id]
    const remaining = state.panels.filter((item) => item.group === panel.group)
    const next = remaining[Math.max(0, index - 1)] || remaining[0]
    if (panel.group === 'main' && state.activeId === id) state.activeId = next?.id || 'chat'
    if (panel.group === 'secondary' && state.secondaryActiveId === id)
      state.secondaryActiveId = next?.id || null
    if (!state.secondaryActiveId) state.focusedGroup = 'main'
  }
  function move(workId: string, id: string, group: PanelGroup, beforeId?: string) {
    if (id === 'chat' || id === beforeId) return
    const state = session(workId),
      panel = state.panels.find((panel) => panel.id === id)
    if (!panel) return
    const previousGroup = panel.group
    const wasActive =
      previousGroup === 'main' ? state.activeId === id : state.secondaryActiveId === id
    state.panels.splice(state.panels.indexOf(panel), 1)
    if (wasActive) {
      const neighbor = state.panels.find((item) => item.group === previousGroup)
      if (previousGroup === 'main') state.activeId = neighbor?.id || 'chat'
      else state.secondaryActiveId = neighbor?.id || null
    }
    panel.group = group
    const target = beforeId
      ? state.panels.findIndex((item) => item.id === beforeId && item.group === group)
      : -1
    state.panels.splice(target < 0 ? state.panels.length : Math.max(1, target), 0, panel)
    activate(workId, id)
  }
  function split(workId: string, id: string, edge: SplitEdge) {
    const state = session(workId)
    if (id === 'chat' || !state.panels.some((panel) => panel.id === id)) return
    state.splitAxis = edge === 'top' || edge === 'bottom' ? 'vertical' : 'horizontal'
    state.secondaryFirst = edge === 'left' || edge === 'top'
    move(workId, id, 'secondary')
  }
  function remove(workId: string) {
    delete sessions.value[workId]
  }
  return { sessions, session, open, openObject, activate, close, move, split, remove }
})
