import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { workspaceAPI, type Work } from '@/api/workspace'
import { useAuthStore } from '@/stores/auth'

const CACHE_TTL = 30_000
const HISTORY_LIMIT = 10

// Retain the list across route remounts. Revalidation never hides the current rows.
export const useWorkHistoryStore = defineStore('work-history', () => {
  const auth = useAuthStore()
  const works = ref<Work[]>([])
  const loading = ref(false)
  const hasLoaded = ref(false)
  let loadedAt = 0
  let generation = 0
  let revision = 0
  let inFlight: Promise<void> | null = null
  const changes = new Map<string, { revision: number; value: Partial<Work> | null }>()

  function clear() {
    generation++
    works.value = []
    loading.value = false
    hasLoaded.value = false
    loadedAt = 0
    inFlight = null
    changes.clear()
  }

  watch(() => (auth.token ? auth.currentUser?.id : null), clear, { flush: 'sync' })

  async function ensureLoaded(force = false): Promise<void> {
    const token = auth.token
    const owner = auth.currentUser?.id
    if (!token || owner == null) return
    if (inFlight) return inFlight
    if (!force && hasLoaded.value && Date.now() - loadedAt < CACHE_TTL) return

    const requestGeneration = generation
    const requestRevision = revision
    loading.value = true
    inFlight = (async () => {
      try {
        const response = await workspaceAPI.getWorks(token, 0, HISTORY_LIMIT)
        if (generation !== requestGeneration || auth.currentUser?.id !== owner || !auth.token)
          return
        let next = response.works
        // An older response must not undo a live title/status change or a deletion.
        for (const [workId, change] of changes) {
          if (change.revision <= requestRevision) continue
          if (change.value === null) next = next.filter((work) => work.work_id !== workId)
          else {
            const index = next.findIndex((work) => work.work_id === workId)
            if (index >= 0) next[index] = { ...next[index]!, ...change.value }
            else if (change.value.id != null && change.value.work_id)
              next.unshift(change.value as Work)
          }
        }
        works.value = next.slice(0, HISTORY_LIMIT)
        loadedAt = Date.now()
      } catch (error) {
        // Keep already displayed history when a background refresh fails.
        if (generation === requestGeneration) console.error('Could not refresh work history', error)
      } finally {
        if (generation === requestGeneration) {
          hasLoaded.value = true
          loading.value = false
          inFlight = null
        }
      }
    })()
    return inFlight
  }

  function patch(workId: string, value: Partial<Work>) {
    if (!auth.token) return
    const previous = changes.get(workId)?.value
    changes.set(workId, { revision: ++revision, value: { ...previous, ...value } })
    const work = works.value.find((work) => work.work_id === workId)
    if (work) Object.assign(work, value)
  }

  function upsert(work: Work) {
    if (!auth.token) return
    changes.set(work.work_id, { revision: ++revision, value: { ...work } })
    const index = works.value.findIndex((item) => item.work_id === work.work_id)
    if (index >= 0) works.value[index] = work
    else works.value = [work, ...works.value].slice(0, HISTORY_LIMIT)
  }

  function remove(workId: string) {
    changes.set(workId, { revision: ++revision, value: null })
    works.value = works.value.filter((work) => work.work_id !== workId)
  }

  return { works, loading, hasLoaded, ensureLoaded, patch, upsert, remove }
})
